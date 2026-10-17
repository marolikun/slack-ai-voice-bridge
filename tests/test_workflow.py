"""Tests for the workflow orchestration (Slack + transcriber faked).

The activity imports the Slack client and transcriber inside its function
body (workflow-sandbox requirement), so the fakes are patched on their
source modules, not on the workflow module.
"""

import pytest

from src.clients.slack import SlackFile
from src.config import DEFAULT_TRANSCRIPT_HEADER
from src.workflows import slack_voice_transcribe as wf


def test_build_transcript_message_quotes_each_line() -> None:
    single = wf.build_transcript_message("line one")
    assert single == DEFAULT_TRANSCRIPT_HEADER + "\n> line one"

    multi = wf.build_transcript_message("line one\nline two")
    assert multi == DEFAULT_TRANSCRIPT_HEADER + "\n> line one\n> line two"


def test_build_transcript_message_accepts_custom_header() -> None:
    custom = wf.build_transcript_message("line one", header="Custom header:")
    assert custom == "Custom header:\n> line one"


def test_ensure_audio_accepts_audio_only() -> None:
    from src.clients.transcription import TranscriptionError

    audio_file = SlackFile(
        file_id="F", name="v.mp4", mimetype="audio/mp4", download_url="https://x"
    )
    wf.ensure_audio_file(audio_file)  # no exception expected

    pdf = SlackFile(
        file_id="F", name="doc.pdf", mimetype="application/pdf", download_url="https://x"
    )
    with pytest.raises(TranscriptionError, match="Not an audio file"):
        wf.ensure_audio_file(pdf)


class _RecordingFakeSlack:
    posted: list[tuple[str, str, str | None]] = []

    def __init__(self, token: str, timeout: float = 120.0, transport=None) -> None:
        self._token = token

    async def __aenter__(self) -> "_RecordingFakeSlack":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get_file(self, file_id: str) -> SlackFile:
        return SlackFile(
            file_id=file_id,
            name="voice.mp4",
            mimetype="audio/mp4",
            download_url="https://x",
        )

    async def download_file(self, download_url: str) -> bytes:
        return b"audio-bytes"

    async def post_message(
        self, channel_id: str, text: str, thread_ts: str | None = None
    ) -> None:
        _RecordingFakeSlack.posted.append((channel_id, text, thread_ts))


class _FakeTranscriber:
    def __init__(self, model: str = "m", client_factory=None) -> None:
        pass

    async def transcribe(self, audio: bytes, filename: str = "voice.mp4") -> str:
        return "hello from the test"


async def _run_activity(monkeypatch, **kwargs) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "token")
    # Function-body imports resolve through the source modules at call time.
    monkeypatch.setattr("src.clients.slack.SlackClient", _RecordingFakeSlack)
    monkeypatch.setattr("src.clients.transcription.VoxtralTranscriber", _FakeTranscriber)
    _RecordingFakeSlack.posted.clear()
    await wf.transcribe_slack_voice_message(**kwargs)


async def test_activity_posts_transcript_as_thread_reply(monkeypatch) -> None:
    result = await _run_activity(
        monkeypatch, file_id="F1", channel_id="C1", thread_ts="123.456"
    )

    assert result == "hello from the test"
    assert _RecordingFakeSlack.posted == [
        ("C1", wf.build_transcript_message("hello from the test"), "123.456")
    ]


async def test_activity_posts_without_thread_when_ts_missing(monkeypatch) -> None:
    await _run_activity(monkeypatch, file_id="F1", channel_id="C1")

    assert _RecordingFakeSlack.posted[0][2] is None


async def test_activity_uses_transcript_header_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TRANSCRIPT_HEADER", ":memo: Custom transcript:")
    await _run_activity(monkeypatch, file_id="F1", channel_id="C1")

    assert _RecordingFakeSlack.posted[0][1] == (
        ":memo: Custom transcript:\n> hello from the test"
    )
