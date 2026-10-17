"""Minimal async Slack Web API client for the voice pipeline (both directions).

Transcription direction: fetch file metadata, download a private file, post a
message. Reply direction: upload audio and share it into a channel/thread.

File uploads use the modern 3-step flow (files.getUploadURLExternal -> POST
bytes to the upload URL -> files.completeUploadExternal) because the legacy
files.upload endpoint is deprecated and now answers method_deprecated.
Requires the bot scopes files:read and files:write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

SLACK_API_BASE_URL = "https://slack.com/api"


class SlackApiError(RuntimeError):
    """Slack answered with an API-level error (ok: false)."""

    def __init__(self, method: str, slack_error: str, detail: Any = None) -> None:
        super().__init__("Slack API call '" + method + "' failed: " + slack_error)
        self.method = method
        self.slack_error = slack_error
        self.detail = detail


@dataclass(frozen=True)
class SlackFile:
    """The subset of a Slack file object this pipeline needs."""

    file_id: str
    name: str
    mimetype: str
    download_url: str


def _parse_file(raw: dict[str, Any]) -> SlackFile:
    """Map a Slack file object to a SlackFile instance."""
    download_url = raw.get("url_private_download") or raw.get("url_private")
    if not download_url:
        raise SlackApiError("files.info", "file_has_no_download_url", raw.get("id"))
    return SlackFile(
        file_id=str(raw.get("id", "")),
        name=str(raw.get("name", "voice.mp4")),
        mimetype=str(raw.get("mimetype", "audio/mp4")),
        download_url=str(download_url),
    )


class SlackClient:
    """Thin async wrapper around the Slack endpoints this pipeline uses.

    The HTTP transport is injectable so unit tests can supply an
    httpx.MockTransport instead of touching the network.
    """

    def __init__(
        self,
        token: str,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=SLACK_API_BASE_URL,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> SlackClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self._token}

    async def get_file(self, file_id: str) -> SlackFile:
        """Fetch file metadata via files.info."""
        response = await self._client.get(
            "files.info", params={"file": file_id}, headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackApiError("files.info", str(data.get("error", "unknown_error")))
        return _parse_file(data["file"])

    async def download_file(self, download_url: str) -> bytes:
        """Download a private file (absolute URL, authorized via bearer token)."""
        response = await self._client.get(download_url, headers=self._headers())
        response.raise_for_status()
        return response.content

    async def post_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> None:
        """Post a message via chat.postMessage, optionally as a threaded reply."""
        payload: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        response = await self._client.post(
            "chat.postMessage", json=payload, headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise SlackApiError("chat.postMessage", str(data.get("error", "unknown_error")))

    async def upload_audio(
        self,
        channel_id: str,
        audio: bytes,
        filename: str,
        thread_ts: str | None = None,
        initial_comment: str | None = None,
    ) -> str:
        """Upload audio and share it into a channel (optionally a thread).

        Uses the 3-step external upload flow; returns the new Slack file ID.
        """
        # 1. Request an external upload URL (query params, not JSON body).
        url_response = await self._client.get(
            "files.getUploadURLExternal",
            params={"filename": filename, "length": str(len(audio))},
            headers=self._headers(),
        )
        url_response.raise_for_status()
        url_data = url_response.json()
        if not url_data.get("ok"):
            raise SlackApiError(
                "files.getUploadURLExternal", str(url_data.get("error", "unknown_error"))
            )
        upload_url = str(url_data["upload_url"])
        file_id = str(url_data["file_id"])

        # 2. Upload the raw bytes to the returned URL (absolute URL, no auth).
        upload = await self._client.post(upload_url, content=audio)
        upload.raise_for_status()

        # 3. Complete the upload and share the file.
        payload: dict[str, Any] = {
            "files": [{"id": file_id}],
            "channel_id": channel_id,
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts
        if initial_comment:
            payload["initial_comment"] = initial_comment
        complete = await self._client.post(
            "files.completeUploadExternal", json=payload, headers=self._headers()
        )
        complete.raise_for_status()
        complete_data = complete.json()
        if not complete_data.get("ok"):
            raise SlackApiError(
                "files.completeUploadExternal",
                str(complete_data.get("error", "unknown_error")),
            )
        return file_id
