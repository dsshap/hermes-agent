import base64
import copy
import sys
import types

import pytest

from .plugin_loader import load_email_unsubscriber_plugin

gu = load_email_unsubscriber_plugin().gmail_unsubscriber


def _b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _msg(msg_id, sender="News <news@example.com>", subject="Sale", url="https://example.com/unsub/abc", internal="2000"):
    return {
        "id": msg_id,
        "threadId": "t-" + msg_id,
        "internalDate": internal,
        "snippet": "snippet",
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "List-Unsubscribe", "value": f"<{url}>"},
                {"name": "List-Unsubscribe-Post", "value": "List-Unsubscribe=One-Click"},
            ],
            "body": {"data": _b64("hello")},
            "mimeType": "text/plain",
        },
    }


def _body_unsub_msg(msg_id, sender="News <news@example.com>", subject="Prefs", url="https://example.com/unsubscribe", internal="2000"):
    return {
        "id": msg_id,
        "threadId": "t-" + msg_id,
        "internalDate": internal,
        "snippet": "snippet",
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": _b64(f"unsubscribe here: {url}")},
            "mimeType": "text/plain",
        },
    }


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Messages:
    def __init__(self, messages):
        self.messages = {m["id"]: m for m in messages}

    def list(self, **kwargs):
        return _Exec({"messages": [{"id": mid} for mid in self.messages]})

    def get(self, **kwargs):
        return _Exec(self.messages[kwargs["id"]])


class _Users:
    def __init__(self, messages):
        self._messages = _Messages(messages)

    def messages(self):
        return self._messages


class _Service:
    def __init__(self, messages):
        self._users = _Users(messages)

    def users(self):
        return self._users


@pytest.fixture(autouse=True)
def hermes_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    yield


def test_parse_list_unsubscribe_prefers_safe_https():
    candidate, meta = gu.candidate_from_message(_msg("m1"))
    assert candidate["email"] == "news@example.com"
    assert candidate["domain"] == "example.com"
    assert candidate["url"] == "https://example.com/unsub/abc"


def test_rejects_unsafe_unsubscribe_url():
    assert not gu.is_safe_https_url("http://example.com/unsub")
    assert not gu.is_safe_https_url("https://127.0.0.1/unsub")
    assert not gu.is_safe_https_url("javascript:alert(1)")


def test_scan_records_pending_and_seen():
    result = gu.scan_trash(_Service([_msg("m1")]), max_messages=10)
    assert "u-" in result.markdown
    assert "Pending browser-assisted candidates" in result.markdown
    assert " via `" not in result.markdown
    state = gu.load_state()
    assert state["seen"] == ["m1"]
    assert len(state["candidates"]) == 1
    assert len(state["current_batch_ids"]) == 1
    cid = state["current_batch_ids"][0]
    assert f"- `{cid}` **news@example.com** — Sale" in result.markdown


def test_body_https_unsubscribe_is_pending_browser_candidate():
    result = gu.scan_trash(_Service([_body_unsub_msg("m1")]), max_messages=10)

    assert "Needs manual handling" not in result.markdown
    state = gu.load_state()
    cid, candidate = next(iter(state["candidates"].items()))
    assert candidate["url"] == "https://example.com/unsubscribe"
    assert candidate["status"] == "pending"
    assert cid in state["current_batch_ids"]


def test_unsubscribe_records_unsubscribed_sender_via_browser(monkeypatch):
    gu.scan_trash(_Service([_msg("m1")]), max_messages=10)
    cid = next(iter(gu.load_state()["candidates"]))
    monkeypatch.setattr(gu, "_execute_browser_agent", lambda candidate: (True, "browser_unsubscribed: done"))
    monkeypatch.setattr(gu, "_execute_https", lambda candidate: (_ for _ in ()).throw(AssertionError("direct HTTP should not run")))

    text = gu.unsubscribe(cid)

    assert "Unsubscribed" in text
    state = gu.load_state()
    assert cid not in state["candidates"]
    assert "news@example.com" in state["unsubscribed"]
    assert state["unsubscribed"]["news@example.com"]["candidate_id"] == cid


def test_unsubscribe_browser_needs_input_records_manual_review(monkeypatch):
    gu.scan_trash(_Service([_msg("m1")]), max_messages=10)
    cid = next(iter(gu.load_state()["candidates"]))
    monkeypatch.setattr(gu, "_execute_browser_agent", lambda candidate: (False, "browser_needs_input: login required"))

    text = gu.unsubscribe(cid)

    assert "Manual review" in text
    state = gu.load_state()
    assert state["candidates"][cid]["status"] == "manual_review"
    assert state["candidates"][cid]["result"] == "browser_needs_input: login required"
    assert not state["unsubscribed"]


def test_unsubscribe_browser_failure_marks_failed(monkeypatch):
    gu.scan_trash(_Service([_msg("m1")]), max_messages=10)
    cid = next(iter(gu.load_state()["candidates"]))
    monkeypatch.setattr(gu, "_execute_browser_agent", lambda candidate: (False, "browser_failed: no button"))

    text = gu.unsubscribe(cid)

    assert "Failed" in text
    state = gu.load_state()
    assert state["candidates"][cid]["status"] == "failed"
    assert state["candidates"][cid]["result"] == "browser_failed: no button"
    assert not state["unsubscribed"]


def _manual_candidate(cid="u-manual", url="https://example.com/prefs", status="manual_review"):
    return {
        "email": "news@example.com",
        "domain": "example.com",
        "subject": "Prefs",
        "url": url,
        "status": status,
    }


def test_unsubscribe_rejects_unsafe_url_without_browser(monkeypatch):
    state = gu.load_state()
    state["candidates"]["u-manual"] = _manual_candidate(url="https://127.0.0.1/prefs")
    gu.save_state(state)

    def fail_if_called(candidate):
        raise AssertionError("browser agent should not run")

    monkeypatch.setattr(gu, "_execute_browser_agent", fail_if_called)
    text = gu.unsubscribe("u-manual")

    assert "unsafe" in text.lower()
    state = gu.load_state()
    assert state["candidates"]["u-manual"]["status"] == "manual_review"
    assert state["candidates"]["u-manual"]["result"] == "browser_unsafe_url"


def test_unsubscribe_all_uses_browser_for_each_pending_candidate(monkeypatch):
    gu.scan_trash(
        _Service([
            _msg("m1", sender="One <one@example.com>", url="https://one.example/unsub"),
            _msg("m2", sender="Two <two@example.com>", url="https://two.example/unsub"),
        ]),
        max_messages=10,
    )
    calls = []
    saved_snapshots = []
    real_save_state = gu.save_state

    def fake_browser(candidate):
        calls.append(candidate["id"])
        return True, "browser_unsubscribed: done"

    def recording_save_state(state):
        saved_snapshots.append(copy.deepcopy(gu.compact_state(state)))
        real_save_state(state)

    initial = gu.load_state()
    expected = initial["current_batch_ids"]
    monkeypatch.setattr(gu, "_execute_browser_agent", fake_browser)
    monkeypatch.setattr(gu, "save_state", recording_save_state)

    text = gu.unsubscribe("all", unsubscribe_all=True)

    assert "Unsubscribed" in text
    state = gu.load_state()
    assert calls == expected
    assert all(cid not in state["candidates"] for cid in expected)
    assert [saved_snapshots[0]["candidates"][cid]["status"] for cid in expected] == ["approved", "approved"]
    assert expected[0] not in saved_snapshots[1]["candidates"]
    assert saved_snapshots[1]["candidates"][expected[1]]["status"] == "approved"


def test_unsubscribe_all_persists_completed_candidate_before_interruption(monkeypatch):
    gu.scan_trash(
        _Service([
            _msg("m1", sender="One <one@example.com>", url="https://one.example/unsub"),
            _msg("m2", sender="Two <two@example.com>", url="https://two.example/unsub"),
        ]),
        max_messages=10,
    )
    initial = gu.load_state()
    expected = initial["current_batch_ids"]

    def fake_browser(candidate):
        if candidate["id"] == expected[0]:
            return True, "browser_unsubscribed: done"
        raise KeyboardInterrupt

    monkeypatch.setattr(gu, "_execute_browser_agent", fake_browser)

    with pytest.raises(KeyboardInterrupt):
        gu.unsubscribe("all", unsubscribe_all=True)

    state = gu.load_state()
    assert expected[0] not in state["candidates"]
    assert "one@example.com" in state["unsubscribed"]
    assert state["unsubscribed"]["one@example.com"]["candidate_id"] == expected[0]
    assert state["candidates"][expected[1]]["status"] == "approved"
    assert state["candidates"][expected[1]]["approved_at"]


def test_unsubscribe_marks_all_selected_approved_before_browser_work(monkeypatch):
    gu.scan_trash(
        _Service([
            _msg("m1", sender="One <one@example.com>", url="https://one.example/unsub"),
            _msg("m2", sender="Two <two@example.com>", url="https://two.example/unsub"),
        ]),
        max_messages=10,
    )
    initial = gu.load_state()
    ids = initial["current_batch_ids"]

    def fake_browser(candidate):
        raise KeyboardInterrupt

    monkeypatch.setattr(gu, "_execute_browser_agent", fake_browser)

    with pytest.raises(KeyboardInterrupt):
        gu.unsubscribe_many(ids)

    state = gu.load_state()
    assert [state["candidates"][cid]["status"] for cid in ids] == ["approved", "approved"]
    assert all(state["candidates"][cid].get("approved_at") for cid in ids)


def test_scan_does_not_overwrite_approved_candidate():
    msg = _msg("m1", sender="One <one@example.com>", url="https://one.example/unsub")
    gu.scan_trash(_Service([msg]), max_messages=10)
    state = gu.load_state()
    cid = next(iter(state["candidates"]))
    state["candidates"][cid]["status"] = "approved"
    state["candidates"][cid]["approved_at"] = "saved-approval"
    gu.save_state(state)

    gu.scan_trash(_Service([msg]), max_messages=10)

    state = gu.load_state()
    assert state["candidates"][cid]["status"] == "approved"
    assert state["candidates"][cid]["approved_at"] == "saved-approval"


def test_browser_prompt_instructs_agent_to_identify_buttons_and_checkboxes():
    prompt = gu._browser_prompt(_manual_candidate(status="pending"))
    lower = prompt.lower()

    assert "browser snapshots" in lower
    assert "buttons" in lower
    assert "checkboxes" in lower
    assert "click only controls directly related" in lower
    assert "do not opt in" in lower


def test_execute_browser_agent_success_parses_unsubscribed_and_closes_agent(monkeypatch):
    instances = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            instances.append(self)

        def run_conversation(self, prompt, task_id=None):
            self.prompt = prompt
            self.task_id = task_id
            return {"final_response": "UNSUBSCRIBED: confirmation page"}

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=FakeAgent))

    ok, detail = gu._execute_browser_agent(_manual_candidate(status="pending"))

    assert ok is True
    assert detail == "browser_unsubscribed: confirmation page"
    assert instances[0].kwargs["enabled_toolsets"] == ["browser"]
    assert instances[0].kwargs["skip_memory"] is True
    assert instances[0].kwargs["skip_context_files"] is True
    assert instances[0].closed is True


def test_execute_browser_agent_needs_input_parses_manual_review(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def run_conversation(self, prompt, task_id=None):
            return {"final_response": "NEEDS_INPUT: captcha required"}

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=FakeAgent))

    ok, detail = gu._execute_browser_agent(_manual_candidate(status="pending"))

    assert ok is False
    assert detail == "browser_needs_input: captcha required"


def test_execute_browser_agent_failed_parses_failed(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def run_conversation(self, prompt, task_id=None):
            return {"final_response": "FAILED: unsubscribe button missing"}

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=FakeAgent))

    ok, detail = gu._execute_browser_agent(_manual_candidate(status="pending"))

    assert ok is False
    assert detail == "browser_failed: unsubscribe button missing"


def test_execute_browser_agent_rejects_unsafe_url_before_agent_construction(monkeypatch):
    class FailAgent:
        def __init__(self, **kwargs):
            raise AssertionError("AIAgent should not be constructed")

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=FailAgent))

    ok, detail = gu._execute_browser_agent(_manual_candidate(url="https://127.0.0.1/prefs", status="pending"))

    assert ok is False
    assert detail == "unsafe_url"


def test_post_unsubscribe_new_mail_is_flagged(monkeypatch):
    state = gu.load_state()
    state["unsubscribed"]["news@example.com"] = {
        "domain": "example.com",
        "at_ms": 1000,
    }
    gu.save_state(state)

    result = gu.scan_trash(_Service([_msg("m2", internal="2000")]), max_messages=10)

    assert result.post_unsubscribe
    assert "Still receiving mail" in result.markdown


def test_ignore_domain_suppresses_candidate():
    gu.add_ignore("domain", "example.com")
    result = gu.scan_trash(_Service([_msg("m1")]), max_messages=10)
    assert "No new unsubscribe candidates" in result.markdown
    assert not gu.load_state()["candidates"]
