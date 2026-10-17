"""Text-to-speech via Mistral's Voxtral TTS API (models voxtral-mini-tts-*).

Uses get_mistral_client() from the Workflows SDK - the same pattern as the
transcription client: it authenticates with the deployment's service-account
token and adds workflow telemetry. Two mutually exclusive ways to define the
voice (one of them must be given):

* voice_id      - a saved voice from the Voices API. Saved voices are owned
                  by USER accounts; a service account cannot own or use
                  audio resources. voice_id therefore only works when
                  MISTRAL_USER_API_KEY is bound (user-key client) or when a
                  preset voice is usable by the caller - otherwise the API
                  answers 404 "Voice not found".
* ref_audio     - a base64-encoded reference clip passed inline
                  ("clone on the fly"). Works for service accounts and
                  avoids the ownership restriction entirely; needs a
                  2-3s+ consented sample (voice cloning usage policy).

Client selection: when the env var MISTRAL_USER_API_KEY is set (workspace
secret "mistral_user_api_key"), the client authenticates with that user key
(get_mistral_client(api_key=...)) - telemetry still flows through the
workflows helper. Otherwise the default service-account client is used.

The response's audio_data field is base64-encoded; this module returns
decoded bytes.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Callable

DEFAULT_TTS_MODEL = "voxtral-mini-tts-2603"


class TTSError(RuntimeError):
    """Raised when speech generation is impossible or yields no audio."""


def _default_client_factory() -> Any:
    from mistralai.workflows.client import get_mistral_client

    user_key = os.environ.get("MISTRAL_USER_API_KEY", "").strip()
    if user_key:
        # User-owned saved voices need a user principal; explicit api_key
        # wins over the service-account token (see docs: Mistral client).
        return get_mistral_client(api_key=user_key)
    return get_mistral_client()


class VoxtralSpeaker:
    """Synthesizes speech from text with a Voxtral TTS model.

    The client factory is injectable so unit tests can supply a fake client
    instead of touching the network or the platform credential chain.
    """

    def __init__(
        self,
        model: str = DEFAULT_TTS_MODEL,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._model = model
        self._client_factory = client_factory or _default_client_factory

    async def synthesize(
        self,
        text: str,
        voice_id: str = "",
        ref_audio_b64: str = "",
        response_format: str = "mp3",
    ) -> bytes:
        """Return synthesized audio bytes for the given text (mp3 by default).

        Pass either voice_id (saved voice) or ref_audio_b64 (inline reference
        clip, cloned on the fly).
        """
        if not voice_id and not ref_audio_b64:
            raise TTSError(
                "No voice given: pass voice_id (saved voice) or ref_audio_b64"
                " (inline reference audio); one of the two is required."
            )
        client = self._client_factory()
        try:
            speech = client.audio.speech
            kwargs: dict[str, Any] = {
                "model": self._model,
                "input": text,
                "response_format": response_format,
            }
            if voice_id:
                kwargs["voice_id"] = voice_id
            else:
                kwargs["ref_audio"] = ref_audio_b64
            if hasattr(speech, "complete_async"):
                response = await speech.complete_async(**kwargs)
            else:
                # Fallback for sync-only client surfaces; blocks briefly.
                response = speech.complete(**kwargs)
        except Exception as exc:  # noqa: BLE001 - surface a single, stable error
            raise TTSError("Speech generation failed: " + str(exc)) from exc
        audio_b64 = getattr(response, "audio_data", None)
        if audio_b64 is None and isinstance(response, dict):
            audio_b64 = response.get("audio_data")
        if not audio_b64:
            raise TTSError("Speech response contained no audio data")
        try:
            return base64.b64decode(str(audio_b64))
        except Exception as exc:  # noqa: BLE001 - stable error for bad payloads
            raise TTSError("Speech audio was not valid base64: " + str(exc)) from exc
