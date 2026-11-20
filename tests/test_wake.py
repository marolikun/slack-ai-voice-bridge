"""Tests for the wake workflow: envelope parsing, voice routing, reply
posting, config.

Network-free: the activity imports its collaborators inside the function body,
so tests patch the modules the activity imports from (src.clients.assistant,
src.clients.slack, src.clients.transcription, src.clients.tts) and call the
undecorated activity function, following the repo convention (see AGENTS.md).
"""

from __future__ import annotations

import httpx
import pytest

from src.config import Settings
from src.workflows import wake as wake_module
from src.workflows.wake import (
    WakeInput,
    WakeInputError,
    build_wake_reply,
    extract_message,
    resolve_request,
)

ENVELOPE_BODY = {
    "type": "event_callback",
    "event": {
        "type": "message",
        "channel_type": "im",
        "channel": "D012345ABCDE",
        "user": "U012345ABCD",
        "text": "Weck-Test mit Auftrag",
    },
}

VOICE_ENVELOPE_BODY = {
    "type": "event_callback",
    "event": {
        "type": "message",
        "subtype": "file_share",
        "channel_type": "im",
        "channel": "D012345ABCDE",
        "user": "U012345ABCD",
        "text": "",
        "files": [
            {"id": "F0VOICE123", "mimetype": "audio/mp4"},
            {"id": "F0PIC234", "mimetype": "image/png"},
        ],
    },
}


def _unwrap(fn):
    """Return the raw function behind the SDK activity decorator."""
    return getattr(fn, "__wrapped__", fn)


# --- parsing -----------------------------------------------------------


def test_extract_message_reads_event_fields():
    text, channel, author, voice_file_id = extract_message(ENVELOPE_BODY)
    assert text == "Weck-Test mit Auftrag"
    assert channel == "D012345ABCDE"
    assert author == "U012345ABCD"
    assert voice_file_id == ""


def test_extract_message_picks_first_audio_file():
    text, channel, author, voice_file_id = extract_message(VOICE_ENVELOPE_BODY)
    assert text == ""
    assert voice_file_id == "F0VOICE123"


def test_extract_message_ignores_non_audio_files():
    body = {
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "U1",
            "text": "",
            "files": [{"id": "F1", "mimetype": "image/png"}],
        }
    }
    with pytest.raises(WakeInputError):
        extract_message(body)


@pytest.mark.parametrize(
    "body",
    [None, {}, {"event": {}}, {"event": {"text": "hi"}}, {"event": {"channel": "C1"}}],
)
def test_extract_message_rejects_incomplete_bodies(body):
    with pytest.raises(WakeInputError):
        extract_message(body)


def test_resolve_request_prefers_direct_invocation():
    request = WakeInput(user_text="Auftrag", reply_channel="C0GENERAL")
    assert resolve_request(request) == ("Auftrag", "C0GENERAL", "", "")


def test_resolve_request_direct_call_passes_voice_file_id():
    request = WakeInput(user_text="Auftrag", reply_channel="C0GENERAL", voice_file_id="F0VOICE")
    assert resolve_request(request) == ("Auftrag", "C0GENERAL", "", "F0VOICE")


def test_resolve_request_reads_webhook_envelope():
    request = WakeInput(method="POST", body=ENVELOPE_BODY)
    assert resolve_request(request) == (
        "Weck-Test mit Auftrag",
        "D012345ABCDE",
        "U012345ABCD",
        "",
    )


def test_resolve_request_reads_voice_envelope():
    request = WakeInput(method="POST", body=VOICE_ENVELOPE_BODY)
    assert resolve_request(request) == ("", "D012345ABCDE", "U012345ABCD", "F0VOICE123")


def test_resolve_request_requires_reply_channel_for_direct_call():
    with pytest.raises(WakeInputError):
        resolve_request(WakeInput(user_text="Auftrag"))


def test_build_wake_reply_quotes_request_and_appends_answer():
    reply = build_wake_reply("Frage?", "Antwort.")
    assert reply.startswith(wake_module.WAKE_REPLY_HEADER)
    assert "> Frage?" in reply
    assert reply.endswith("Antwort.")


# --- settings ----------------------------------------------------------


def test_wake_settings_from_env(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("WAKE_MODEL", "mistral-medium-latest")
    settings = Settings.from_env()
    assert settings.wake_model == "mistral-medium-latest"
    assert settings.wake_system_prompt  # non-empty default


def test_wake_settings_defaults(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    settings = Settings.from_env()
    assert settings.wake_model == "mistral-large-latest"
    assert settings.wake_system_prompt  # non-empty default


# --- activity (fake collaborators) --------------------------------------


class FakeAssistant:
    def __init__(self, model=None):
        self.model = model
        self.calls = []

    async def complete(self, system_prompt, user_text):
        self.calls.append((system_prompt, user_text))
        return "Mock-Antwort"


class FakeTranscriber:
    def __init__(self, model=None):
        self.model = model
        self.calls = []

    async def transcribe(self, audio, filename="voice.mp4"):
        self.calls.append((bytes(audio), filename))
        return " Transkript der Sprachnotiz "


class FakeSpeaker:
    def __init__(self, model=None):
        self.model = model
        self.calls = []

    async def synthesize(self, text, voice_id="", ref_audio_b64="", response_format="mp3"):
        self.calls.append((text, voice_id, ref_audio_b64))
        return b"voice-bytes"


class ExplodingSpeaker(FakeSpeaker):
    async def synthesize(self, text, voice_id="", ref_audio_b64="", response_format="mp3"):
        from src.clients.tts import TTSError

        raise TTSError("tts unavailable")


class RecordingSlack:
    def __init__(
        self,
        token,
        timeout=120.0,
        fail_channels=(),
        fail_upload_channels=(),
    ):
        self.fail_channels = set(fail_channels)
        self.fail_upload_channels = set(fail_upload_channels)
        self.posted = []
        self.opened = []
        self.uploads = []
        self.fetched_files = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def post_message(self, channel_id, text, thread_ts=None):
        if channel_id in self.fail_channels:
            from src.clients.slack import SlackApiError

            raise SlackApiError("chat.postMessage", "not_in_channel")
        self.posted.append((channel_id, text))

    async def open_im(self, user_id):
        self.opened.append(user_id)
        return "D0FALLBACK"

    async def get_file(self, file_id):
        from src.clients.slack import SlackFile

        self.fetched_files.append(file_id)
        return SlackFile(
            file_id=file_id,
            name="voice.mp4",
            mimetype="audio/mp4",
            download_url="https://files.example/" + file_id,
        )

    async def download_file(self, url):
        return b"voice-audio"

    async def upload_audio(self, channel_id, audio, filename, thread_ts=None, initial_comment=None):
        if channel_id in self.fail_upload_channels:
            from src.clients.slack import SlackApiError

            raise SlackApiError("files.completeUploadExternal", "not_in_channel")
        self.uploads.append((channel_id, bytes(audio), initial_comment))
        return "FUPLOADED"


# --- activity: text path -------------------------------------------------


async def test_activity_posts_reply_to_source_channel(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t")
    assistant = FakeAssistant()
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: assistant)
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    answer = await _unwrap(wake_module.process_wake_request)(
        user_text="Guten Morgen", reply_channel="C0SOURCE", author_user_id="U1"
    )
    assert answer == "Mock-Antwort"
    assert len(slack.posted) == 1
    channel, text = slack.posted[0]
    assert channel == "C0SOURCE"
    assert "Mock-Antwort" in text
    assert slack.opened == []
    assert slack.uploads == []


async def test_activity_falls_back_to_dm_when_channel_fails(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t", fail_channels={"D0FOREIGN"})
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: FakeAssistant())
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    answer = await _unwrap(wake_module.process_wake_request)(
        user_text="Guten Morgen", reply_channel="D0FOREIGN", author_user_id="U1"
    )
    assert answer == "Mock-Antwort"
    assert slack.opened == ["U1"]
    assert len(slack.posted) == 1
    channel, text = slack.posted[0]
    assert channel == "D0FALLBACK"
    assert text.startswith(wake_module.WAKE_REPLY_HEADER)


async def test_activity_reraises_without_author_on_channel_error(monkeypatch):
    from src.clients.slack import SlackApiError

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t", fail_channels={"D0X"})
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: FakeAssistant())
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    with pytest.raises(SlackApiError):
        await _unwrap(wake_module.process_wake_request)(
            user_text="Guten Morgen", reply_channel="D0X", author_user_id=""
        )


# --- activity: voice path -------------------------------------------------


async def test_activity_voice_request_transcribes_and_replies_as_voice(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t")
    assistant = FakeAssistant()
    transcriber = FakeTranscriber()
    speaker = FakeSpeaker()
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: assistant)
    monkeypatch.setattr("src.clients.transcription.VoxtralTranscriber", lambda *a, **k: transcriber)
    monkeypatch.setattr("src.clients.tts.VoxtralSpeaker", lambda *a, **k: speaker)
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    answer = await _unwrap(wake_module.process_wake_request)(
        user_text="",
        reply_channel="C0SOURCE",
        author_user_id="U1",
        voice_file_id="F0NOTE",
    )
    assert answer == "Mock-Antwort"
    # The transcript replaced the (empty) message text as the model's task.
    assert assistant.calls[0][1] == "Transkript der Sprachnotiz"
    assert len(transcriber.calls) == 1
    # Reply posted as a voice upload, not as a text message.
    assert slack.posted == []
    assert len(slack.uploads) == 1
    channel, audio, initial_comment = slack.uploads[0]
    assert channel == "C0SOURCE"
    assert audio == b"voice-bytes"
    assert initial_comment == wake_module.WAKE_VOICE_INITIAL_COMMENT
    # No voice id configured: the incoming note served as the TTS sample.
    assert speaker.calls[0][1] == ""
    assert speaker.calls[0][2]  # non-empty base64 ref audio
    assert slack.fetched_files == ["F0NOTE", "F0NOTE"]


async def test_activity_voice_reply_falls_back_to_dm_on_upload_error(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t", fail_upload_channels={"C0NOMEMBER"})
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: FakeAssistant())
    monkeypatch.setattr("src.clients.transcription.VoxtralTranscriber", lambda *a, **k: FakeTranscriber())
    monkeypatch.setattr("src.clients.tts.VoxtralSpeaker", lambda *a, **k: FakeSpeaker())
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    answer = await _unwrap(wake_module.process_wake_request)(
        user_text="",
        reply_channel="C0NOMEMBER",
        author_user_id="U1",
        voice_file_id="F0NOTE",
    )
    assert answer == "Mock-Antwort"
    assert slack.opened == ["U1"]
    assert len(slack.uploads) == 1
    channel, _, _ = slack.uploads[0]
    assert channel == "D0FALLBACK"


async def test_activity_voice_request_degrades_to_text_when_tts_fails(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t")
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: FakeAssistant())
    monkeypatch.setattr("src.clients.transcription.VoxtralTranscriber", lambda *a, **k: FakeTranscriber())
    monkeypatch.setattr("src.clients.tts.VoxtralSpeaker", lambda *a, **k: ExplodingSpeaker())
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    answer = await _unwrap(wake_module.process_wake_request)(
        user_text="",
        reply_channel="C0SOURCE",
        author_user_id="U1",
        voice_file_id="F0NOTE",
    )
    assert answer == "Mock-Antwort"
    assert slack.uploads == []
    assert len(slack.posted) == 1
    channel, text = slack.posted[0]
    assert channel == "C0SOURCE"
    assert "Transkript der Sprachnotiz" in text  # quoted request line
    assert "Mock-Antwort" in text


async def test_activity_prefers_text_of_message_over_empty_transcript_edge(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t")

    class EmptyTranscriber:
        def __init__(self, model=None):
            pass

        async def transcribe(self, audio, filename="voice.mp4"):
            return "   "

    monkeypatch.setattr("src.clients.assistant.MistralAssistant", lambda *a, **k: FakeAssistant())
    monkeypatch.setattr("src.clients.transcription.VoxtralTranscriber", lambda *a, **k: EmptyTranscriber())
    monkeypatch.setattr("src.clients.tts.VoxtralSpeaker", lambda *a, **k: FakeSpeaker())
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    with pytest.raises(WakeInputError):
        await _unwrap(wake_module.process_wake_request)(
            user_text="",
            reply_channel="C0SOURCE",
            author_user_id="U1",
            voice_file_id="F0NOTE",
        )


# --- open_im parsing (MockTransport) --------------------------------------


async def test_open_im_parses_channel_id():
    from src.clients.slack import SlackClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "channel": {"id": "D0NEW"}})

    client = SlackClient(token="t", transport=httpx.MockTransport(handler))
    async with client:
        assert await client.open_im("U1") == "D0NEW"
