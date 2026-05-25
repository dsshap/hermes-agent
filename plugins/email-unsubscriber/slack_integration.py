"""Slack UI integration for the email unsubscriber feature."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from .gmail_unsubscriber import (
    DEFAULT_SLACK_CHANNEL,
    handle_slack_unsubscribe_text,
    unsubscribe,
    unsubscribe_many,
)
from gateway.platforms.slack_extensions import SlackIntegration, register_slack_integration

logger = logging.getLogger(__name__)

ACTION_SELECT = "email_unsubscriber_select"
ACTION_UNSUBSCRIBE_SELECTED = "email_unsubscriber_unsubscribe_selected"
ACTION_UNSUBSCRIBE_ALL = "email_unsubscriber_unsubscribe_all"
_ACTION_IDS = (ACTION_SELECT, ACTION_UNSUBSCRIBE_SELECTED, ACTION_UNSUBSCRIBE_ALL)
_REPORT_TITLE = "# Gmail Trash Auto-Unsubscriber"
_CANDIDATE_RE = re.compile(r"- `(u-[^`]+)` \*\*([^*]+)\*\*(?: via `([^`]+)`)?(?: — (.*))?")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def _short_text(value: str, limit: int) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _format_summary(content: str, adapter: Any = None) -> str:
    summary = "\n".join((content or "").splitlines()[:8])[:2900]
    summary = _URL_RE.sub("[redacted URL]", summary)
    formatter = getattr(adapter, "format_message", None)
    if callable(formatter):
        return formatter(summary)
    return summary


def build_blocks(content: str, adapter: Any = None, metadata: Optional[dict] = None) -> Optional[list[dict]]:
    """Build Block Kit controls for an email-unsubscriber report."""
    if _REPORT_TITLE not in (content or ""):
        return None

    candidate_lines = _CANDIDATE_RE.findall(content or "")
    if not candidate_lines:
        return None

    options: list[dict] = []
    for cid, sender, _domain, subject in candidate_lines[:50]:
        subject = _URL_RE.sub("[redacted URL]", (subject or "").strip()) or "(no subject)"
        label = _short_text(f"{sender}: {subject}", 75)
        options.append(
            {
                "text": {"type": "plain_text", "text": label, "emoji": True},
                "value": cid,
            }
        )

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _format_summary(content, adapter)}},
        {
            "type": "input",
            "block_id": "email_unsubscriber_selection",
            "optional": True,
            "label": {"type": "plain_text", "text": "Select senders to unsubscribe", "emoji": True},
            "element": {
                "type": "multi_static_select",
                "action_id": ACTION_SELECT,
                "placeholder": {"type": "plain_text", "text": "Choose unsubscribe candidates", "emoji": True},
                "options": options,
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Unsubscribe selected", "emoji": True},
                    "style": "primary",
                    "action_id": ACTION_UNSUBSCRIBE_SELECTED,
                    "value": "selected",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Unsubscribe all", "emoji": True},
                    "style": "danger",
                    "action_id": ACTION_UNSUBSCRIBE_ALL,
                    "value": "all",
                },
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Thread replies also work: `unsubscribe <candidate-id>` or `unsubscribe all`.",
            },
        },
    ]


def _selected_candidate_ids(body: dict) -> list[str]:
    selected: list[str] = []
    values = (body.get("state") or {}).get("values") or {}
    for block_values in values.values():
        if not isinstance(block_values, dict):
            continue
        for item in block_values.values():
            if not isinstance(item, dict):
                continue
            for option in item.get("selected_options") or []:
                value = option.get("value") if isinstance(option, dict) else None
                if isinstance(value, str) and value and value not in selected:
                    selected.append(value)
    return selected


async def handle_action(adapter: Any, body: dict, action: dict) -> bool:
    """Handle email-unsubscriber Block Kit actions."""
    action_id = action.get("action_id", "") if isinstance(action, dict) else ""
    if action_id not in _ACTION_IDS:
        return False
    if action_id == ACTION_SELECT:
        return True

    body = body if isinstance(body, dict) else {}
    channel_id = (body.get("channel") or {}).get("id") or ""
    if channel_id and channel_id != DEFAULT_SLACK_CHANNEL:
        logger.warning("[Slack email-unsubscriber] Ignoring action outside configured channel: %s", channel_id)
        return True

    if action_id == ACTION_UNSUBSCRIBE_ALL:
        result_text = await asyncio.to_thread(unsubscribe, "all", unsubscribe_all=True)
    else:
        selected = _selected_candidate_ids(body)
        result_text = (
            "No unsubscribe candidates selected."
            if not selected
            else await asyncio.to_thread(unsubscribe_many, selected)
        )

    message = body.get("message") or {}
    thread_ts = message.get("thread_ts") or message.get("ts")
    if channel_id:
        await adapter._get_client(channel_id).chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=result_text,
        )
    return True


async def handle_message(
    adapter: Any,
    event: dict,
    original_text: str,
    channel_id: str,
    thread_ts: str,
) -> bool:
    """Handle plain Slack thread replies for email unsubscribe confirmations."""
    result = await asyncio.to_thread(
        handle_slack_unsubscribe_text,
        original_text,
        channel_id,
        thread_ts,
    )
    if not result:
        return False

    await adapter._get_client(channel_id).chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text=result,
    )
    return True


integration = SlackIntegration(
    name="email_unsubscriber.slack",
    action_ids=_ACTION_IDS,
    build_blocks=build_blocks,
    handle_action=handle_action,
    handle_message=handle_message,
)


def register() -> None:
    """Register the email-unsubscriber Slack integration."""
    register_slack_integration(integration)
