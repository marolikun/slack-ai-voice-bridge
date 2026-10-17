"""Transcription of audio bytes via Mistral's Voxtral speech-to-text API.

Uses get_mistral_client() from the Workflows SDK instead of a raw REST call
with an API key. Rationale: managed deployments do NOT inject
MISTRAL_API_KEY - by design the platform mounts a rotating service-account
token (MISTRAL_SA_TOKEN_PATH) which the Workflows SDK client resolves
automatically. The SDK client also adds workflow telemetry and observability
metadata that a vanilla Mistral(...) client would miss.

Per the docs, get_mistral_client() must be used inside activities; this
transcriber is constructed and called from within the activity body only.
"""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_MODEL = "voxtral-mini-latest"


class TranscriptionError(RuntimeError):
    """Raised when transcription is impossible or yields no usable text."""


def _default_client_factory() -> Any:
    from mistralai.workflows.client import get_mistral_client

    return get_mistral_client()


class VoxtralTranscriber:
    """Transcribes audio bytes with a Voxtral model via the Workflows SDK client.

    The client factory is injectable so unit tests can supply a fake client
    instead of touching the network or the platform credential chain.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._model = model
        self._client_factory = client_factory or _default_client_factory

    async def transcribe(self, audio: bytes, filename: str = "voice.mp4") -> str:
        """Return the transcript for the given audio bytes (whitespace-trimmed)."""
        client = self._client_factory()
        try:
            transcriptions = client.audio.transcriptions
            file_payload = {"content": audio, "file_name": filename}
            if hasattr(transcriptions, "complete_async"):
                response = await transcriptions.complete_async(
                    model=self._model,
                    file=file_payload,
                )
            else:
                # Fallback for sync-only client surfaces; blocks briefly.
                response = transcriptions.complete(
                    model=self._model,
                    file=file_payload,
                )
        except Exception as exc:  # noqa: BLE001 - surface a single, stable error
            raise TranscriptionError(
                "Transcription request failed: " + str(exc)
            ) from exc
        text = self._extract_text(response)
        if not text:
            raise TranscriptionError("Model returned an empty transcript")
        return text

    @staticmethod
    def _extract_text(response: Any) -> str:
        text = getattr(response, "text", None)
        if text is None and isinstance(response, dict):
            text = response.get("text")
        return str(text or "").strip()
