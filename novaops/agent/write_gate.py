"""Persisted, exact-scope authorization for graph write-tool visibility."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


class ApprovalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS workflow_approvals (
                    thread_id TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK (scope IN ('maya', 'webex')),
                    employee_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    system_name TEXT NOT NULL,
                    access_level TEXT NOT NULL,
                    business_justification TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('approved', 'rejected')),
                    approver_employee_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (
                        thread_id, scope, employee_id, tool_name, system_name,
                        access_level, business_justification
                    )
                )
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record_decision(
        self,
        *,
        thread_id: str,
        scope: str,
        employee_id: str,
        tool_name: str,
        system_name: str,
        access_level: str,
        business_justification: str,
        status: str,
        approver_employee_id: str,
        reason: str,
    ) -> None:
        values = (
            thread_id,
            scope,
            employee_id,
            tool_name,
            system_name,
            access_level,
            business_justification,
            status,
            approver_employee_id,
            reason,
        )
        if any(not value.strip() for value in values):
            raise ValueError("Approval decisions require every field to be non-empty")
        if scope not in {"maya", "webex"}:
            raise ValueError(f"Unknown approval scope: {scope}")
        if status not in {"approved", "rejected"}:
            raise ValueError("Approval status must be 'approved' or 'rejected'")
        recorded_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO workflow_approvals (
                    thread_id, scope, employee_id, tool_name, system_name,
                    access_level, business_justification, status,
                    approver_employee_id, reason, recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    thread_id, scope, employee_id, tool_name, system_name,
                    access_level, business_justification
                )
                DO UPDATE SET
                    status = excluded.status,
                    approver_employee_id = excluded.approver_employee_id,
                    reason = excluded.reason,
                    recorded_at = excluded.recorded_at
                """,
                (*values, recorded_at),
            )

    def has_approved(
        self,
        *,
        thread_id: str,
        scope: str,
        employee_id: str,
        tool_name: str,
        system_name: str,
        access_level: str,
        business_justification: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM workflow_approvals
                WHERE thread_id = ?
                  AND scope = ?
                  AND employee_id = ?
                  AND tool_name = ?
                  AND system_name = ?
                  AND access_level = ?
                  AND business_justification = ?
                  AND status = 'approved'
                """,
                (
                    thread_id,
                    scope,
                    employee_id,
                    tool_name,
                    system_name,
                    access_level,
                    business_justification,
                ),
            ).fetchone()
        return row is not None

    def get_decision(
        self,
        *,
        thread_id: str,
        scope: str,
        employee_id: str,
        tool_name: str,
        system_name: str,
        access_level: str,
        business_justification: str,
    ) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, approver_employee_id, reason, recorded_at
                FROM workflow_approvals
                WHERE thread_id = ?
                  AND scope = ?
                  AND employee_id = ?
                  AND tool_name = ?
                  AND system_name = ?
                  AND access_level = ?
                  AND business_justification = ?
                """,
                (
                    thread_id,
                    scope,
                    employee_id,
                    tool_name,
                    system_name,
                    access_level,
                    business_justification,
                ),
            ).fetchone()
        return dict(row) if row is not None else None


def can_release_write(
    approval_store: ApprovalStore,
    *,
    thread_id: str,
    scope: str,
    employee_id: str | None,
    tool_name: str,
    system_name: str | None,
    access_level: str | None,
    business_justification: str | None,
) -> bool:
    """The sole authorization check that can release a write tool."""
    if (
        not thread_id
        or not employee_id
        or not system_name
        or not access_level
        or not business_justification
    ):
        return False
    return approval_store.has_approved(
        thread_id=thread_id,
        scope=scope,
        employee_id=employee_id,
        tool_name=tool_name,
        system_name=system_name,
        access_level=access_level,
        business_justification=business_justification,
    )


def compute_write_idempotency_key(
    *,
    thread_id: str,
    scope: str,
    employee_id: str,
    tool_name: str,
    system_name: str,
    access_level: str,
    business_justification: str,
) -> str:
    payload = {
        "thread_id": thread_id,
        "scope": scope,
        "employee_id": employee_id,
        "tool_name": tool_name,
        "system_name": system_name,
        "access_level": access_level,
        "business_justification": business_justification,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
