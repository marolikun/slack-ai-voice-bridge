"""Tests for the connector probe workflow (network-free).

The connector client is injected by the workflow runtime (Depends), so
these tests call the undecorated activity with a fake client and verify
the probe contract: exactly one read-only tool call whose result is
summarized compactly. The functions follow the repo convention of
unwrapping SDK-decorated activities (see AGENTS.md).
"""

from __future__ import annotations

import json

from src.workflows import connector_probe as probe


def _unwrap(fn):
    """Return the raw function behind the SDK activity decorator."""
    return getattr(fn, "__wrapped__", fn)


# --- module declaration --------------------------------------------------


def test_module_declares_the_probe_connector():
    assert probe.google_calendar_connector.connector_name == "google_calendar"
    assert probe.PROBE_TOOL == "list_calendars"


# --- result summarization ------------------------------------------------


def test_summarize_prefers_text_content_items():
    payload = {
        "content": [{"type": "text", "text": json.dumps({"calendars": [{"id": "primary"}]})}],
        "metadata": {"uninteresting": True},
    }
    rendered = probe.summarize_tool_result(payload)
    assert "primary" in rendered
    assert "uninteresting" not in rendered


def test_summarize_falls_back_to_json_dump_for_unknown_shapes():
    payload = {"unexpected": 42}
    rendered = probe.summarize_tool_result(payload)
    assert "42" in rendered


def test_summarize_truncates_long_results():
    payload = {"content": [{"type": "text", "text": "x" * 10_000}]}
    rendered = probe.summarize_tool_result(payload)
    assert "[truncated]" in rendered
    assert len(rendered) <= probe.MAX_RESULT_CHARS + len("[truncated]")


def test_summarize_handles_non_dict_payloads():
    assert probe.summarize_tool_result(None) == "None"


# --- activity (fake connector client) -------------------------------------


class FakeToolCallClient:
    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else {
            "content": [
                {"type": "text", "text": json.dumps({"calendars": [{"id": "primary"}]})}
            ]
        }

    async def call_tool(self, tool_name, arguments=None):
        self.calls.append((tool_name, arguments))
        return self.result


async def test_activity_calls_exactly_one_read_only_tool():
    fake = FakeToolCallClient()
    result = await _unwrap(probe.probe_connector)(client=fake)
    assert fake.calls == [("list_calendars", {})]
    assert "primary" in result
