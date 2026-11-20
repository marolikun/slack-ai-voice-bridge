"""Workflow: probe connector availability from a managed deployment.

Identity probe: calls one read-only tool (list_calendars) on a Studio
connector (google_calendar) through the workflows connector plugin.
The platform resolves the connector credentials for the identity the
deployment runs under. The outcome is visible on the execution record:

- COMPLETED with a calendar list: credentials resolve for this
  deployment identity. Connector-based checks run fine on it.
- The workflow pauses with an auth_url event: no credentials stored yet
  for this identity; the platform offers a consent URL. Completing it
  stores connector credentials for the running identity and the
  workflow resumes.
- FAILED with a ConnectorError: the identity cannot use the connector;
  credentials must be added for the deployment identity, or the checks
  need an on-behalf-of (OBO) hardened deployment.

The tool call is read-only; the workflow posts nowhere and needs no
secret bindings. The connector client is injected by the workflow
runtime (Depends), so nothing network-facing is constructed at
module import (worker-validation sandbox rule).
"""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    import mistralai.workflows as workflows
except ImportError:  # pragma: no cover - SDK module layout varies across versions
    import mistralai_workflows as workflows  # type: ignore[no-redef]

from mistralai.workflows import Depends
from mistralai.workflows.plugins.mistralai.connectors import (
    ToolCallClient,
    connector,
    uses_connectors,
)
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PROBE_CONNECTOR = "google_calendar"
PROBE_TOOL = "list_calendars"
MAX_RESULT_CHARS = 2000

google_calendar_connector = connector(PROBE_CONNECTOR)


def summarize_tool_result(payload: Any, max_chars: int = MAX_RESULT_CHARS) -> str:
    """Render a connector tool result (model-dumped dict) compactly.

    Connector tool calls return a dumped response object whose payload
    lives in content as MCP text items. Prefer those text items; fall
    back to a JSON dump so unexpected shapes stay inspectable.
    """
    if isinstance(payload, dict):
        texts: list[str] = []
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    texts.append(item["text"])
        if texts:
            rendered = json.dumps(texts, ensure_ascii=False)
        else:
            rendered = json.dumps(payload, ensure_ascii=False, default=str)
    else:
        rendered = str(payload)
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars] + "...[truncated]"
    return rendered


@workflows.activity(name="probe-connector-list-calendars")
async def probe_connector(
    client: ToolCallClient = Depends(google_calendar_connector),
) -> str:
    """Call the read-only probe tool once; return a compact summary."""
    logger.info("Probing connector %s with tool %s", PROBE_CONNECTOR, PROBE_TOOL)
    result = await client.call_tool(tool_name=PROBE_TOOL, arguments={})
    logger.info("Connector tool %s returned a result", PROBE_TOOL)
    return summarize_tool_result(result)


class ConnectorProbeInput(BaseModel):
    """Workflow input (empty; exists for schema compatibility)."""


@workflows.workflow.define(
    name="connector-probe",
    workflow_display_name="Connector Probe",
    workflow_description=(
        "Probes whether the deployment identity can use a Studio connector "
        "by calling one read-only connector tool (list_calendars)."
    ),
)
@uses_connectors(google_calendar_connector)
class ConnectorProbeWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: ConnectorProbeInput) -> str:
        return await probe_connector()
