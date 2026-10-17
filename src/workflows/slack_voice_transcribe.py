"""Workflow: transcribe a Slack voice message and post the transcript as a reply.

Pipeline: files.info -> download audio -> Voxtral transcription -> chat.postMessage
(threaded reply under the original message).

Input schema:
    file_id     Slack file ID of the voice message attachment.
    channel_id  Channel the message was posted in.
    thread_ts   Timestamp of the original message; the transcript is posted as a
                reply in that thread (optional).

Errors (SlackApiError, TranscriptionError, ConfigError) intentionally propagate:
the workflow engine records them on the execution, and the calling agent has a
fallback for failed transcriptions.

Sandbox note: the workflow sandbox forbids importing httpx-based client
modules at module level (they pull in urllib.request). All such imports are
therefore deferred into function bodies - activities run outside the sandbox,
and function-body imports are not executed during workflow definition
validation.

Auth note: the transcriber uses get_mistral_client() (Workflows SDK), which
authenticates via the platform's service-account token - no API key needed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    import mistralai.workflows as workflows
except ImportError:  # pragma: no cover - SDK module layout varies across versions
    import mistralai_workflows as workflows  # type: ignore[no-redef]

from pydantic import BaseModel, Field

from src.config import DEFAULT_TRANSCRIPT_HEADER

if TYPE_CHECKING:  # Only used in annotations; never imported at runtime here.
    from src.clients.slack import SlackFile

logger = logging.getLogger(__name__)

AUDIO_MIME_PREFIX = "audio/"


class SlackVoiceTranscribeInput(BaseModel):
    """Workflow input - see module docstring."""

    file_id: str = Field(description="Slack file ID of the voice message")
    channel_id: str = Field(description="Slack channel ID the message lives in")
    thread_ts: str = Field(
        default="", description="Timestamp of the original message (thread anchor)"
    )


def build_transcript_message(
    transcript: str, header: str = DEFAULT_TRANSCRIPT_HEADER
) -> str:
    """Format the Slack reply that carries the transcript (one quote block)."""
    lines = transcript.splitlines() or [transcript]
    quoted = "\n".join("> " + line for line in lines)
    return header + "\n" + quoted


def ensure_audio_file(file: SlackFile) -> None:
    """Guard: only transcribe files Slack classifies as audio."""
    from src.clients.transcription import TranscriptionError

    if not file.mimetype.startswith(AUDIO_MIME_PREFIX):
        raise TranscriptionError(
            "Not an audio file (mimetype: " + repr(file.mimetype) + ")"
        )


@workflows.activity()
async def transcribe_slack_voice_message(
    file_id: str, channel_id: str, thread_ts: str = ""
) -> str:
    """Run the full pipeline for one voice message; returns the transcript."""
    from src.clients.slack import SlackClient
    from src.clients.transcription import VoxtralTranscriber
    from src.config import Settings

    settings = Settings.from_env()
    slack = SlackClient(
        token=settings.slack_bot_token, timeout=settings.http_timeout_seconds
    )
    transcriber = VoxtralTranscriber(model=settings.voxtral_model)

    async with slack:
        file = await slack.get_file(file_id)
        ensure_audio_file(file)
        logger.info("Downloading Slack file %s (%s)", file.file_id, file.mimetype)
        audio = await slack.download_file(file.download_url)
        logger.info("Transcribing %d bytes with model %s", len(audio), settings.voxtral_model)
        transcript = await transcriber.transcribe(audio, filename=file.name)
        await slack.post_message(
            channel_id=channel_id,
            text=build_transcript_message(transcript, header=settings.transcript_header),
            thread_ts=thread_ts or None,
        )
        logger.info("Transcript posted to channel %s", channel_id)
    return transcript


@workflows.workflow.define(
    name="slack-voice-transcribe",
    workflow_display_name="Slack Voice Transcribe",
    workflow_description="Transcribes a Slack voice message with Voxtral and posts the transcript as a threaded reply.",
)
class SlackVoiceTranscribeWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: SlackVoiceTranscribeInput) -> str:
        return await transcribe_slack_voice_message(
            file_id=input.file_id,
            channel_id=input.channel_id,
            thread_ts=input.thread_ts,
        )
