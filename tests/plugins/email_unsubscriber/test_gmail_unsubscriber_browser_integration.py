"""Opt-in browser integration tests for Gmail browser-assisted unsubscribe.

These tests exercise a real local browser against a local static page. The
first test drives browser tools deterministically; the second is an opt-in live
LLM/browser smoke test that uses the real Gmail browser-agent seam. They are
marked integration and excluded from the default suite by pyproject.toml.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Iterator

import pytest

from .plugin_loader import load_email_unsubscriber_plugin

gu = load_email_unsubscriber_plugin().gmail_unsubscriber


_REAL_HERMES_HOME = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()


HTML = b"""
<!doctype html>
<html>
  <head><title>Newsletter preferences</title></head>
  <body>
    <h1>Newsletter preferences</h1>
    <p>Choose what you want to receive.</p>
    <form>
      <label>
        <input type="checkbox" id="product-updates" name="product_updates">
        Send me product updates
      </label>
      <label>
        <input type="checkbox" id="stop-marketing" name="stop_marketing">
        Stop all marketing emails
      </label>
      <button type="button" id="confirm">Confirm unsubscribe</button>
    </form>
    <p id="status" aria-live="polite"></p>
    <script>
      document.getElementById('confirm').addEventListener('click', () => {
        const stop = document.getElementById('stop-marketing').checked;
        const optedIn = document.getElementById('product-updates').checked;
        const message = stop && !optedIn
          ? 'You are unsubscribed from marketing emails.'
          : 'Selection was not safe.';
        document.getElementById('status').textContent = message;
        fetch('/record?status=' + encodeURIComponent(message)).catch(() => {});
      });
    </script>
  </body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/record":
            status = urllib.parse.parse_qs(parsed.query).get("status", [""])[0]
            self.server.events.append(status)  # type: ignore[attr-defined]
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path != "/unsubscribe-test":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib API
        return


@pytest.fixture
def local_unsubscribe_page() -> Iterator[SimpleNamespace]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.events = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield SimpleNamespace(url=f"http://{host}:{port}/unsubscribe-test", events=server.events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture(autouse=True)
def hermes_home(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker("live_llm_browser") is None:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        yield
        return

    # The live LLM smoke test intentionally uses the user's configured Hermes
    # provider/auth instead of an empty test home. Keep only the Gmail
    # unsubscriber state isolated so the test cannot modify the real
    # email-unsubscriber state file. This is opt-in and may refresh the user's
    # normal Hermes auth, matching a real `hermes` run.
    state_root = tmp_path / "email-unsubscriber"
    monkeypatch.setattr(gu, "state_dir", lambda: state_root)

    if os.getenv("HERMES_RUN_LIVE_LLM_BROWSER_TESTS") != "1":
        # Keep the default hermetic home for the skipped live test so merely
        # collecting/running integration tests never depends on credentials.
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        yield
        return

    if not _REAL_HERMES_HOME.exists():
        pytest.skip(f"configured Hermes home not found: {_REAL_HERMES_HOME}")

    monkeypatch.setenv("HERMES_HOME", str(_REAL_HERMES_HOME))
    # hermes_cli.auth refuses to read the real auth store while pytest is marked
    # in the environment. This live smoke test is explicitly opted in, so remove
    # the marker for this test only and use the same auth path as real Hermes.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    yield


def _candidate(url: str) -> dict[str, str]:
    return {
        "id": "u-browser-page",
        "sender_key": "news@example.com",
        "sender_domain": "example.com",
        "subject": "Newsletter preferences",
        "unsubscribe_url": url,
        "unsubscribe_host": "127.0.0.1",
        "method": "manual_review",
        "status": "pending",
    }


def _load_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive assertion helper
        raise AssertionError(f"browser tool returned non-JSON: {raw[:500]}") from exc


def _snapshot_text(response: dict) -> str:
    return str(response.get("snapshot") or response.get("data", {}).get("snapshot") or "")


def _find_ref(snapshot: str, label: str) -> str:
    needle = label.lower()
    for line in snapshot.splitlines():
        if needle in line.lower():
            match = re.search(r"\bref=([^,\]\s]+)", line)
            if match:
                return "@" + match.group(1).lstrip("@")
    raise AssertionError(f"Could not find browser ref for {label!r} in snapshot:\n{snapshot}")


def _allow_exact_local_url(monkeypatch, url: str) -> None:
    real_safe = gu.is_safe_https_url

    def test_only_safe(candidate_url: str) -> bool:
        return candidate_url == url or real_safe(candidate_url)

    monkeypatch.setattr(gu, "is_safe_https_url", test_only_safe)


def _force_local_browser_backend(monkeypatch) -> None:
    from tools import browser_tool

    # Force local browser mode for this localhost-only fixture without relaxing
    # production Gmail URL safety or global browser SSRF settings.
    monkeypatch.setattr(browser_tool, "_get_cloud_provider", lambda: None)
    monkeypatch.setattr(browser_tool, "_cached_cloud_provider", None, raising=False)
    monkeypatch.setattr(browser_tool, "_cloud_provider_resolved", True, raising=False)


def _wait_for_recorded_event(page: SimpleNamespace, needle: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(needle in event for event in page.events):
            return
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {needle!r}; recorded events: {page.events!r}")


@pytest.mark.integration
def test_browser_tool_reviews_local_unsubscribe_page_clicks_checkbox_and_button(monkeypatch, local_unsubscribe_page):
    """A real browser sees a local page, chooses the safe checkbox, and confirms.

    The production LLM remains mocked out; the deterministic replacement uses
    the same browser tools the short-lived Gmail AIAgent would receive. This
    covers the page-review mechanics that unit tests cannot: snapshot refs,
    checkbox click, confirm button click, and confirmation text detection.
    """
    from tools import browser_tool

    _force_local_browser_backend(monkeypatch)
    _allow_exact_local_url(monkeypatch, local_unsubscribe_page.url)

    candidate = _candidate(local_unsubscribe_page.url)
    state = gu.load_state()
    state["pending_candidates"][candidate["id"]] = candidate
    gu.save_state(state)

    actions: list[str] = []

    def deterministic_browser_agent(candidate: dict) -> tuple[bool, str]:
        task_id = "test-gmail-unsubscribe-local-page"
        try:
            nav = _load_json(browser_tool.browser_navigate(candidate["unsubscribe_url"], task_id=task_id))
            if not nav.get("success"):
                pytest.skip(f"browser_navigate unavailable for local integration fixture: {nav}")

            snapshot = _snapshot_text(nav) or _snapshot_text(
                _load_json(browser_tool.browser_snapshot(task_id=task_id))
            )
            assert "Send me product updates" in snapshot
            assert "Stop all marketing emails" in snapshot
            assert "Confirm unsubscribe" in snapshot

            opt_in_ref = _find_ref(snapshot, "Send me product updates")
            stop_ref = _find_ref(snapshot, "Stop all marketing emails")
            confirm_ref = _find_ref(snapshot, "Confirm unsubscribe")

            # The page contains both an opt-in checkbox and an unsubscribe
            # checkbox. The browser review must choose only the unsubscribe
            # control before clicking the confirmation button.
            assert opt_in_ref != stop_ref
            actions.append("did_not_check_product_updates")

            checked = _load_json(browser_tool.browser_click(stop_ref, task_id=task_id))
            assert checked.get("success"), checked
            actions.append("checked_stop_marketing")

            clicked = _load_json(browser_tool.browser_click(confirm_ref, task_id=task_id))
            assert clicked.get("success"), clicked
            actions.append("clicked_confirm_unsubscribe")

            _wait_for_recorded_event(local_unsubscribe_page, "You are unsubscribed")
            assert not any("Selection was not safe" in event for event in local_unsubscribe_page.events)
            return True, "browser_unsubscribed: checked stop marketing and clicked confirm unsubscribe"
        finally:
            try:
                browser_tool.cleanup_all_browsers()
            except Exception:
                pass

    monkeypatch.setattr(gu, "_execute_browser_agent", deterministic_browser_agent)

    text = gu.unsubscribe(candidate["id"])

    assert "Unsubscribed" in text
    assert actions == [
        "did_not_check_product_updates",
        "checked_stop_marketing",
        "clicked_confirm_unsubscribe",
    ]
    saved = gu.load_state()
    saved_candidate = saved["pending_candidates"][candidate["id"]]
    assert saved_candidate["status"] == "unsubscribed"
    assert saved_candidate["result"] == "browser_unsubscribed: checked stop marketing and clicked confirm unsubscribe"
    assert "news@example.com" in saved["unsubscribed_senders"]


@pytest.mark.integration
@pytest.mark.live_llm_browser
def test_live_llm_browser_agent_reviews_local_unsubscribe_page(monkeypatch, local_unsubscribe_page):
    """Opt-in live LLM/browser smoke test for the real Gmail browser-agent seam.

    Set HERMES_RUN_LIVE_LLM_BROWSER_TESTS=1 and provide normal Hermes model
    credentials to run this. Unlike the deterministic test above, this does not
    replace AIAgent or _execute_browser_agent: the LLM must inspect the page,
    choose the safe checkbox, click Confirm unsubscribe, and report success.
    """
    if os.getenv("HERMES_RUN_LIVE_LLM_BROWSER_TESTS") != "1":
        pytest.skip("set HERMES_RUN_LIVE_LLM_BROWSER_TESTS=1 to spend a live LLM/browser call")

    from tools import browser_tool

    _force_local_browser_backend(monkeypatch)
    _allow_exact_local_url(monkeypatch, local_unsubscribe_page.url)

    candidate = _candidate(local_unsubscribe_page.url)
    state = gu.load_state()
    state["pending_candidates"][candidate["id"]] = candidate
    gu.save_state(state)

    try:
        # Pytest re-adds PYTEST_CURRENT_TEST for the call phase after fixtures
        # run. Remove it immediately before invoking AIAgent so Hermes auth uses
        # the same real configured provider path as a normal `hermes` command.
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        text = gu.unsubscribe(candidate["id"])
        assert "Unsubscribed" in text, text
        _wait_for_recorded_event(local_unsubscribe_page, "You are unsubscribed", timeout=20.0)
    finally:
        try:
            browser_tool.cleanup_all_browsers()
        except Exception:
            pass

    assert not any("Selection was not safe" in event for event in local_unsubscribe_page.events)
    saved = gu.load_state()
    saved_candidate = saved["pending_candidates"][candidate["id"]]
    assert saved_candidate["status"] == "unsubscribed"
    assert saved_candidate["result"].startswith("browser_unsubscribed:")
    assert "news@example.com" in saved["unsubscribed_senders"]
