"""Tests for the Voxtral TTS speaker (fake SDK client, no network)."""

import base64

import pytest

from src.clients.tts import TTSError, VoxtralSpeaker


class _Response:
    def __init__(self, audio_data):
        self.audio_data = audio_data


class _AsyncSpeech:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    async def complete_async(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class _Client:
    def __init__(self, speech):
        class _Audio:
            pass
        audio = _Audio()
        audio.speech = speech
        self.audio = audio


def _speaker(speech) -> VoxtralSpeaker:
    return VoxtralSpeaker(model="voxtral-mini-tts-2603", client_factory=lambda: _Client(speech))


async def test_synthesize_returns_decoded_audio() -> None:
    speech = _AsyncSpeech(response=_Response(base64.b64encode(b"mp3-bytes")))
    result = await _speaker(speech).synthesize("Hello world", voice_id="v-1")
    assert result == b"mp3-bytes"
    assert speech.calls[0] == {
        "model": "voxtral-mini-tts-2603",
        "input": "Hello world",
        "voice_id": "v-1",
        "response_format": "mp3",
    }


async def test_synthesize_with_ref_audio_sends_ref_audio() -> None:
    speech = _AsyncSpeech(response=_Response(base64.b64encode(b"wav-bytes")))
    ref = base64.b64encode(b"sample-bytes").decode()
    result = await _speaker(speech).synthesize("Hello", ref_audio_b64=ref)
    assert result == b"wav-bytes"
    assert speech.calls[0] == {
        "model": "voxtral-mini-tts-2603",
        "input": "Hello",
        "ref_audio": ref,
        "response_format": "mp3",
    }


async def test_synthesize_without_any_voice_raises() -> None:
    with pytest.raises(TTSError, match="No voice given"):
        await _speaker(_AsyncSpeech()).synthesize("text", voice_id="", ref_audio_b64="")


async def test_missing_audio_data_raises() -> None:
    speech = _AsyncSpeech(response=_Response(None))
    with pytest.raises(TTSError, match="no audio data"):
        await _speaker(speech).synthesize("text", voice_id="v")


async def test_api_error_is_wrapped() -> None:
    speech = _AsyncSpeech(error=RuntimeError("boom"))
    with pytest.raises(TTSError, match="Speech generation failed"):
        await _speaker(speech).synthesize("text", voice_id="v")


async def test_invalid_base64_is_wrapped() -> None:
    speech = _AsyncSpeech(response=_Response("not-base64!!!"))
    with pytest.raises(TTSError, match="base64"):
        await _speaker(speech).synthesize("text", voice_id="v")
