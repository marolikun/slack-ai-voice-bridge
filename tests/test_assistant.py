"""Network-free tests for the MistralAssistant client wrapper."""

from __future__ import annotations

import pytest

from src.clients.assistant import AssistantError, MistralAssistant


class _Chunk:
    def __init__(self, type_: str, text: str) -> None:
        self.type = type_
        self.text = text


class _Message:
    def __init__(self, content) -> None:
        self.content = content


class _Choice:
    def __init__(self, message) -> None:
        self.message = message


class _Response:
    def __init__(self, choice) -> None:
        self.choices = [choice]


class _Chat:
    def __init__(self, response) -> None:
        self._response = response

    async def complete_async(self, model, messages):
        return self._response


class _Client:
    def __init__(self, response) -> None:
        self.chat = _Chat(response)


# --- _extract_text ------------------------------------------------------


def test_extract_text_plain_string_content():
    response = _Response(_Choice(_Message("  Hello.  ")))
    assert MistralAssistant._extract_text(response) == "Hello."


def test_extract_text_dict_response():
    response = {"choices": [{"message": {"content": "Hi."}}]}
    assert MistralAssistant._extract_text(response) == "Hi."


def test_extract_text_drops_thinking_chunks():
    content = [_Chunk("thinking", "internal reasoning"), _Chunk("text", "Answer.")]
    response = _Response(_Choice(_Message(content)))
    assert MistralAssistant._extract_text(response) == "Answer."


def test_extract_text_joins_multiple_text_chunks():
    content = [_Chunk("thinking", "reason"), _Chunk("text", "Hello "), _Chunk("text", "there.")]
    response = _Response(_Choice(_Message(content)))
    assert MistralAssistant._extract_text(response) == "Hello there."


def test_extract_text_accepts_dict_chunks_and_plain_strings():
    content = [{"type": "thinking", "text": "reason"}, {"type": "text", "text": "Answer."}, "raw"]
    response = _Response(_Choice(_Message(content)))
    assert MistralAssistant._extract_text(response) == "Answer.raw"


def test_extract_text_thinking_only_yields_empty():
    content = [_Chunk("thinking", "internal reasoning")]
    response = _Response(_Choice(_Message(content)))
    assert MistralAssistant._extract_text(response) == ""


def test_extract_text_no_choices_yields_empty():
    assert MistralAssistant._extract_text({"choices": []}) == ""


# --- complete -----------------------------------------------------------


async def test_complete_returns_clean_answer_from_chunked_content():
    content = [_Chunk("thinking", "reason"), _Chunk("text", "Answer.")]
    assistant = MistralAssistant(client_factory=lambda: _Client(_Response(_Choice(_Message(content)))))
    assert await assistant.complete("system prompt", "task text") == "Answer."


async def test_complete_raises_on_empty_text_after_dropping_thinking():
    content = [_Chunk("thinking", "reason")]
    assistant = MistralAssistant(client_factory=lambda: _Client(_Response(_Choice(_Message(content)))))
    with pytest.raises(AssistantError):
        await assistant.complete("system prompt", "task text")


async def test_complete_wraps_client_errors():
    class _Boom:
        chat = None

    assistant = MistralAssistant(client_factory=lambda: _Boom())
    with pytest.raises(AssistantError):
        await assistant.complete("system prompt", "task text")
