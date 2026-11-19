"""Persistent SQLite database seeded from the supplied NovaOps dataset."""

from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


class Database:
    def __init__(self, path: Path, dataset_dir: Path) -> None:
        self.path = path
        self.dataset_dir = dataset_dir
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_if_empty()
        self._ensure_runtime_tables()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_if_empty(self) -> None:
        schema_path = self.dataset_dir / "database" / "schema.sql"
        seed_path = self.dataset_dir / "database" / "seed.sql"
        if not schema_path.is_file() or not seed_path.is_file():
            raise FileNotFoundError(
                f"Database schema or seed file is missing under {self.dataset_dir}"
            )

        with self._connect() as connection:
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if existing_tables:
                if existing_tables <= {"workflow_approvals"}:
                    pass
                elif {
                    "employees",
                    "systems",
                    "tickets",
                    "onboarding_tasks",
                } <= existing_tables:
                    employee_count = connection.execute(
                        "SELECT COUNT(*) FROM employees"
                    ).fetchone()[0]
                    if employee_count:
                        return
                    raise RuntimeError(
                        f"Database at {self.path} has an empty employee table; refusing to reseed it"
                    )
                else:
                    raise RuntimeError(
                        f"Database at {self.path} contains an incomplete schema; refusing to reset it"
                    )

            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.executescript(seed_path.read_text(encoding="utf-8"))
            employee_count = connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            if employee_count == 0:
                raise RuntimeError(f"Database seed at {seed_path} did not create any employees")

    def _ensure_runtime_tables(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS write_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def fetch_all(
        self, query: str, parameters: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(query, tuple(parameters)).fetchall()
            ]

    def fetch_one(
        self, query: str, parameters: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(query, tuple(parameters)).fetchone()
            return dict(row) if row is not None else None

    def is_manager(self, employee_id: str) -> bool:
        return (
            self.fetch_one(
                "SELECT 1 FROM employees WHERE manager_id = ? LIMIT 1", (employee_id,)
            )
            is not None
        )

    def belongs_to_group(self, employee_id: str, group_id: str) -> bool:
        return (
            self.fetch_one(
                "SELECT 1 FROM employees WHERE employee_id = ? AND user_group_id = ?",
                (employee_id, group_id),
            )
            is not None
        )

    def can_approve_access_request(
        self,
        approver_employee_id: str,
        employee_id: str,
        system_name: str,
        access_level: str,
    ) -> bool:
        approver = self.get_employee(approver_employee_id)
        employee = self.get_employee(employee_id)
        if (
            approver is None
            or employee is None
            or approver["status"] != "active"
            or employee["status"] == "terminated"
        ):
            return False
        if access_level.casefold() in {"admin", "administrator", "privileged_admin"}:
            return (
                approver["user_group_id"] == "UG_IT"
                and approver["role"] == "IT Manager"
            )
        return (
            approver_employee_id == employee["manager_id"]
            and system_name in employee["systems_allowed_by_role"]
        )

    def report_team_webex_seat_assignment_gap(
        self, manager_employee_id: str
    ) -> dict[str, Any]:
        if self.get_employee(manager_employee_id) is None:
            raise ValueError(f"Unknown caller employee id: {manager_employee_id}")
        if not self.is_manager(manager_employee_id):
            raise PermissionError(
                f"Employee {manager_employee_id!r} is not a manager in the employee records"
            )

        subscription = self.fetch_one(
            """
            SELECT s.system_id, s.system_name, sub.seat_limit, sub.active_seats,
                   sub.status AS subscription_status
            FROM software_subscriptions AS sub
            JOIN systems AS s ON s.system_id = sub.system_id
            WHERE s.system_name = ?
            """,
            ("Webex",),
        )
        if subscription is None:
            raise RuntimeError("The supplied database has no Webex subscription record")

        employees = self.fetch_all(
            """
            SELECT employee_id, full_name, role, systems_allowed_by_role
            FROM employees
            WHERE manager_id = ?
            ORDER BY full_name, employee_id
            """,
            (manager_employee_id,),
        )

        team_members: list[dict[str, Any]] = []
        for employee in employees:
            allowed_systems = json.loads(employee["systems_allowed_by_role"])
            if not isinstance(allowed_systems, list) or not all(
                isinstance(system, str) for system in allowed_systems
            ):
                raise RuntimeError(
                    "Invalid systems_allowed_by_role data for "
                    f"employee {employee['employee_id']!r}"
                )

            request = self.fetch_one(
                """
                SELECT request_id, status, access_level, decision_reason
                FROM access_requests
                WHERE employee_id = ? AND system_id = ?
                ORDER BY created_at DESC, request_id DESC
                LIMIT 1
                """,
                (employee["employee_id"], subscription["system_id"]),
            )
            task = self.fetch_one(
                """
                SELECT task_id, status, description
                FROM onboarding_tasks
                WHERE employee_id = ? AND related_system_id = ?
                ORDER BY due_date, task_id
                LIMIT 1
                """,
                (employee["employee_id"], subscription["system_id"]),
            )
            tickets = self.fetch_all(
                """
                SELECT ticket_id, status, subject
                FROM tickets
                WHERE employee_id = ? AND related_system_id = ?
                ORDER BY created_at DESC, ticket_id
                """,
                (employee["employee_id"], subscription["system_id"]),
            )
            team_members.append(
                {
                    "employee_id": employee["employee_id"],
                    "full_name": employee["full_name"],
                    "role": employee["role"],
                    "role_entitled_to_webex": "Webex" in allowed_systems,
                    "seat_assignment": "not_recorded",
                    "access_request": request,
                    "onboarding_task": task,
                    "tickets": tickets,
                }
            )

        return {
            "system_name": subscription["system_name"],
            "seat_limit": subscription["seat_limit"],
            "active_seats": subscription["active_seats"],
            "subscription_status": subscription["subscription_status"],
            "seat_assignments_recorded": False,
            "assignment_gap": (
                "The source database records subscription seat counts and employee role "
                "eligibility, but it does not record employee-to-seat assignments. "
                "Individual seat holders cannot be determined."
            ),
            "team_members": team_members,
        }

    def employee_can_view(self, caller_id: str, subject_id: str) -> bool:
        caller = self.fetch_one(
            "SELECT employee_id, user_group_id FROM employees WHERE employee_id = ?",
            (caller_id,),
        )
        subject = self.fetch_one(
            "SELECT employee_id, manager_id FROM employees WHERE employee_id = ?",
            (subject_id,),
        )
        if caller is None or subject is None:
            return False
        return (
            caller_id == subject_id
            or caller["user_group_id"] in {"UG_HR", "UG_IT"}
            or subject["manager_id"] == caller_id
        )

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        employee = self.fetch_one(
            """
            SELECT e.employee_id, e.full_name, e.email, e.department_id,
                   d.name AS department, e.role, e.manager_id, e.location,
                   e.employment_type, e.status, e.start_date, e.end_date,
                   e.user_group_id, g.name AS user_group, e.systems_allowed_by_role,
                   e.notes
            FROM employees AS e
            JOIN departments AS d ON d.department_id = e.department_id
            JOIN user_groups AS g ON g.user_group_id = e.user_group_id
            WHERE e.employee_id = ?
            """,
            (employee_id,),
        )
        if employee is not None:
            employee["systems_allowed_by_role"] = json.loads(
                employee["systems_allowed_by_role"]
            )
        return employee

    def list_employee_access_requests(
        self, employee_id: str, system_name: str | None = None
    ) -> list[dict[str, Any]]:
        query = """
            SELECT ar.request_id, ar.employee_id, s.system_name, ar.access_level,
                   ar.business_justification, ar.status, ar.approver_id,
                   ar.created_at, ar.decision_reason
            FROM access_requests AS ar
            JOIN systems AS s ON s.system_id = ar.system_id
            WHERE ar.employee_id = ?
        """
        parameters: tuple[Any, ...] = (employee_id,)
        if system_name:
            query += " AND s.system_name = ?"
            parameters += (system_name,)
        query += " ORDER BY ar.created_at DESC, ar.request_id DESC"
        return self.fetch_all(query, parameters)

    def create_access_request(
        self,
        *,
        employee_id: str,
        system_name: str,
        access_level: str,
        business_justification: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {
            "employee_id": employee_id,
            "system_name": system_name,
            "access_level": access_level,
            "business_justification": business_justification,
        }
        payload_hash = _payload_hash(payload)
        _require_idempotency_key(idempotency_key)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _read_idempotent_result(
                connection, idempotency_key, "create_access_request", payload_hash
            )
            if replay is not None:
                return replay
            employee = connection.execute(
                "SELECT manager_id FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
            system = connection.execute(
                "SELECT system_id FROM systems WHERE system_name = ?", (system_name,)
            ).fetchone()
            if employee is None:
                raise ValueError(f"Unknown employee id: {employee_id}")
            if system is None:
                raise ValueError(f"Unknown system: {system_name}")

            existing = connection.execute(
                """
                SELECT ar.request_id, ar.status
                FROM access_requests AS ar
                WHERE ar.employee_id = ? AND ar.system_id = ?
                  AND ar.access_level = ? AND ar.business_justification = ?
                ORDER BY ar.created_at DESC, ar.request_id DESC
                LIMIT 1
                """,
                (
                    employee_id,
                    system["system_id"],
                    access_level,
                    business_justification,
                ),
            ).fetchone()
            if existing is not None:
                result = {
                    "request_id": existing["request_id"],
                    "status": existing["status"],
                    "reused": True,
                }
                _store_idempotent_result(
                    connection,
                    idempotency_key,
                    "create_access_request",
                    payload_hash,
                    result,
                )
                return result

            next_number = connection.execute(
                """
                SELECT COALESCE(MAX(CAST(SUBSTR(request_id, 3) AS INTEGER)), 0) + 1
                FROM access_requests
                WHERE request_id GLOB 'AR[0-9]*'
                """
            ).fetchone()[0]
            request_id = f"AR{next_number:03d}"
            created_at = _utc_timestamp()
            connection.execute(
                """
                INSERT INTO access_requests
                    (request_id, employee_id, system_id, access_level,
                     business_justification, status, approver_id, created_at, decision_reason)
                VALUES (?, ?, ?, ?, ?, 'pending_approval', ?, ?, ?)
                """,
                (
                    request_id,
                    employee_id,
                    system["system_id"],
                    access_level,
                    business_justification,
                    employee["manager_id"],
                    created_at,
                    "Awaiting required approvals; access has not been granted.",
                ),
            )
            result = {
                "request_id": request_id,
                "status": "pending_approval",
                "reused": False,
            }
            _store_idempotent_result(
                connection,
                idempotency_key,
                "create_access_request",
                payload_hash,
                result,
            )
        return result

    def create_ticket(
        self,
        *,
        employee_id: str,
        system_name: str,
        subject: str,
        description: str,
        priority: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {
            "employee_id": employee_id,
            "system_name": system_name,
            "subject": subject,
            "description": description,
            "priority": priority,
        }
        payload_hash = _payload_hash(payload)
        _require_idempotency_key(idempotency_key)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = _read_idempotent_result(
                connection, idempotency_key, "create_ticket", payload_hash
            )
            if replay is not None:
                return replay
            employee_exists = connection.execute(
                "SELECT 1 FROM employees WHERE employee_id = ?", (employee_id,)
            ).fetchone()
            system = connection.execute(
                "SELECT system_id FROM systems WHERE system_name = ?", (system_name,)
            ).fetchone()
            if employee_exists is None:
                raise ValueError(f"Unknown employee id: {employee_id}")
            if system is None:
                raise ValueError(f"Unknown system: {system_name}")
            existing = connection.execute(
                """
                SELECT ticket_id, status FROM tickets
                WHERE employee_id = ? AND related_system_id = ?
                  AND subject = ? AND description = ? AND priority = ?
                ORDER BY created_at DESC, ticket_id DESC
                LIMIT 1
                """,
                (employee_id, system["system_id"], subject, description, priority),
            ).fetchone()
            if existing is not None:
                result = {
                    "ticket_id": existing["ticket_id"],
                    "status": existing["status"],
                    "reused": True,
                }
                _store_idempotent_result(
                    connection,
                    idempotency_key,
                    "create_ticket",
                    payload_hash,
                    result,
                )
                return result
            next_number = connection.execute(
                """
                SELECT COALESCE(MAX(CAST(SUBSTR(ticket_id, 2) AS INTEGER)), 0) + 1
                FROM tickets
                WHERE ticket_id GLOB 'T[0-9]*'
                """
            ).fetchone()[0]
            ticket_id = f"T{next_number:03d}"
            connection.execute(
                """
                INSERT INTO tickets
                    (ticket_id, employee_id, category, subcategory, priority, status,
                     subject, description, created_at, assigned_team, related_system_id)
                VALUES (?, ?, 'Access', ?, ?, 'open', ?, ?, ?, 'IT', ?)
                """,
                (
                    ticket_id,
                    employee_id,
                    system_name,
                    priority,
                    subject,
                    description,
                    _utc_timestamp(),
                    system["system_id"],
                ),
            )
            result = {"ticket_id": ticket_id, "status": "open", "reused": False}
            _store_idempotent_result(
                connection,
                idempotency_key,
                "create_ticket",
                payload_hash,
                result,
            )
        return result


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_idempotency_key(key: str) -> None:
    if not key.strip():
        raise ValueError("A non-empty idempotency key is required for writes")


def _read_idempotent_result(
    connection: sqlite3.Connection,
    idempotency_key: str,
    tool_name: str,
    payload_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT tool_name, payload_hash, result_json
        FROM write_idempotency WHERE idempotency_key = ?
        """,
        (idempotency_key,),
    ).fetchone()
    if row is None:
        return None
    if row["tool_name"] != tool_name or row["payload_hash"] != payload_hash:
        raise ValueError("Idempotency key was already used for a different write")
    return json.loads(row["result_json"])


def _store_idempotent_result(
    connection: sqlite3.Connection,
    idempotency_key: str,
    tool_name: str,
    payload_hash: str,
    result: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO write_idempotency (
            idempotency_key, tool_name, payload_hash, result_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            idempotency_key,
            tool_name,
            payload_hash,
            json.dumps(result, sort_keys=True),
            _utc_timestamp(),
        ),
    )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
