"""Tests for the Slack client, using httpx.MockTransport (no network)."""

import json

import httpx
import pytest

from src.clients.slack import SlackApiError, SlackClient, SlackFile

_TOKEN = "xoxb-test-token"


def _client(handler) -> SlackClient:
    return SlackClient(token=_TOKEN, transport=httpx.MockTransport(handler))


async def test_get_file_parses_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["file"] == "F123"
        assert request.headers["Authorization"] == "Bearer " + _TOKEN
        return httpx.Response(
            200,
            json={
                "ok": True,
                "file": {
                    "id": "F123",
                    "name": "voice.mp4",
                    "mimetype": "audio/mp4",
                    "url_private_download": "https://files.slack.com/files-pri/F123/download",
                },
            },
        )

    async with _client(handler) as slack:
        file = await slack.get_file("F123")

    assert file == SlackFile(
        file_id="F123",
        name="voice.mp4",
        mimetype="audio/mp4",
        download_url="https://files.slack.com/files-pri/F123/download",
    )


async def test_get_file_raises_slack_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "file_not_found"})

    async with _client(handler) as slack:
        with pytest.raises(SlackApiError) as exc_info:
            await slack.get_file("F404")
    assert exc_info.value.slack_error == "file_not_found"
    assert exc_info.value.method == "files.info"


async def test_get_file_rejects_file_without_download_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "file": {"id": "F1", "name": "x", "mimetype": "audio/mp4"}},
        )

    async with _client(handler) as slack:
        with pytest.raises(SlackApiError) as exc_info:
            await slack.get_file("F1")
    assert exc_info.value.slack_error == "file_has_no_download_url"


async def test_download_file_returns_bytes_authorized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer " + _TOKEN
        return httpx.Response(200, content=b"audio-bytes")

    async with _client(handler) as slack:
        content = await slack.download_file("https://files.slack.com/some/file")
    assert content == b"audio-bytes"


async def test_post_message_sends_thread_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as slack:
        await slack.post_message("C123", "hello", thread_ts="123.456")

    assert captured["payload"] == {"channel": "C123", "text": "hello", "thread_ts": "123.456"}


async def test_post_message_omits_thread_ts_when_none() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as slack:
        await slack.post_message("C123", "hello")

    assert "thread_ts" not in captured["payload"]


async def test_post_message_raises_on_slack_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    async with _client(handler) as slack:
        with pytest.raises(SlackApiError) as exc_info:
            await slack.post_message("C404", "hello")
    assert exc_info.value.slack_error == "channel_not_found"
    assert exc_info.value.method == "chat.postMessage"
