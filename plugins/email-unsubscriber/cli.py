"""CLI for the Gmail Trash auto-unsubscriber."""

from __future__ import annotations

import os
import argparse
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

JOB_NAME = "Gmail Trash Auto-Unsubscriber"
SCRIPT_NAME = "email-unsubscriber.py"
SLACK_DELIVER = "slack:C0B6Q5G9QQG"
DAILY_SCHEDULE = "0 9 * * *"


def _wrapper_path() -> Path:
    return get_hermes_home() / "scripts" / SCRIPT_NAME


def _write_wrapper() -> Path:
    path = _wrapper_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from hermes_cli.plugins import discover_plugins\n"
        "discover_plugins()\n"
        "from hermes_plugins.email_unsubscriber.gmail_unsubscriber import main\n"
        "raise SystemExit(main(['run']))\n",
        encoding="utf-8",
    )
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _upsert_cron_job() -> dict[str, Any]:
    from cron.jobs import create_job, load_jobs, update_job

    for job in load_jobs():
        if job.get("name") == JOB_NAME:
            updated = update_job(
                job["id"],
                {
                    "prompt": "",
                    "schedule": DAILY_SCHEDULE,
                    "script": SCRIPT_NAME,
                    "no_agent": True,
                    "deliver": SLACK_DELIVER,
                    "enabled": True,
                    "state": "scheduled",
                },
            )
            return updated or job

    return create_job(
        prompt="",
        schedule=DAILY_SCHEDULE,
        name=JOB_NAME,
        script=SCRIPT_NAME,
        no_agent=True,
        deliver=SLACK_DELIVER,
    )


def setup_command(args) -> int:
    wrapper = _write_wrapper()
    job = _upsert_cron_job()
    print("Gmail Trash Auto-Unsubscriber is set up.")
    print(f"Wrapper: {wrapper}")
    print(f"Cron job: {job.get('name')} ({job.get('id')})")
    print(f"Schedule: {job.get('schedule_display', DAILY_SCHEDULE)}")
    print(f"Deliver: {SLACK_DELIVER}")
    print("Run Google Workspace auth if needed, then start the gateway scheduler.")
    return 0


def run_command(args) -> int:
    from .gmail_unsubscriber import DEFAULT_MAX_MESSAGES, scan_trash

    result = scan_trash(
        max_messages=getattr(args, "max", None) or DEFAULT_MAX_MESSAGES,
        backfill=not getattr(args, "no_backfill", False),
        dry_run=True,
    )
    print(result.markdown)
    return 0


def status_command(args) -> int:
    from .gmail_unsubscriber import status_text

    print(status_text())
    try:
        from cron.jobs import load_jobs

        job = next((j for j in load_jobs() if j.get("name") == JOB_NAME), None)
        if job:
            print(f"Cron job: {job.get('id')} next={job.get('next_run_at')} deliver={job.get('deliver')}")
        else:
            print("Cron job: not installed")
    except Exception as exc:
        print(f"Cron job: unknown ({exc})")
    return 0


def unsubscribe_command(args) -> int:
    from .gmail_unsubscriber import unsubscribe

    target = args.candidate_id
    print(unsubscribe(target, unsubscribe_all=target.lower() == "all"))
    return 0


def ignore_command(args) -> int:
    from .gmail_unsubscriber import add_ignore, load_state

    if args.ignore_command == "add":
        print(add_ignore(args.kind, args.value))
        return 0
    state = load_state()
    ignore = state.get("ignore", {}) if isinstance(state.get("ignore"), dict) else {}
    print("Ignored senders:")
    for sender in ignore.get("emails", []):
        print(f"- {sender}")
    print("Ignored domains:")
    for domain in ignore.get("domains", []):
        print(f"- {domain}")
    return 0


def email_unsubscriber_command(args) -> int:
    command = getattr(args, "email_unsubscriber_command", None)
    if command == "setup":
        return setup_command(args)
    if command == "run":
        return run_command(args)
    if command == "status":
        return status_command(args)
    if command == "unsubscribe":
        return unsubscribe_command(args)
    if command == "ignore":
        return ignore_command(args)
    print("usage: hermes email-unsubscriber {setup,run,status,unsubscribe,ignore}")
    return 1


def register_cli(parser: argparse.ArgumentParser) -> None:
    """Build the ``hermes email-unsubscriber`` argparse tree."""
    parser.set_defaults(func=email_unsubscriber_command)
    sub = parser.add_subparsers(dest="email_unsubscriber_command")

    sub.add_parser("setup", help="Install the daily Slack-delivered cron job")

    run = sub.add_parser("run", help="Run a dry-run scan now")
    run.add_argument("--max", type=int, default=None, help="Maximum Trash messages to scan")
    run.add_argument("--no-backfill", action="store_true", help="Only consider messages not previously seen")

    sub.add_parser("status", help="Show state and cron status")

    unsubscribe = sub.add_parser("unsubscribe", help="Unsubscribe one candidate id or 'all' with browser assistance")
    unsubscribe.add_argument("candidate_id")

    ignore = sub.add_parser("ignore", help="Manage ignore list")
    ignore_sub = ignore.add_subparsers(dest="ignore_command")
    ignore_add = ignore_sub.add_parser("add", help="Ignore a sender or domain")
    ignore_add.add_argument("kind", choices=["sender", "domain"])
    ignore_add.add_argument("value")
    ignore_sub.add_parser("list", help="List ignored senders/domains")


def register_parser(subparsers) -> None:
    """Compatibility helper for unit tests and ad-hoc parser construction."""
    parser = subparsers.add_parser(
        "email-unsubscriber",
        help="Gmail Trash unsubscribe assistant",
        description=(
            "Scan Gmail Trash, report unsubscribe candidates to Slack, "
            "and unsubscribe safely after confirmation."
        ),
    )
    register_cli(parser)
