"""Chat completions via Mistral's Workflows SDK client.

Mirrors the pattern of src/clients/transcription.py: managed deployments mount
a rotating service-account token (MISTRAL_SA_TOKEN_PATH) that the Workflows
SDK client resolves, so a raw Mistral(api_key=...) client is never constructed
here. get_mistral_client() must be called inside activities; the client factory
is injectable so unit tests need no network or platform credentials.
"""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_CHAT_MODEL = "mistral-large-latest"


class AssistantError(RuntimeError):
    """Raised when the chat completion request fails or yields no text."""


def _default_client_factory() -> Any:
    from mistralai.workflows.client import get_mistral_client

    return get_mistral_client()


class MistralAssistant:
    """Answers prompts with a chat model via the Workflows SDK client."""

    def __init__(
        self,
        model: str = DEFAULT_CHAT_MODEL,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._model = model
        self._client_factory = client_factory or _default_client_factory

    async def complete(self, system_prompt: str, user_text: str) -> str:
        """Return the assistant's answer for the given task text (trimmed)."""
        client = self._client_factory()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ]
        try:
            chat = client.chat
            if hasattr(chat, "complete_async"):
                response = await chat.complete_async(model=self._model, messages=messages)
            else:
                # Fallback for sync-only client surfaces; blocks briefly.
                response = chat.complete(model=self._model, messages=messages)
        except Exception as exc:  # noqa: BLE001 - surface a single, stable error
            raise AssistantError("Chat completion request failed: " + str(exc)) from exc
        text = self._extract_text(response)
        if not text:
            raise AssistantError("Model returned an empty response")
        return text

    @staticmethod
    def _extract_text(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return ""
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None and isinstance(first, dict):
            message = first.get("message")
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            # Split-reasoning models return the message content as a list of
            # chunk objects (e.g. thinking and text chunks). Only the text
            # chunks carry the user-facing answer; thinking chunks are dropped.
            return MistralAssistant._join_text_chunks(content).strip()
        return str(content or "").strip()

    @staticmethod
    def _join_text_chunks(chunks: list) -> str:
        parts: list[str] = []
        for chunk in chunks:
            if isinstance(chunk, str):
                parts.append(chunk)
                continue
            chunk_type = getattr(chunk, "type", None)
            text = getattr(chunk, "text", None)
            if text is None and isinstance(chunk, dict):
                text = chunk.get("text")
                chunk_type = chunk.get("type", chunk_type)
            if text is None:
                continue
            if chunk_type == "thinking":
                continue
            parts.append(str(text))
        return "".join(parts)
