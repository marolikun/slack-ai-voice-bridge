"""Tests for the Voxtral transcriber (fake SDK client, no network)."""

import pytest

from src.clients.transcription import TranscriptionError, VoxtralTranscriber


class _Response:
    def __init__(self, text):
        self.text = text


class _AsyncTranscriptions:
    """Fake with both async and sync methods (normal SDK surface)."""

    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    async def complete_async(self, *, model, file):
        self.calls.append(("async", model, file))
        if self._error is not None:
            raise self._error
        return self._response

    def complete(self, *, model, file):  # pragma: no cover - async path preferred
        self.calls.append(("sync", model, file))
        if self._error is not None:
            raise self._error
        return self._response


class _SyncOnlyTranscriptions:
    """Fake without the async variant (fallback path)."""

    def __init__(self, response=None):
        self.calls = []
        self._response = response

    def complete(self, *, model, file):
        self.calls.append(("sync", model, file))
        return self._response


class _Client:
    def __init__(self, transcriptions):
        class _Audio:
            pass
        audio = _Audio()
        audio.transcriptions = transcriptions
        self.audio = audio


def _transcriber(transcriptions) -> VoxtralTranscriber:
    client = _Client(transcriptions)
    return VoxtralTranscriber(
        model="voxtral-mini-latest", client_factory=lambda: client
    )


async def test_transcribe_returns_trimmed_text() -> None:
    transcriptions = _AsyncTranscriptions(response=_Response("  Hello world.  "))
    result = await _transcriber(transcriptions).transcribe(
        b"audio-bytes", filename="voice.mp4"
    )
    assert result == "Hello world."


async def test_request_is_wellformed() -> None:
    transcriptions = _AsyncTranscriptions(response=_Response("ok"))
    await _transcriber(transcriptions).transcribe(b"audio-bytes", filename="voice.mp4")
    mode, model, file = transcriptions.calls[0]
    assert mode == "async"
    assert model == "voxtral-mini-latest"
    assert file == {"content": b"audio-bytes", "file_name": "voice.mp4"}


async def test_sync_fallback_used_when_no_async_variant() -> None:
    transcriptions = _SyncOnlyTranscriptions(response=_Response("ok"))
    result = await _transcriber(transcriptions).transcribe(b"audio-bytes")
    assert result == "ok"
    assert transcriptions.calls[0][0] == "sync"


async def test_dict_response_is_supported() -> None:
    transcriptions = _AsyncTranscriptions(response={"text": " Hello. "})
    result = await _transcriber(transcriptions).transcribe(b"audio-bytes")
    assert result == "Hello."


async def test_empty_transcript_raises() -> None:
    transcriptions = _AsyncTranscriptions(response=_Response("   "))
    with pytest.raises(TranscriptionError, match="empty transcript"):
        await _transcriber(transcriptions).transcribe(b"audio-bytes")


async def test_missing_text_field_is_treated_as_empty() -> None:
    transcriptions = _AsyncTranscriptions(response=_Response(None))
    with pytest.raises(TranscriptionError):
        await _transcriber(transcriptions).transcribe(b"audio-bytes")


async def test_api_error_is_wrapped() -> None:
    transcriptions = _AsyncTranscriptions(error=RuntimeError("boom"))
    with pytest.raises(TranscriptionError, match="Transcription request failed"):
        await _transcriber(transcriptions).transcribe(b"audio-bytes")
