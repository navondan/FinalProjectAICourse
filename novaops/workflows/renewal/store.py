"""Durable renewal records in the shared NovaOps SQLite database.

The persistence contract is the design: every fact the PRD asks a reviewer to see has its
own row, and the *recorded agreement* is kept apart from the *terms in effect*
(`software_subscriptions` / `contracts`) until the scheduled run applies it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from novaops.core.database import Database

RENEWAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS renewal_cases (
    contract_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    recommendation_json TEXT NOT NULL,
    urgent INTEGER NOT NULL,
    owner TEXT,
    reason TEXT NOT NULL,
    next_action TEXT NOT NULL,
    deadline TEXT,
    PRIMARY KEY (contract_id, as_of)
);
CREATE TABLE IF NOT EXISTS renewal_recommendations (
    contract_id TEXT NOT NULL,
    term_end_date TEXT NOT NULL,
    recommendation_json TEXT NOT NULL,
    created_as_of TEXT NOT NULL,
    PRIMARY KEY (contract_id, term_end_date)
);
CREATE TABLE IF NOT EXISTS renewal_runs (
    run_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    subscription_id TEXT,
    approver_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status_reason TEXT NOT NULL,
    next_owner TEXT,
    recommendation_json TEXT NOT NULL,
    requested_scope_json TEXT NOT NULL,
    approved_scope_json TEXT,
    approval_event_id TEXT,
    required_clearances_json TEXT NOT NULL,
    created_as_of TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewal_run_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    cause TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewal_processed_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT,
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewal_decisions (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_employee_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    approval_id TEXT,
    scope_json TEXT,
    reason TEXT,
    channel TEXT,
    decided_at TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS renewal_provider_replies (
    message_id TEXT PRIMARY KEY,
    run_id TEXT,
    fixture_id TEXT,
    received_on TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    extracted_json TEXT,
    verdict TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewal_agreements (
    agreement_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    contract_id TEXT NOT NULL,
    subscription_id TEXT,
    confirmation_id TEXT NOT NULL,
    seat_limit INTEGER NOT NULL,
    annual_cost_usd INTEGER NOT NULL,
    term_start_date TEXT NOT NULL,
    term_end_date TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('confirmed_awaiting_effective_date', 'applied')),
    recorded_on TEXT NOT NULL,
    applied_on TEXT,
    audit_event_id TEXT,
    source_message_id TEXT NOT NULL,
    approval_event_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS renewal_outbox (
    message_key TEXT PRIMARY KEY,
    run_id TEXT,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    headers_json TEXT NOT NULL DEFAULT '{}',
    created_on TEXT NOT NULL,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('pending', 'delivered', 'uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    delivered_at TEXT
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class RenewalStore:
    def __init__(self, path: Path, dataset_dir: Path) -> None:
        Database(path, dataset_dir)  # seeds the business tables on first use
        self.path = path
        self.dataset_dir = dataset_dir
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.executescript(RENEWAL_SCHEMA)
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        finally:
            connection.close()

    def all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.transaction() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def one(self, query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.all(query, parameters)
        return rows[0] if rows else None

    # -- run state ---------------------------------------------------------------

    @staticmethod
    def move_run(
        connection: sqlite3.Connection,
        run_id: str,
        *,
        to_stage: str,
        reason: str,
        next_owner: str | None,
        business_date: str,
        cause: str,
        **columns: Any,
    ) -> None:
        current = connection.execute(
            "SELECT stage FROM renewal_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if current is None:
            raise KeyError(run_id)
        assignments = ["stage = ?", "status_reason = ?", "next_owner = ?", "updated_at = ?"]
        values: list[Any] = [to_stage, reason, next_owner, utc_now()]
        for column, value in columns.items():
            assignments.append(f"{column} = ?")
            values.append(value)
        connection.execute(
            f"UPDATE renewal_runs SET {', '.join(assignments)} WHERE run_id = ?",
            (*values, run_id),
        )
        connection.execute(
            """INSERT INTO renewal_run_transitions
               (run_id, business_date, from_stage, to_stage, cause, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, business_date, current["stage"], to_stage, cause, utc_now()),
        )

    @staticmethod
    def enqueue(
        connection: sqlite3.Connection,
        *,
        message_key: str,
        run_id: str | None,
        channel: str,
        recipient: str,
        subject: str,
        body: str,
        created_on: str,
        headers: dict[str, str] | None = None,
    ) -> bool:
        """Insert once; a replay of the same business effect cannot create a second message."""
        cursor = connection.execute(
            """INSERT OR IGNORE INTO renewal_outbox
               (message_key, run_id, channel, recipient, subject, body, headers_json,
                created_on, delivery_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (message_key, run_id, channel, recipient, subject, body, dumps(headers or {}), created_on),
        )
        return cursor.rowcount == 1
