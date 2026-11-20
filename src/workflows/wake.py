"""Workflow: answer a Slack wake-up message with a chat model and reply.

Triggered by an event webhook (e.g. a direct message to the Slack app) or
called directly. Two input shapes are supported:

1. Webhook envelope: Mistral delivers the raw HTTP request it received:
   {"method": "...", "headers": {...}, "query_params": {},
    "body": {...Slack event_callback payload...}}. The workflow extracts the
   message text, the channel it was posted in, and the author's user id.
2. Direct invocation: {"user_text": "...", "reply_channel": "..."} for manual
   runs and future scheduled sweeps.

The answer is generated with the configured chat model (get_mistral_client()
service-account token) and posted back with the Slack app behind
SLACK_BOT_TOKEN. If that app cannot post into the source channel (for example
a direct message the app is not part of), the workflow falls back to opening
the app's own direct-message channel with the author and answering there.

Errors (AssistantError, SlackApiError, ConfigError, WakeInputError)
intentionally propagate: the workflow engine records them on the execution.
Sandbox note: httpx-based and SDK client imports stay inside activity bodies.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import mistralai.workflows as workflows
except ImportError:  # pragma: no cover - SDK module layout varies across versions
    import mistralai_workflows as workflows  # type: ignore[no-redef]

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

WAKE_REPLY_HEADER = ":sun: *Wake reply*"


class WakeInputError(ValueError):
    """Raised when the input cannot be resolved into a task."""


class WakeInput(BaseModel):
    """Workflow input - see module docstring."""

    user_text: str = Field(
        default="", description="Direct invocation: the task text to answer"
    )
    reply_channel: str = Field(
        default="", description="Direct invocation: channel to post the answer to"
    )
    method: str = Field(default="", description="Webhook envelope: HTTP method")
    headers: dict[str, str] | None = Field(
        default=None, description="Webhook envelope: request headers"
    )
    query_params: dict[str, str] | None = Field(
        default=None, description="Webhook envelope: query parameters"
    )
    body: dict[str, Any] | None = Field(
        default=None, description="Webhook envelope: the parsed Slack event payload"
    )
    raw_body: str = Field(default="", description="Webhook envelope: the raw request body")


def extract_message(body: dict[str, Any] | None) -> tuple[str, str, str]:
    """Extract (text, channel_id, author_user_id) from a Slack event payload."""
    if not isinstance(body, dict):
        raise WakeInputError("Input has no Slack event body")
    event = body.get("event")
    if not isinstance(event, dict):
        raise WakeInputError("Slack event body has no event object")
    text = str(event.get("text", "")).strip()
    channel = str(event.get("channel", "")).strip()
    author = str(event.get("user", "")).strip()
    if not text:
        raise WakeInputError("Slack event carries no message text")
    if not channel:
        raise WakeInputError("Slack event carries no channel id")
    return text, channel, author


def resolve_request(input: WakeInput) -> tuple[str, str, str]:
    """Normalize the two input shapes into (task_text, reply_channel, author)."""
    if input.user_text:
        if not input.reply_channel:
            raise WakeInputError("Direct invocation needs a reply_channel")
        return input.user_text, input.reply_channel, ""
    text, channel, author = extract_message(input.body)
    return text, channel, author


def build_wake_reply(request_text: str, answer: str, header: str = WAKE_REPLY_HEADER) -> str:
    """Format the Slack reply: header, quoted request, then the answer."""
    lines = request_text.splitlines() or [request_text]
    quoted = "\n".join("> " + line for line in lines)
    return header + "\n" + quoted + "\n\n" + answer


@workflows.activity()
async def process_wake_request(
    user_text: str, reply_channel: str, author_user_id: str = ""
) -> str:
    """Answer the wake-up message and post the reply; returns the answer text."""
    from src.clients.assistant import MistralAssistant
    from src.clients.slack import SlackApiError, SlackClient
    from src.config import Settings

    settings = Settings.from_env()
    assistant = MistralAssistant(model=settings.wake_model)
    logger.info("Answering wake-up request with model %s", settings.wake_model)
    answer = await assistant.complete(settings.wake_system_prompt, user_text)

    reply = build_wake_reply(user_text, answer)
    slack = SlackClient(
        token=settings.slack_bot_token, timeout=settings.http_timeout_seconds
    )
    async with slack:
        try:
            await slack.post_message(channel_id=reply_channel, text=reply)
            logger.info("Wake reply posted to channel %s", reply_channel)
            return answer
        except SlackApiError as exc:
            if not author_user_id:
                raise
            logger.info(
                "Cannot post to %s (%s); falling back to a direct message to %s",
                reply_channel,
                exc.slack_error,
                author_user_id,
            )
            dm_channel = await slack.open_im(author_user_id)
            await slack.post_message(channel_id=dm_channel, text=reply)
            logger.info("Wake reply posted to author via direct message %s", dm_channel)
    return answer


@workflows.workflow.define(
    name="wake",
    workflow_display_name="Wake",
    workflow_description=(
        "Answers a Slack wake-up message with a chat model and posts the reply "
        "back to the author (webhook envelope or direct invocation)."
    ),
)
class WakeWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: WakeInput) -> str:
        request = resolve_request(input)
        return await process_wake_request(*request)
