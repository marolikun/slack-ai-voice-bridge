"""Workflow: list the saved voices visible to the caller (name + id + languages).

Diagnostics companion to slack-voice-speak: saved voices are owned by USER
accounts - the deployment's service account sees only the platform presets.
When the optional workspace secret MISTRAL_USER_API_KEY is bound (env
MISTRAL_USER_API_KEY), the voices client authenticates with that user key
and user-owned voices (e.g. personal saved voices) become visible, so a
voice name can be resolved to its voice_id.
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    import mistralai.workflows as workflows
except ImportError:  # pragma: no cover - SDK module layout varies across versions
    import mistralai_workflows as workflows  # type: ignore[no-redef]

from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _voices_client() -> Any:
    """Return the Mistral client for voice listing (user key if configured)."""
    from mistralai.workflows.client import get_mistral_client

    user_key = os.environ.get("MISTRAL_USER_API_KEY", "").strip()
    if user_key:
        return get_mistral_client(api_key=user_key)
    return get_mistral_client()


async def _list_voices() -> list[dict[str, Any]]:
    """List all voices visible to the caller (offset pagination)."""
    client = _voices_client()
    voices_api = client.audio.voices
    collected: list[Any] = []
    limit = 20
    offset = 0
    while True:
        if hasattr(voices_api, "list_async"):
            page = await voices_api.list_async(limit=limit, offset=offset)
        else:
            page = voices_api.list(limit=limit, offset=offset)
        items = getattr(page, "items", None) or (page.get("items") if isinstance(page, dict) else []) or []
        collected.extend(items)
        total = getattr(page, "total", None)
        if total is None and isinstance(page, dict):
            total = page.get("total")
        if total is not None and offset + len(items) >= int(total):
            break
        if not items:
            break
        offset += len(items)
    result: list[dict[str, Any]] = []
    for voice in collected:
        if isinstance(voice, dict):
            result.append(voice)
        else:
            result.append(
                {
                    "id": getattr(voice, "id", ""),
                    "name": getattr(voice, "name", ""),
                    "languages": getattr(voice, "languages", None),
                    "created_at": str(getattr(voice, "created_at", "")),
                }
            )
    return result


@workflows.activity()
async def list_saved_voices() -> str:
    """Return one line per visible voice: 'name | id | languages | created'."""
    voices = await _list_voices()
    if not voices:
        return "no voices visible to this caller"
    lines = []
    for v in voices:
        lines.append(
            "{} | {} | {} | {}".format(
                v.get("name", ""),
                v.get("id", ""),
                ",".join(v.get("languages") or []),
                v.get("created_at", ""),
            )
        )
    logger.info("Listed %d voices visible to the caller", len(voices))
    return "\n".join(lines)


class ListVoicesInput(BaseModel):
    """Workflow input (empty; exists for schema compatibility)."""

    pass


@workflows.workflow.define(
    name="list-voices",
    workflow_display_name="List Voices",
    workflow_description="Lists the voices visible to the caller (name, id, languages) to resolve a voice name to its voice_id.",
)
class ListVoicesWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: ListVoicesInput) -> str:
        return await list_saved_voices()
