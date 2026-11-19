"""MCP tool implementations backed by the seeded database and document index."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novaops.agent.write_gate import ApprovalStore, can_release_write
from novaops.core.database import Database
from novaops.retrieval.embeddings import BedrockEmbedder
from novaops.retrieval.store import FaissDocumentStore, RetrievalResult


class NovaOpsTools:
    def __init__(
        self,
        database: Database,
        document_store: FaissDocumentStore,
        embedder: BedrockEmbedder,
        data_dir: Path,
        approval_store: ApprovalStore,
    ) -> None:
        self.database = database
        self.document_store = document_store
        self.embedder = embedder
        self.data_dir = data_dir
        self.approval_store = approval_store

    def get_employee(self, employee_id: str, caller_employee_id: str) -> dict[str, Any]:
        employee_id = self._resolve_employee_id(employee_id)
        self._require_employee_access(caller_employee_id, employee_id)
        employee = self.database.get_employee(employee_id)
        if employee is None:
            raise ValueError(f"Unknown employee id: {employee_id}")
        return employee

    def check_software_subscription(self, system_name: str) -> dict[str, Any]:
        subscription = self.database.fetch_one(
            """
            SELECT s.system_id, s.system_name, sub.seat_limit, sub.active_seats, sub.renewal_date,
                   sub.status, sub.annual_cost, d.name AS owner_department
            FROM software_subscriptions AS sub
            JOIN systems AS s ON s.system_id = sub.system_id
            JOIN departments AS d ON d.department_id = sub.owner_department_id
            WHERE s.system_name = ? COLLATE NOCASE
            """,
            (system_name.strip(),),
        )
        if subscription is None:
            raise ValueError(f"No subscription found for system: {system_name}")
        return subscription

    def report_team_webex_seat_assignment_gap(
        self, caller_employee_id: str
    ) -> dict[str, Any]:
        return self.database.report_team_webex_seat_assignment_gap(
            caller_employee_id
        )

    def list_employee_tickets(
        self, employee_id: str, caller_employee_id: str
    ) -> list[dict[str, Any]]:
        self._require_employee_access(caller_employee_id, employee_id)
        return self.database.fetch_all(
            """
            SELECT ticket_id, employee_id, category, subcategory, priority, status,
                   subject, description, created_at, assigned_team
            FROM tickets WHERE employee_id = ? ORDER BY created_at DESC, ticket_id
            """,
            (employee_id,),
        )

    def list_employee_access_requests(
        self,
        employee_id: str,
        caller_employee_id: str,
        system_name: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_employee_access(caller_employee_id, employee_id)
        return self.database.list_employee_access_requests(employee_id, system_name)

    def create_access_request(
        self,
        employee_id: str,
        caller_employee_id: str,
        thread_id: str,
        scope: str,
        system_name: str,
        access_level: str,
        business_justification: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_employee_access(caller_employee_id, employee_id)
        if not access_level.strip() or not business_justification.strip():
            raise ValueError("Access level and business justification are required")
        if scope != "webex" or not can_release_write(
            self.approval_store,
            thread_id=thread_id,
            scope=scope,
            employee_id=employee_id,
            tool_name="create_access_request",
            system_name=system_name,
            access_level=access_level.strip(),
            business_justification=business_justification.strip(),
        ):
            raise PermissionError(
                "Access request write requires a matching persisted approval"
            )
        return self.database.create_access_request(
            employee_id=employee_id,
            system_name=system_name,
            access_level=access_level.strip(),
            business_justification=business_justification.strip(),
            idempotency_key=idempotency_key,
        )

    def search_knowledge_base(
        self, query: str, caller_employee_id: str | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        is_manager = self._caller_is_manager(caller_employee_id)
        return _serialize_results(
            self.document_store.search(
                query,
                self.embedder,
                is_manager=is_manager,
                limit=limit,
            )
        )

    def search_hr_documents(
        self, query: str, caller_employee_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        if not self.database.belongs_to_group(caller_employee_id, "UG_HR"):
            raise PermissionError("HR documents are restricted to HR Group callers")
        return _serialize_results(
            self.document_store.search(
                query,
                self.embedder,
                is_manager=False,
                limit=limit,
                source_prefix="documents/employment/",
            )
        )

    def list_policies(self) -> list[dict[str, str]]:
        policy_dir = self.data_dir / "documents" / "policies"
        if not policy_dir.is_dir():
            raise FileNotFoundError(f"Policy directory does not exist: {policy_dir}")
        return [
            {"name": path.stem, "source": path.relative_to(self.data_dir).as_posix()}
            for path in sorted(policy_dir.glob("*.md"))
        ]

    def get_policy(self, policy_name: str) -> dict[str, str]:
        matches = [
            path
            for path in (self.data_dir / "documents" / "policies").glob("*.md")
            if policy_name in {path.stem, path.name}
        ]
        if not matches:
            available = sorted(
                path.stem
                for path in (self.data_dir / "documents" / "policies").glob("*.md")
            )
            raise ValueError(
                f"Unknown policy: {policy_name}. Available policies: {', '.join(available)}"
            )
        path = matches[0]
        return {
            "name": path.stem,
            "source": path.relative_to(self.data_dir).as_posix(),
            "content": path.read_text(encoding="utf-8"),
        }

    def list_onboarding_tasks(
        self, employee_id: str, caller_employee_id: str
    ) -> list[dict[str, Any]]:
        self._require_employee_access(caller_employee_id, employee_id)
        return self.database.fetch_all(
            """
            SELECT task_id, employee_id, task_type, description, owner_group,
                   status, due_date, related_system_id
            FROM onboarding_tasks
            WHERE employee_id = ?
            ORDER BY due_date, task_id
            """,
            (employee_id,),
        )

    def check_asset_inventory(
        self, employee_id: str, caller_employee_id: str
    ) -> list[dict[str, Any]]:
        self._require_employee_access(caller_employee_id, employee_id)
        employee = self.database.get_employee(employee_id)
        if employee is None:
            raise ValueError(f"Unknown employee id: {employee_id}")
        return self.database.fetch_all(
            """
            SELECT asset_id, employee_id, asset_type, model, status, location,
                   assigned_at, notes
            FROM assets
            WHERE employee_id = ?
               OR (employee_id IS NULL AND status = 'available' AND location = ?)
            ORDER BY asset_type, asset_id
            """,
            (employee_id, employee["location"]),
        )

    def create_ticket(
        self,
        employee_id: str,
        caller_employee_id: str,
        thread_id: str,
        scope: str,
        system_name: str,
        subject: str,
        description: str,
        idempotency_key: str,
        priority: str = "medium",
    ) -> dict[str, Any]:
        self._require_employee_access(caller_employee_id, employee_id)
        if not subject.strip() or not description.strip():
            raise ValueError("Ticket subject and description are required")
        if priority not in {"low", "medium", "high"}:
            raise ValueError("Priority must be low, medium, or high")
        if scope != "webex" or not can_release_write(
            self.approval_store,
            thread_id=thread_id,
            scope=scope,
            employee_id=employee_id,
            tool_name="create_ticket",
            system_name=system_name,
            access_level=priority,
            business_justification=description.strip(),
        ):
            raise PermissionError("Ticket write requires a matching persisted approval")
        return self.database.create_ticket(
            employee_id=employee_id,
            system_name=system_name,
            subject=subject.strip(),
            description=description.strip(),
            priority=priority,
            idempotency_key=idempotency_key,
        )

    def _resolve_employee_id(self, identifier: str) -> str:
        """Accept an employee ID, email, or exact full name; return the employee ID.

        Unresolved identifiers are returned unchanged so the access check answers with
        the same generic refusal and cannot be used to probe who exists.
        """
        row = self.database.fetch_one(
            """
            SELECT employee_id FROM employees
            WHERE employee_id = ? COLLATE NOCASE
               OR email = ? COLLATE NOCASE
               OR full_name = ? COLLATE NOCASE
            """,
            (identifier.strip(),) * 3,
        )
        return row["employee_id"] if row else identifier

    def _require_employee_access(self, caller_id: str, subject_id: str) -> None:
        if not self.database.employee_can_view(caller_id, subject_id):
            raise PermissionError(
                f"Caller {caller_id!r} is not permitted to access employee {subject_id!r}"
            )

    def _caller_is_manager(self, caller_id: str | None) -> bool:
        if caller_id is None:
            return False
        if self.database.get_employee(caller_id) is None:
            raise ValueError(f"Unknown caller employee id: {caller_id}")
        return self.database.is_manager(caller_id)


def _serialize_results(results: list[RetrievalResult]) -> list[dict[str, Any]]:
    return [
        {
            "title": result.chunk.title,
            "source": result.citation,
            "chunk_index": result.chunk.chunk_index,
            "score": result.score,
            "text": result.chunk.text,
        }
        for result in results
    ]
