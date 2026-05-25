from pathlib import Path

import gateway.platforms.slack as slack_mod
import gateway.platforms.slack_extensions as slack_extensions_mod


def test_slack_core_has_no_email_unsubscriber_imports_or_handlers():
    source = "\n".join(
        Path(module.__file__).read_text(encoding="utf-8")
        for module in (slack_mod, slack_extensions_mod)
    )

    forbidden = [
        "email_assistant.gmail_unsubscriber",
        "email_assistant.slack_integration",
        "hermes_plugins.email_unsubscriber",
        "plugins/email-unsubscriber",
        "handle_slack_unsubscribe_text",
        "_email_unsubscriber_blocks",
        "_handle_email_unsubscriber_action",
        "email_unsubscriber_select",
        "email_unsubscriber_unsubscribe_selected",
        "email_unsubscriber_unsubscribe_all",
        "Gmail Trash Auto-Unsubscriber",
    ]

    for needle in forbidden:
        assert needle not in source
