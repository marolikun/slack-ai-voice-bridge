"""Worker entrypoint: registers all workflows and starts the long-running worker.

Managed deployments run this module as the container command. The Mistral
platform injects a rotating service-account token (MISTRAL_SA_TOKEN_PATH);
Slack credentials arrive via workspace-secret bindings (SLACK_BOT_TOKEN).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.workflows.slack_voice_transcribe import SlackVoiceTranscribeWorkflow
from src.workflows.speak import SpeakWorkflow
from src.workflows.list_voices import ListVoicesWorkflow

logger = logging.getLogger("slack-ai-voice-bridge.worker")

WORKFLOW_CLASSES = [
    SlackVoiceTranscribeWorkflow,
    SpeakWorkflow,
    ListVoicesWorkflow,
]


def _load_run_worker() -> Any:
    """Resolve the SDK entry point across minor SDK layout differences."""
    try:
        from mistralai.workflows import run_worker
    except ImportError:  # pragma: no cover - depends on installed SDK version
        from mistralai_workflows import run_worker  # type: ignore[import-not-found]
    return run_worker


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    names = [cls.__name__ for cls in WORKFLOW_CLASSES]
    logger.info("Starting worker, registering workflows: %s", ", ".join(names))
    run_worker = _load_run_worker()
    await run_worker(WORKFLOW_CLASSES)


if __name__ == "__main__":
    asyncio.run(main())
