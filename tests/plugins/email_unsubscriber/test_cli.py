import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

from .plugin_loader import load_email_unsubscriber_plugin


def _load_cli_module():
    return load_email_unsubscriber_plugin().cli


def test_setup_writes_wrapper_and_upserts_cron(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    from cron import jobs
    cli = _load_cli_module()

    cron_dir = home / "cron"
    monkeypatch.setattr(jobs, "HERMES_DIR", home)
    monkeypatch.setattr(jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", cron_dir / "output")

    cli.setup_command(None)
    cli.setup_command(None)

    wrapper = home / "scripts" / "email-unsubscriber.py"
    assert wrapper.exists()
    wrapper_source = wrapper.read_text(encoding="utf-8")
    assert "email_assistant" not in wrapper_source
    assert "hermes_plugins.email_unsubscriber.gmail_unsubscriber" in wrapper_source
    installed = [j for j in jobs.load_jobs() if j.get("name") == cli.JOB_NAME]
    assert len(installed) == 1
    assert installed[0]["no_agent"] is True
    assert installed[0]["script"] == "email-unsubscriber.py"
    assert installed[0]["deliver"] == "slack:C0B6Q5G9QQG"


def test_unsubscribe_command_routes_existing_action(monkeypatch, capsys):
    plugin = load_email_unsubscriber_plugin()
    cli = plugin.cli
    gu = plugin.gmail_unsubscriber

    called = {}

    def fake_unsubscribe(target, *, unsubscribe_all=False):
        called["target"] = target
        called["unsubscribe_all"] = unsubscribe_all
        return "ok"

    monkeypatch.setattr(gu, "unsubscribe", fake_unsubscribe)

    assert cli.unsubscribe_command(SimpleNamespace(candidate_id="all")) == 0

    assert called == {"target": "all", "unsubscribe_all": True}
    assert capsys.readouterr().out.strip() == "ok"


def test_main_py_does_not_hardcode_email_unsubscriber_command():
    source = Path("hermes_cli/main.py").read_text(encoding="utf-8")
    assert "email-unsubscriber" not in source
    assert "email_unsubscriber" not in source


def test_email_unsubscriber_parser_does_not_advertise_review_command():
    cli = _load_cli_module()

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    cli.register_parser(subparsers)
    email_parser = subparsers.choices["email-unsubscriber"]
    email_subparsers = next(
        action for action in email_parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert "unsubscribe" in email_subparsers.choices
    assert "review" not in email_subparsers.choices


def test_email_unsubscriber_plugin_registers_cli_command_without_hermes_cli_import():
    source = Path("plugins/email-unsubscriber/__init__.py").read_text(encoding="utf-8")
    assert "hermes_cli.email_unsubscriber" not in source
    assert "from .cli import email_unsubscriber_command, register_cli" in source
    assert "from .slack_integration import register as register_slack_integration" in source

    class Ctx:
        def __init__(self):
            self.commands = []

        def register_cli_command(self, **kwargs):
            self.commands.append(kwargs)

    sys.modules.pop("hermes_plugins.email_unsubscriber", None)
    module = load_email_unsubscriber_plugin()

    ctx = Ctx()
    module.register(ctx)
    assert ctx.commands[0]["name"] == "email-unsubscriber"
    assert ctx.commands[0]["setup_fn"] is module.register_cli
    assert ctx.commands[0]["handler_fn"] is module.email_unsubscriber_command
