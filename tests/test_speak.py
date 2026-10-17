"""Tests for the slack-voice-speak workflow orchestration (Slack + speaker faked)."""

import base64

import pytest

from src.workflows import speak as wf


class _RecordingFakeSlack:
    uploaded: list[tuple[str, bytes, str, str | None, str | None]] = []
    downloaded: list[str] = []

    def __init__(self, token: str, timeout: float = 120.0, transport=None) -> None:
        self._token = token

    async def __aenter__(self) -> "_RecordingFakeSlack":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get_file(self, file_id):
        class _File:
            name = "sample.wav"
            download_url = "https://slack.example/" + file_id
        return _File()

    async def download_file(self, download_url):
        return b"sample-bytes"

    async def upload_audio(self, channel_id, audio, filename, thread_ts=None, initial_comment=None):
        _RecordingFakeSlack.uploaded.append((channel_id, audio, filename, thread_ts, initial_comment))
        return "F999"


class _FakeSpeaker:
    def __init__(self, model: str = "m", client_factory=None) -> None:
        pass

    async def synthesize(
        self, text: str, voice_id: str = "", ref_audio_b64: str = "", response_format: str = "mp3"
    ) -> bytes:
        return b"synth-" + text.encode()


async def _run_activity(monkeypatch, **kwargs) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "token")
    monkeypatch.setattr("src.clients.slack.SlackClient", _RecordingFakeSlack)
    monkeypatch.setattr("src.clients.tts.VoxtralSpeaker", _FakeSpeaker)
    _RecordingFakeSlack.uploaded.clear()
    await wf.speak_to_slack(**kwargs)


async def test_activity_uploads_synth_audio_as_thread_reply(monkeypatch) -> None:
    result = await _run_activity(
        monkeypatch, text="Hello", channel_id="C1", thread_ts="123.456", voice_id="v-1"
    )
    assert result == "F999"
    assert _RecordingFakeSlack.uploaded == [
        ("C1", b"synth-Hello", "voice-reply.mp3", "123.456", ":loud_sound: Voice reply:")
    ]


async def test_activity_falls_back_to_env_voice_id(monkeypatch) -> None:
    monkeypatch.setenv("VOXTRAL_VOICE_ID", "v-env")
    await _run_activity(monkeypatch, text="Hello", channel_id="C1")
    assert _RecordingFakeSlack.uploaded[0][3] is None


async def test_activity_without_any_voice_raises(monkeypatch) -> None:
    from src.clients.tts import TTSError

    with pytest.raises(TTSError, match="No voice configured"):
        await _run_activity(monkeypatch, text="Hello", channel_id="C1")


async def test_activity_downloads_sample_and_clones_inline(monkeypatch) -> None:
    _RecordingFakeSlack.downloaded.clear()
    await _run_activity(
        monkeypatch, text="Hello", channel_id="C1", sample_file_id="F-SAMPLE"
    )
    assert _RecordingFakeSlack.downloaded == ["https://slack.example/F-SAMPLE"]
    assert _RecordingFakeSlack.uploaded[0][1] == b"synth-Hello"
