"""Email unsubscriber plugin registration."""

from __future__ import annotations

from .cli import email_unsubscriber_command, register_cli
from .slack_integration import register as register_slack_integration


def register(ctx) -> None:
    register_slack_integration()

    ctx.register_cli_command(
        name="email-unsubscriber",
        help="Gmail Trash unsubscribe assistant",
        setup_fn=register_cli,
        handler_fn=email_unsubscriber_command,
        description=(
            "Scan Gmail Trash, report unsubscribe candidates to Slack, "
            "and unsubscribe safely after confirmation."
        ),
    )
