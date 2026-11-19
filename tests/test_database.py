from pathlib import Path

import pytest

from novaops.agent.write_gate import ApprovalStore
from novaops.core.database import Database


def test_database_seeds_from_the_supplied_fixture_and_reopens_without_reset(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    dataset_dir = repository_root / "novaops-enterprise-agent-dataset"
    database_path = tmp_path / "novaops.sqlite3"

    database = Database(database_path, dataset_dir)
    first_employee_count = database.fetch_one("SELECT COUNT(*) AS count FROM employees")
    first_ticket_count = database.fetch_one("SELECT COUNT(*) AS count FROM tickets")
    reopened = Database(database_path, dataset_dir)
    second_employee_count = reopened.fetch_one("SELECT COUNT(*) AS count FROM employees")
    second_ticket_count = reopened.fetch_one("SELECT COUNT(*) AS count FROM tickets")

    assert first_employee_count == {"count": 20}
    assert first_ticket_count == {"count": 6}
    assert second_employee_count == first_employee_count
    assert second_ticket_count == first_ticket_count


def test_database_initialization_preserves_early_approval_store(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "novaops.sqlite3"
    approvals = ApprovalStore(database_path)
    approvals.record_decision(
        thread_id="thread-1",
        scope="webex",
        employee_id="E010",
        tool_name="create_access_request",
        system_name="Webex",
        access_level="standard_user",
        business_justification="Customer onboarding calls require Webex.",
        status="approved",
        approver_employee_id="E006",
        reason="Test approval.",
    )

    database = Database(
        database_path,
        repository_root / "novaops-enterprise-agent-dataset",
    )

    assert database.fetch_one("SELECT COUNT(*) AS count FROM employees") == {
        "count": 20
    }
    assert approvals.has_approved(
        thread_id="thread-1",
        scope="webex",
        employee_id="E010",
        tool_name="create_access_request",
        system_name="Webex",
        access_level="standard_user",
        business_justification="Customer onboarding calls require Webex.",
    )


def test_manager_status_comes_from_manager_id_not_user_group(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database = Database(
        tmp_path / "novaops.sqlite3",
        repository_root / "novaops-enterprise-agent-dataset",
    )

    assert database.is_manager("E018")
    assert not database.is_manager("E001")
    assert database.get_employee("E018")["user_group"] == "Regular Employee"
    assert database.employee_can_view("E004", "E001")
    assert database.employee_can_view("E018", "E010")
    assert not database.employee_can_view("E001", "E010")


def test_webex_seat_gap_reports_evidence_without_inventing_assignments(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database = Database(
        tmp_path / "novaops.sqlite3",
        repository_root / "novaops-enterprise-agent-dataset",
    )

    report = database.report_team_webex_seat_assignment_gap("E018")
    team = {member["employee_id"]: member for member in report["team_members"]}

    assert report["seat_limit"] == 40
    assert report["active_seats"] == 42
    assert report["seat_assignments_recorded"] is False
    assert "cannot be determined" in report["assignment_gap"]
    assert set(team) == {"E001", "E003", "E010"}
    assert all(member["seat_assignment"] == "not_recorded" for member in team.values())
    assert team["E001"]["role_entitled_to_webex"] is True
    assert team["E001"]["onboarding_task"]["status"] == "blocked"
    assert team["E003"]["role_entitled_to_webex"] is False
    assert team["E010"]["role_entitled_to_webex"] is True
    assert team["E010"]["access_request"]["status"] == "blocked"
    assert team["E010"]["tickets"][0]["ticket_id"] == "T001"


def test_seat_gap_report_requires_a_known_manager(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database = Database(
        tmp_path / "novaops.sqlite3",
        repository_root / "novaops-enterprise-agent-dataset",
    )

    with pytest.raises(PermissionError, match="not a manager"):
        database.report_team_webex_seat_assignment_gap("E001")
    with pytest.raises(ValueError, match="Unknown caller"):
        database.report_team_webex_seat_assignment_gap("missing")


def test_access_and_ticket_writes_are_idempotent(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database = Database(
        tmp_path / "novaops.sqlite3",
        repository_root / "novaops-enterprise-agent-dataset",
    )
    initial_access_requests = database.fetch_one(
        "SELECT COUNT(*) AS count FROM access_requests"
    )
    initial_tickets = database.fetch_one("SELECT COUNT(*) AS count FROM tickets")

    access_request = {
        "employee_id": "E001",
        "system_name": "Webex",
        "access_level": "standard_user",
        "business_justification": "Customer onboarding calls require Webex.",
        "idempotency_key": "access-write-1",
    }
    created_request = database.create_access_request(**access_request)
    replayed_request = database.create_access_request(**access_request)
    reused_request = database.create_access_request(
        **{**access_request, "idempotency_key": "access-write-2"}
    )

    ticket = {
        "employee_id": "E001",
        "system_name": "Webex",
        "subject": "Webex license unavailable",
        "description": "Webex reports that the account is not licensed.",
        "priority": "high",
        "idempotency_key": "ticket-write-1",
    }
    created_ticket = database.create_ticket(**ticket)
    replayed_ticket = database.create_ticket(**ticket)

    assert created_request == replayed_request
    assert created_request["status"] == "pending_approval"
    assert created_request["request_id"] == reused_request["request_id"]
    assert reused_request["reused"] is True
    assert created_ticket == replayed_ticket
    assert created_ticket["ticket_id"] == "T007"
    assert database.fetch_one("SELECT COUNT(*) AS count FROM access_requests") == {
        "count": initial_access_requests["count"] + 1
    }
    assert database.fetch_one("SELECT COUNT(*) AS count FROM tickets") == {
        "count": initial_tickets["count"] + 1
    }
    with pytest.raises(ValueError, match="different write"):
        database.create_ticket(**{**ticket, "subject": "Changed subject"})


def test_webex_approval_authority_comes_from_manager_and_role_records(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    database = Database(
        tmp_path / "novaops.sqlite3",
        repository_root / "novaops-enterprise-agent-dataset",
    )

    assert database.can_approve_access_request(
        "E018", "E001", "Webex", "standard_user"
    )
    assert not database.can_approve_access_request(
        "E004", "E001", "Webex", "standard_user"
    )
    assert database.can_approve_access_request(
        "E006", "E001", "Webex", "admin"
    )
