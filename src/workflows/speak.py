"""Workflow: speak a text as a voice message into a Slack channel/thread.

Reverse of slack_voice_transcribe: text -> Voxtral TTS -> audio bytes -> Slack
file upload, shared as a threaded reply.

Input schema:
    text            The text to speak.
    channel_id      Channel the voice message is posted in.
    thread_ts       Optional timestamp of the parent message (thread anchor).
    voice_id        Optional Mistral saved-voice id; falls back to the
                    VOXTRAL_VOICE_ID env var. NOTE: speech generation only
                    accepts voices owned by the caller; a managed
                    deployment's service account cannot own audio
                    resources, so voice_id will 404 unless the voice is
                    usable by the service account.
    sample_file_id   Optional Slack file ID of a voice sample (e.g. a wav/mp3
                    posted in the channel). When no voice_id is configured,
                    the sample is downloaded and passed to Voxtral TTS as
                    inline ref_audio ("clone on the fly"), which needs no
                    voice ownership. One of voice_id / sample_file_id /
                    VOXTRAL_VOICE_ID must be set.

Errors (SlackApiError, TTSError, ConfigError) intentionally propagate; the
workflow engine records them on the execution.

Sandbox note: httpx-based client imports stay inside activity bodies (see
slack_voice_transcribe.py). Voice cloning requires the sample speaker's
explicit consent (Mistral voice-cloning usage policy); the caller is
responsible for ensuring consent before using a sample.
"""

from __future__ import annotations

import base64
import logging

try:
    import mistralai.workflows as workflows
except ImportError:  # pragma: no cover - SDK module layout varies across versions
    import mistralai_workflows as workflows  # type: ignore[no-redef]

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_VOICE_FILENAME = "voice-reply.mp3"


class SpeakInput(BaseModel):
    """Workflow input - see module docstring."""

    text: str = Field(description="Text to synthesize and speak")
    channel_id: str = Field(description="Slack channel ID to post into")
    thread_ts: str = Field(
        default="", description="Timestamp of the parent message (thread anchor)"
    )
    voice_id: str = Field(
        default="",
        description="Mistral saved-voice id; falls back to VOXTRAL_VOICE_ID env",
    )
    sample_file_id: str = Field(
        default="",
        description=(
            "Slack file ID of a voice sample; used as inline ref_audio when no"
            " voice_id is configured (clone on the fly)"
        ),
    )


@workflows.activity()
async def speak_to_slack(
    text: str,
    channel_id: str,
    thread_ts: str = "",
    voice_id: str = "",
    sample_file_id: str = "",
) -> str:
    """Synthesize text to audio and post it as a Slack voice message."""
    from src.clients.slack import SlackClient
    from src.clients.tts import TTSError, VoxtralSpeaker
    from src.config import Settings

    settings = Settings.from_env()
    effective_voice_id = voice_id or settings.voxtral_voice_id

    ref_audio_b64 = ""
    if not effective_voice_id:
        if not sample_file_id:
            raise TTSError(
                "No voice configured: pass voice_id, sample_file_id (Slack voice"
                " sample) or set VOXTRAL_VOICE_ID."
            )
        async with SlackClient(
            token=settings.slack_bot_token, timeout=settings.http_timeout_seconds
        ) as slack:
            sample = await slack.get_file(sample_file_id)
            sample_bytes = await slack.download_file(sample.download_url)
        ref_audio_b64 = base64.b64encode(sample_bytes).decode()
        logger.info(
            "Using inline ref_audio from Slack sample %s (%d bytes)",
            sample_file_id,
            len(sample_bytes),
        )

    speaker = VoxtralSpeaker()
    logger.info(
        "Synthesizing %d characters of text (voice_id=%s, ref_audio=%s)",
        len(text),
        effective_voice_id or "-",
        "yes" if ref_audio_b64 else "no",
    )
    audio = await speaker.synthesize(
        text, voice_id=effective_voice_id, ref_audio_b64=ref_audio_b64
    )

    slack = SlackClient(
        token=settings.slack_bot_token, timeout=settings.http_timeout_seconds
    )
    async with slack:
        file_id = await slack.upload_audio(
            channel_id=channel_id,
            audio=audio,
            filename=DEFAULT_VOICE_FILENAME,
            thread_ts=thread_ts or None,
            initial_comment=settings.speak_initial_comment,
        )
        logger.info("Voice reply uploaded to channel %s (file %s)", channel_id, file_id)
    return file_id


@workflows.workflow.define(
    name="slack-voice-speak",
    workflow_display_name="Slack Voice Speak",
    workflow_description="Synthesizes text with Voxtral TTS and posts it as a Slack voice message (threaded reply).",
)
class SpeakWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: SpeakInput) -> str:
        return await speak_to_slack(
            text=input.text,
            channel_id=input.channel_id,
            thread_ts=input.thread_ts,
            voice_id=input.voice_id,
            sample_file_id=input.sample_file_id,
        )
