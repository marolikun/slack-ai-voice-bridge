"""Tests for SlackClient.upload_audio (3-step external upload flow, no network)."""

import httpx
import pytest

from src.clients.slack import SlackApiError, SlackClient


def _handler(requests):
    """Build a MockTransport handler that records requests and answers the 3 steps."""

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "files.getUploadURLExternal" in str(request.url):
            return httpx.Response(
                200,
                json={"ok": True, "upload_url": "https://files.example.com/u/abc", "file_id": "F123"},
            )
        if "files.example.com" in str(request.url.host if hasattr(request.url, 'host') else request.url):
            return httpx.Response(200, json={"ok": True})
        if "files.completeUploadExternal" in str(request.url):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    return handle


def _client(requests) -> SlackClient:
    return SlackClient(token="t", transport=httpx.MockTransport(_handler(requests)))


async def test_upload_audio_uses_three_step_flow() -> None:
    requests: list[httpx.Request] = []
    async with _client(requests) as slack:
        file_id = await slack.upload_audio(
            "C1", b"audio-bytes", "voice.mp3", thread_ts="123.456", initial_comment="hi"
        )
    assert file_id == "F123"
    assert len(requests) == 3
    get_url, upload, complete = requests
    # Step 1: query params filename/length + bearer auth
    assert str(get_url.url).startswith("https://slack.com/api/files.getUploadURLExternal")
    assert "filename=voice.mp3" in str(get_url.url)
    assert "length=11" in str(get_url.url)
    assert get_url.headers["Authorization"] == "Bearer t"
    # Step 2: raw bytes to the returned upload URL, without bearer auth
    assert str(upload.url) == "https://files.example.com/u/abc"
    assert upload.content == b"audio-bytes"
    assert "Authorization" not in upload.headers
    # Step 3: complete + share, JSON body with thread and comment
    body = complete.read()
    assert body["files"] == [{"id": "F123"}]
    assert body["channel_id"] == "C1"
    assert body["thread_ts"] == "123.456"
    assert body["initial_comment"] == "hi"


async def test_upload_without_thread_omits_fields() -> None:
    requests: list[httpx.Request] = []
    async with _client(requests) as slack:
        await slack.upload_audio("C1", b"audio", "voice.mp3")
    body = requests[2].read()
    assert "thread_ts" not in body
    assert "initial_comment" not in body


async def test_get_url_error_is_wrapped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "scope_denied"})

    slack = SlackClient(token="t", transport=httpx.MockTransport(handle))
    with pytest.raises(SlackApiError, match="getUploadURLExternal"):
        await slack.upload_audio("C1", b"audio", "v.mp3")


async def test_complete_error_is_wrapped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if "getUploadURLExternal" in str(request.url):
            return httpx.Response(
                200, json={"ok": True, "upload_url": "https://files.example.com/u", "file_id": "F1"}
            )
        if "files.example.com" in str(request.url):
            return httpx.Response(200)
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    slack = SlackClient(token="t", transport=httpx.MockTransport(handle))
    with pytest.raises(SlackApiError, match="completeUploadExternal"):
        await slack.upload_audio("C1", b"audio", "v.mp3")
