"""Workflow: answer a Slack wake-up message with a chat model and reply.

Triggered by an event webhook (e.g. a direct message to the Slack app) or
called directly. Two input shapes are supported:

1. Webhook envelope: Mistral delivers the raw HTTP request it received:
   {"method": "...", "headers": {...}, "query_params": {},
    "body": {...Slack event_callback payload...}}. The workflow extracts the
   message text, the channel it was posted in, the author's user id, and an
   optional audio attachment (voice note).
2. Direct invocation: {"user_text": "...", "reply_channel": "..."} for manual
   runs and future scheduled sweeps.

The answer is generated with the configured chat model (get_mistral_client()
service-account token) and posted back with the Slack app behind
SLACK_BOT_TOKEN. If that app cannot post into the source channel (for example
a direct message the app is not part of), the workflow falls back to opening
the app's own direct-message channel with the author and answering there.

Voice notes are mirrored: the incoming audio is transcribed with the
configured Voxtral STT model, the transcript is answered by the chat model,
and the answer is spoken back as a Slack voice message (TTS). The spoken reply
uses VOXTRAL_VOICE_ID when configured, otherwise the incoming voice note is
used as an inline reference sample ("clone on the fly", which requires the
sample speaker's consent per the Mistral voice-cloning usage policy). If
speech synthesis fails, the workflow degrades to a text reply instead of
failing the whole round.

Errors (AssistantError, SlackApiError, TTSError, ConfigError, WakeInputError)
intentionally propagate: the workflow engine records them on the execution.
Transcription errors do NOT degrade; a voice request whose audio cannot be
read or transcribed fails visibly.
Sandbox note: httpx-based and SDK client imports stay inside activity bodies.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

try:
    import mistralai.workflows as workflows
except ImportError:  # pragma: no cover - SDK module layout varies across versions
    import mistralai_workflows as workflows  # type: ignore[no-redef]

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

WAKE_REPLY_HEADER = ":sun: *Wake reply*"
WAKE_VOICE_INITIAL_COMMENT = ":loud_sound: *Wake voice reply*:"
VOICE_REPLY_FILENAME = "wake-voice-reply.mp3"


class WakeInputError(ValueError):
    """Raised when the input cannot be resolved into a task."""


class WakeInput(BaseModel):
    """Workflow input - see module docstring."""

    user_text: str = Field(
        default="", description="Direct invocation: the task text to answer"
    )
    reply_channel: str = Field(
        default="", description="Direct invocation: channel to post the answer to"
    )
    voice_file_id: str = Field(
        default="",
        description="Direct invocation: Slack file id of an incoming voice note",
    )
    method: str = Field(default="", description="Webhook envelope: HTTP method")
    headers: dict[str, str] | None = Field(
        default=None, description="Webhook envelope: request headers"
    )
    query_params: dict[str, str] | None = Field(
        default=None, description="Webhook envelope: query parameters"
    )
    body: dict[str, Any] | None = Field(
        default=None, description="Webhook envelope: the parsed Slack event payload"
    )
    raw_body: str = Field(default="", description="Webhook envelope: the raw request body")


def _first_audio_file_id(files: Any) -> str:
    """Return the id of the first audio attachment in a Slack message event."""
    if not isinstance(files, list):
        return ""
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("mimetype", "")).lower().startswith("audio/"):
            file_id = str(entry.get("id", "")).strip()
            if file_id:
                return file_id
    return ""


def extract_message(body: dict[str, Any] | None) -> tuple[str, str, str, str]:
    """Extract (text, channel_id, author_user_id, voice_file_id) from an event."""
    if not isinstance(body, dict):
        raise WakeInputError("Input has no Slack event body")
    event = body.get("event")
    if not isinstance(event, dict):
        raise WakeInputError("Slack event body has no event object")
    text = str(event.get("text", "")).strip()
    channel = str(event.get("channel", "")).strip()
    author = str(event.get("user", "")).strip()
    voice_file_id = _first_audio_file_id(event.get("files"))
    if not text and not voice_file_id:
        raise WakeInputError("Slack event carries neither message text nor an audio file")
    if not channel:
        raise WakeInputError("Slack event carries no channel id")
    return text, channel, author, voice_file_id


def resolve_request(input: WakeInput) -> tuple[str, str, str, str]:
    """Normalize the two input shapes into (task_text, reply_channel, author, voice_file_id)."""
    if input.user_text or input.voice_file_id:
        if not input.reply_channel:
            raise WakeInputError("Direct invocation needs a reply_channel")
        return input.user_text, input.reply_channel, "", input.voice_file_id
    text, channel, author, voice_file_id = extract_message(input.body)
    return text, channel, author, voice_file_id


def build_wake_reply(request_text: str, answer: str, header: str = WAKE_REPLY_HEADER) -> str:
    """Format the Slack reply: header, quoted request, then the answer."""
    lines = request_text.splitlines() or [request_text]
    quoted = "\n".join("> " + line for line in lines)
    return header + "\n" + quoted + "\n\n" + answer


async def _transcribe_voice_note(slack: Any, settings: Any, voice_file_id: str) -> str:
    """Download and transcribe the incoming voice note; returns the transcript."""
    from src.clients.transcription import VoxtralTranscriber

    file = await slack.get_file(voice_file_id)
    audio = await slack.download_file(file.download_url)
    logger.info(
        "Transcribing wake-up voice note %s (%d bytes, %s)",
        voice_file_id,
        len(audio),
        file.mimetype,
    )
    transcriber = VoxtralTranscriber(model=settings.voxtral_model)
    return await transcriber.transcribe(audio, filename=file.name)


async def _post_voice_reply(
    slack: Any,
    settings: Any,
    answer_text: str,
    voice_file_id: str,
    channel_id: str,
    author_user_id: str,
) -> None:
    """Speak the answer and post it as a Slack voice message (with DM fallback)."""
    from src.clients.slack import SlackApiError
    from src.clients.tts import VoxtralSpeaker

    voice_id = settings.voxtral_voice_id
    ref_audio_b64 = ""
    if not voice_id:
        # Clone on the fly from the requester's own voice note. This requires
        # the sample speaker's consent (Mistral voice-cloning usage policy).
        sample = await slack.get_file(voice_file_id)
        sample_bytes = await slack.download_file(sample.download_url)
        ref_audio_b64 = base64.b64encode(sample_bytes).decode()
        logger.info(
            "Using incoming voice note %s as the TTS reference sample", voice_file_id
        )
    speaker = VoxtralSpeaker(model=settings.voxtral_tts_model)
    audio = await speaker.synthesize(
        answer_text, voice_id=voice_id, ref_audio_b64=ref_audio_b64
    )
    try:
        await slack.upload_audio(
            channel_id=channel_id,
            audio=audio,
            filename=VOICE_REPLY_FILENAME,
            initial_comment=WAKE_VOICE_INITIAL_COMMENT,
        )
        logger.info("Wake voice reply posted to channel %s", channel_id)
    except SlackApiError as exc:
        if not author_user_id:
            raise
        logger.info(
            "Cannot post voice reply to %s (%s); falling back to a direct message to %s",
            channel_id,
            exc.slack_error,
            author_user_id,
        )
        dm_channel = await slack.open_im(author_user_id)
        await slack.upload_audio(
            channel_id=dm_channel,
            audio=audio,
            filename=VOICE_REPLY_FILENAME,
            initial_comment=WAKE_VOICE_INITIAL_COMMENT,
        )
        logger.info("Wake voice reply posted to author via direct message %s", dm_channel)


async def _post_text_reply(
    slack: Any, reply: str, channel_id: str, author_user_id: str, method: str
) -> None:
    """Post the formatted text reply (source channel first, DM fallback)."""
    from src.clients.slack import SlackApiError

    try:
        await slack.post_message(channel_id=channel_id, text=reply)
        logger.info("Wake reply posted to channel %s", channel_id)
    except SlackApiError as exc:
        if not author_user_id:
            raise
        logger.info(
            "Cannot post to %s (%s); falling back to a direct message to %s",
            channel_id,
            exc.slack_error,
            author_user_id,
        )
        dm_channel = await slack.open_im(author_user_id)
        await slack.post_message(channel_id=dm_channel, text=reply)
        logger.info("Wake reply posted to author via direct message %s", dm_channel)


@workflows.activity()
async def process_wake_request(
    user_text: str,
    reply_channel: str,
    author_user_id: str = "",
    voice_file_id: str = "",
) -> str:
    """Answer the wake-up request and post the reply; returns the answer text."""
    from src.clients.assistant import MistralAssistant
    from src.clients.slack import SlackApiError, SlackClient
    from src.clients.tts import TTSError
    from src.config import Settings

    settings = Settings.from_env()
    slack = SlackClient(
        token=settings.slack_bot_token, timeout=settings.http_timeout_seconds
    )
    requested_as_voice = bool(voice_file_id)
    async with slack:
        if requested_as_voice:
            transcript = (await _transcribe_voice_note(slack, settings, voice_file_id) or user_text).strip()
            if not transcript:
                raise WakeInputError("Voice note transcription was empty")
            user_text = transcript

        assistant = MistralAssistant(model=settings.wake_model)
        logger.info("Answering wake-up request with model %s", settings.wake_model)
        answer = await assistant.complete(settings.wake_system_prompt, user_text)

        posted_as_voice = False
        if requested_as_voice:
            try:
                await _post_voice_reply(
                    slack, settings, answer, voice_file_id, reply_channel, author_user_id
                )
                posted_as_voice = True
            except TTSError as exc:
                # Speech synthesis is unavailable: degrade to a text reply
                # rather than failing the whole round.
                logger.warning(
                    "Voice reply failed (%s); posting a text reply instead", exc
                )
        if not posted_as_voice:
            reply = build_wake_reply(user_text, answer)
            await _post_text_reply(slack, reply, reply_channel, author_user_id, "chat.postMessage")
    return answer


@workflows.workflow.define(
    name="wake",
    workflow_display_name="Wake",
    workflow_description=(
        "Answers a Slack wake-up message (text or voice note) with a chat model "
        "and posts the reply back to the author (webhook envelope or direct "
        "invocation)."
    ),
)
class WakeWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: WakeInput) -> str:
        request = resolve_request(input)
        return await process_wake_request(*request)
