"""Tests for the wake workflow: envelope parsing, reply routing, config.

Network-free: the activity imports its collaborators inside the function body,
so tests patch the modules the activity imports from (src.clients.assistant,
src.clients.slack) and call the undecorated activity function, following the
repo convention (see AGENTS.md).
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


def _unwrap(fn):
    """Return the raw function behind the SDK activity decorator."""
    return getattr(fn, "__wrapped__", fn)


# --- parsing -----------------------------------------------------------


def test_extract_message_reads_event_fields():
    text, channel, author = extract_message(ENVELOPE_BODY)
    assert text == "Weck-Test mit Auftrag"
    assert channel == "D012345ABCDE"
    assert author == "U012345ABCD"


@pytest.mark.parametrize(
    "body",
    [None, {}, {"event": {}}, {"event": {"text": "hi"}}, {"event": {"channel": "C1"}}],
)
def test_extract_message_rejects_incomplete_bodies(body):
    with pytest.raises(WakeInputError):
        extract_message(body)


def test_resolve_request_prefers_direct_invocation():
    request = WakeInput(user_text="Auftrag", reply_channel="C0GENERAL")
    assert resolve_request(request) == ("Auftrag", "C0GENERAL", "")


def test_resolve_request_reads_webhook_envelope():
    request = WakeInput(method="POST", body=ENVELOPE_BODY)
    assert resolve_request(request) == ("Weck-Test mit Auftrag", "D012345ABCDE", "U012345ABCD")


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


class RecordingSlack:
    def __init__(self, token, timeout=120.0, fail_channels=()):
        self.fail_channels = set(fail_channels)
        self.posted = []
        self.opened = []

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


async def test_activity_posts_reply_to_source_channel(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t")
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", FakeAssistant)
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


async def test_activity_falls_back_to_dm_when_channel_fails(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    slack = RecordingSlack("t", fail_channels={"D0FOREIGN"})
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", FakeAssistant)
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
    monkeypatch.setattr("src.clients.assistant.MistralAssistant", FakeAssistant)
    monkeypatch.setattr("src.clients.slack.SlackClient", lambda *a, **k: slack)

    with pytest.raises(SlackApiError):
        await _unwrap(wake_module.process_wake_request)(
            user_text="Guten Morgen", reply_channel="D0X", author_user_id=""
        )


# --- open_im parsing (MockTransport) --------------------------------------


async def test_open_im_parses_channel_id():
    from src.clients.slack import SlackClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "channel": {"id": "D0NEW"}})

    client = SlackClient(token="t", transport=httpx.MockTransport(handler))
    async with client:
        assert await client.open_im("U1") == "D0NEW"
