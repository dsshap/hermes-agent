import pytest

from .plugin_loader import load_email_unsubscriber_plugin

_plugin = load_email_unsubscriber_plugin()
gu = _plugin.gmail_unsubscriber
si = _plugin.slack_integration
from gateway.platforms.slack_extensions import (
    SlackIntegration,
    clear_slack_integrations_for_tests,
    iter_slack_integrations,
    register_slack_integration,
)


class _FakeSlackClient:
    def __init__(self):
        self.posts = []

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ok": True, "ts": "reply-ts"}


class _FakeAdapter:
    def __init__(self):
        self.client = _FakeSlackClient()

    def _get_client(self, channel_id):
        return self.client

    def format_message(self, content):
        return content


@pytest.fixture(autouse=True)
def _clear_slack_extensions():
    clear_slack_integrations_for_tests()
    yield
    clear_slack_integrations_for_tests()


@pytest.mark.asyncio
async def test_slack_unsubscribe_reply_for_dedicated_channel(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    def fake_unsubscribe(cid, unsubscribe_all=False):
        return f"unsubscribed {cid} all={unsubscribe_all}"

    monkeypatch.setattr(gu, "unsubscribe", fake_unsubscribe)
    adapter = _FakeAdapter()

    assert await si.handle_message(adapter, {}, "unsubscribe all", "C0B6Q5G9QQG", "thread-ts") is True
    assert await si.handle_message(adapter, {}, "unsubscribe u-abc123", "C0B6Q5G9QQG", "thread-ts") is True
    assert await si.handle_message(adapter, {}, "review u-manual", "C0B6Q5G9QQG", "thread-ts") is False
    assert await si.handle_message(adapter, {}, "approve all", "C0B6Q5G9QQG", "thread-ts") is False
    assert await si.handle_message(adapter, {}, "unsubscribe all", "COTHER", "thread-ts") is False
    assert await si.handle_message(adapter, {}, "hello", "C0B6Q5G9QQG", "thread-ts") is False
    assert adapter.client.posts[:2] == [
        {"channel": "C0B6Q5G9QQG", "thread_ts": "thread-ts", "text": "unsubscribed all all=True"},
        {"channel": "C0B6Q5G9QQG", "thread_ts": "thread-ts", "text": "unsubscribed u-abc123 all=False"},
    ]


def test_slack_report_builds_wide_message_multiselect_blocks():
    content = "\n".join([
        "# Gmail Trash Auto-Unsubscriber",
        "",
        "Pending browser-assisted candidates: **1**",
        "",
        "- `u-abc123` **news@example.com** — Sale",
    ])

    blocks = si.build_blocks(content, adapter=_FakeAdapter())

    assert blocks is not None
    select_block = next(
        block for block in blocks
        if block.get("block_id") == "email_unsubscriber_selection"
    )
    assert select_block["type"] == "input"
    assert select_block["label"]["text"] == "Select senders to unsubscribe"
    select = select_block["element"]
    assert select["type"] == "multi_static_select"
    assert select["action_id"] == si.ACTION_SELECT
    option = select["options"][0]
    assert option["text"]["text"] == "news@example.com: Sale"
    assert option["value"] == "u-abc123"
    assert "description" not in option
    assert any(
        el.get("action_id") == si.ACTION_UNSUBSCRIBE_SELECTED
        for block in blocks if block.get("type") == "actions"
        for el in block.get("elements", [])
    )


def test_slack_report_still_parses_legacy_via_domain_lines():
    content = "\n".join([
        "# Gmail Trash Auto-Unsubscriber",
        "",
        "- `u-abc123` **news@example.com** via `example.com` — Sale",
    ])

    blocks = si.build_blocks(content, adapter=_FakeAdapter())

    assert blocks is not None
    select_block = next(
        block for block in blocks
        if block.get("block_id") == "email_unsubscriber_selection"
    )
    assert select_block["element"]["options"][0]["text"]["text"] == "news@example.com: Sale"


def test_slack_report_blocks_ignore_non_unsubscriber_report():
    assert si.build_blocks("ordinary Hermes response", adapter=_FakeAdapter()) is None


@pytest.mark.asyncio
async def test_slack_selected_button_routes_selected_as_one_saved_approval_batch(monkeypatch):
    adapter = _FakeAdapter()
    calls = []
    monkeypatch.setattr(si, "unsubscribe_many", lambda ids: calls.append(list(ids)) or "done selected")

    body = {
        "channel": {"id": "C0B6Q5G9QQG"},
        "message": {"ts": "parent-ts"},
        "state": {
            "values": {
                "email_unsubscriber_selection": {
                    "email_unsubscriber_select": {
                        "selected_options": [{"value": "u-one"}, {"value": "u-two"}],
                    }
                }
            }
        },
    }

    handled = await si.handle_action(
        adapter=adapter,
        body=body,
        action={"action_id": si.ACTION_UNSUBSCRIBE_SELECTED},
    )

    assert handled is True
    assert calls == [["u-one", "u-two"]]
    assert adapter.client.posts == [
        {"channel": "C0B6Q5G9QQG", "thread_ts": "parent-ts", "text": "done selected"}
    ]


@pytest.mark.asyncio
async def test_slack_all_button_routes_all_through_unsubscribe(monkeypatch):
    adapter = _FakeAdapter()
    calls = []
    def fake_unsubscribe(cid, unsubscribe_all=False):
        calls.append((cid, unsubscribe_all))
        return "done all"

    monkeypatch.setattr(si, "unsubscribe", fake_unsubscribe)

    handled = await si.handle_action(
        adapter=adapter,
        body={
            "channel": {"id": "C0B6Q5G9QQG"},
            "message": {"ts": "parent-ts", "thread_ts": "thread-ts"},
            "state": {"values": {}},
        },
        action={"action_id": si.ACTION_UNSUBSCRIBE_ALL},
    )

    assert handled is True
    assert calls == [("all", True)]
    assert adapter.client.posts == [
        {"channel": "C0B6Q5G9QQG", "thread_ts": "thread-ts", "text": "done all"}
    ]


def test_email_slack_integration_registers_actions():
    si.register()
    registered = iter_slack_integrations()
    assert registered == (si.integration,)
    assert registered[0].action_ids == (
        si.ACTION_SELECT,
        si.ACTION_UNSUBSCRIBE_SELECTED,
        si.ACTION_UNSUBSCRIBE_ALL,
    )


def test_slack_extension_registry_registers_and_rejects_duplicate_action_id():
    first = SlackIntegration(name="first", action_ids=("action.one",))
    second = SlackIntegration(name="second", action_ids=("action.one",))

    register_slack_integration(first)
    register_slack_integration(second)

    assert iter_slack_integrations() == (first,)


def test_slack_extension_registry_clear_for_tests():
    register_slack_integration(SlackIntegration(name="first", action_ids=("action.one",)))
    clear_slack_integrations_for_tests()
    assert iter_slack_integrations() == ()
