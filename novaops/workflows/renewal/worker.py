"""Renewal worker: the second container entry point (no HTTP, no HEALTHCHECK).

    python -m novaops.workflows.renewal.worker [--inbox DIR] [--interval 30] [--once]

Each tick:
  1. runs the schedule once per business day (NOVAOPS_RENEWAL_AS_OF pins the date for demos);
  2. consumes internal events from  INBOX/events/*.json   (one event object per file);
  3. consumes supplier replies from INBOX/replies/*.eml.
Processed files move to INBOX/processed/. All state is in SQLite, so a killed worker
restarts where it stopped; a file seen twice is a no-op because events and replies are
de-duplicated by id in the database, not by the file system.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import date
from pathlib import Path
from typing import Any

from novaops.core.config import PROJECT_ROOT, Settings
from novaops.workflows.renewal.service import build_workflow

_stop = False


def _request_stop(signum: int, frame: Any) -> None:  # noqa: ARG001
    global _stop
    _stop = True


def business_date() -> date:
    pinned = os.environ.get("NOVAOPS_RENEWAL_AS_OF")
    return date.fromisoformat(pinned) if pinned else date.today()


def tick(workflow: Any, inbox: Path, last_schedule: date | None) -> tuple[date | None, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    today = business_date()
    if last_schedule != today:
        results.append({"schedule": workflow.run_schedule(today)})
        last_schedule = today
    processed = inbox / "processed"
    for kind, pattern in (("event", "events/*.json"), ("reply", "replies/*.eml")):
        for path in sorted(inbox.glob(pattern)):
            text = path.read_text(encoding="utf-8")
            outcome = workflow.handle_event(json.loads(text)) if kind == "event" else workflow.handle_reply(text)
            results.append({kind: path.name, "outcome": outcome.get("outcome")})
            processed.mkdir(parents=True, exist_ok=True)
            path.replace(processed / path.name)
    workflow.dispatch()
    return last_schedule, results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m novaops.workflows.renewal.worker")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "data" / "renewal.sqlite3")
    parser.add_argument("--inbox", type=Path, default=PROJECT_ROOT / "data" / "renewal-inbox")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    for sub in ("events", "replies"):
        (args.inbox / sub).mkdir(parents=True, exist_ok=True)
    workflow = build_workflow(Settings.from_env(), database_path=args.db)
    last_schedule: date | None = None
    while not _stop:
        last_schedule, results = tick(workflow, args.inbox, last_schedule)
        for result in results:
            print(json.dumps(result, default=str)[:500], flush=True)
        if args.once:
            break
        deadline = time.monotonic() + args.interval
        while not _stop and time.monotonic() < deadline:
            time.sleep(0.5)
    print("renewal worker stopped", flush=True)


if __name__ == "__main__":
    main()
