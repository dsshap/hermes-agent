"""Small process-local extension registry for Slack-specific UI hooks.

The Slack adapter owns transport/lifecycle concerns. Feature modules can register
exact Block Kit action IDs, optional outbound block builders, and optional
pre-mention message handlers here without importing or modifying the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlackIntegration:
    """Feature-local Slack integration hooks.

    All action IDs are exact static strings. No wildcard or regex dispatch is
    supported; the adapter only calls the integration that registered the
    incoming action ID.
    """

    name: str
    action_ids: tuple[str, ...] = ()
    build_blocks: Optional[Callable[..., Optional[list[dict]]]] = None
    handle_action: Optional[Callable[..., Awaitable[bool]]] = None
    handle_message: Optional[Callable[..., Awaitable[bool]]] = None


_integrations: list[SlackIntegration] = []
_action_owners: dict[str, str] = {}


def register_slack_integration(integration: SlackIntegration) -> None:
    """Register *integration* unless its action IDs conflict.

    Duplicate integration names are treated as idempotent no-ops. Duplicate
    action IDs across different integrations are rejected so Slack payloads
    cannot choose an arbitrary handler.
    """
    if not integration.name:
        logger.warning("[Slack extensions] Ignoring unnamed integration")
        return

    if any(existing.name == integration.name for existing in _integrations):
        logger.debug("[Slack extensions] Integration already registered: %s", integration.name)
        return

    seen: set[str] = set()
    for action_id in integration.action_ids:
        if not action_id or action_id in seen:
            logger.warning(
                "[Slack extensions] Rejecting integration %s with duplicate/empty action ID",
                integration.name,
            )
            return
        seen.add(action_id)
        owner = _action_owners.get(action_id)
        if owner:
            logger.warning(
                "[Slack extensions] Rejecting integration %s: action ID %s already owned by %s",
                integration.name,
                action_id,
                owner,
            )
            return

    _integrations.append(integration)
    for action_id in integration.action_ids:
        _action_owners[action_id] = integration.name


def iter_slack_integrations() -> tuple[SlackIntegration, ...]:
    """Return registered Slack integrations in registration order."""
    return tuple(_integrations)


def clear_slack_integrations_for_tests() -> None:
    """Reset the process-local registry for tests."""
    _integrations.clear()
    _action_owners.clear()
