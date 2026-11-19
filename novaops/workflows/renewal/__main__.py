"""python -m novaops.workflows.renewal [--db PATH] [--offline] COMMAND

Each command is a separate process over the same SQLite file, which is how a restart
between stages is demonstrated:

    schedule --as-of 2026-07-01
    event [--event-id IA-WEBEX-APPROVE-001 ...] [--file events.jsonl]
    reply (--fixture-id webex_renewal_approved | --seed 7 | --file reply.eml)
    status
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from novaops.core.config import PROJECT_ROOT, Settings
from novaops.workflows.renewal.ports import MockNotificationAdapter, MockProviderEmailAdapter
from novaops.workflows.renewal.service import EVENTS_FILE, build_workflow

DEFAULT_DB = PROJECT_ROOT / "data" / "renewal.sqlite3"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m novaops.workflows.renewal")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite file (seeded from the dataset on first use)")
    parser.add_argument("--offline", action="store_true", help="rule-based recommender, no model calls")
    commands = parser.add_subparsers(dest="command", required=True)
    schedule = commands.add_parser("schedule")
    schedule.add_argument("--as-of", type=date.fromisoformat, required=True)
    event = commands.add_parser("event")
    event.add_argument("--event-id", nargs="*")
    event.add_argument("--file", type=Path)
    reply = commands.add_parser("reply")
    choice = reply.add_mutually_exclusive_group(required=True)
    choice.add_argument("--fixture-id")
    choice.add_argument("--seed", type=int)
    choice.add_argument("--file", type=Path)
    commands.add_parser("status")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    outbox = args.db.with_suffix(".outbox.jsonl")
    notifications = MockNotificationAdapter(outbox_file=outbox)
    provider = MockProviderEmailAdapter(
        settings.data_dir,
        fixture_id=getattr(args, "fixture_id", None),
        seed=getattr(args, "seed", None),
        raw_reply=args.file.read_text(encoding="utf-8") if args.command == "reply" and args.file else None,
        outbox_file=outbox,
    )
    workflow = build_workflow(
        settings, database_path=args.db, notifications=notifications, provider=provider, offline=args.offline
    )

    if args.command == "schedule":
        result = workflow.run_schedule(args.as_of)
    elif args.command == "event":
        source = MockNotificationAdapter.from_jsonl(
            args.file or settings.data_dir / EVENTS_FILE, event_ids=args.event_id or None
        )
        result = [workflow.handle_event(item) for item in source.receive_events()]
    elif args.command == "reply":
        result = workflow.handle_reply(provider.receive_reply())
    else:
        result = workflow.status()
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
