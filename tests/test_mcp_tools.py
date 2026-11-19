import asyncio
from pathlib import Path

import pytest

from novaops.core.config import Settings
from novaops.core.database import Database
from novaops.agent.write_gate import ApprovalStore
from novaops.mcp_server.server import create_server
from novaops.mcp_server.tools.functions import NovaOpsTools
from novaops.retrieval.ingest import DocumentChunk
from novaops.retrieval.store import FaissDocumentStore


class FakeEmbedder:
    model_id = "test-embedder"

    def embed(self, text: str) -> list[float]:
        return [float(text.casefold().count("maya")), 1.0]


def test_hr_document_search_is_restricted_to_hr_callers(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    dataset_dir = repository_root / "novaops-enterprise-agent-dataset"
    database = Database(tmp_path / "novaops.sqlite3", dataset_dir)
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [
            DocumentChunk(
                source="documents/employment/maya_cohen_offer_letter.md",
                title="Maya Cohen Offer Letter",
                text="Maya Cohen offer and employment details.",
                audience=None,
                metadata={},
                chunk_index=0,
            )
        ],
        embedder,
    )
    tools = NovaOpsTools(database, store, embedder, dataset_dir, ApprovalStore(database.path))

    assert tools.get_employee("E001", "E004")["full_name"] == "Maya Cohen"
    assert tools.get_employee("maya cohen", "E004")["employee_id"] == "E001"
    with pytest.raises(PermissionError, match="not permitted"):
        tools.get_employee("Nobody Here", "E004")
    with pytest.raises(ValueError, match="Available policies: .*remote_work"):
        tools.get_policy("VPN access")
    assert tools.search_hr_documents("Maya offer", "E004")
    with pytest.raises(PermissionError, match="HR Group"):
        tools.search_hr_documents("Maya offer", "E001")
    with pytest.raises(PermissionError, match="not permitted"):
        tools.get_employee("E010", "E001")


def test_mcp_server_exposes_required_foundation_tools(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    dataset_dir = repository_root / "novaops-enterprise-agent-dataset"
    database = Database(tmp_path / "novaops.sqlite3", dataset_dir)
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [
            DocumentChunk(
                source="documents/handbook/getting-started.md",
                title="Getting Started",
                text="Maya onboarding.",
                audience="all",
                metadata={},
                chunk_index=0,
            )
        ],
        embedder,
    )
    settings = Settings.from_env(load_env_file=False)

    server = create_server(
        settings,
        database=database,
        document_store=store,
        embedder=embedder,
        approval_store=ApprovalStore(database.path),
    )

    expected_tools = {
        "get_employee",
        "check_software_subscription",
        "report_team_webex_seat_assignment_gap",
        "list_employee_tickets",
        "list_employee_access_requests",
        "create_access_request",
        "search_knowledge_base",
        "search_hr_documents",
        "list_policies",
        "get_policy",
        "list_onboarding_tasks",
        "check_asset_inventory",
        "create_ticket",
    }
    available_tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in available_tools} == expected_tools


def test_seat_gap_tool_reports_manager_team_and_denies_non_managers(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    dataset_dir = repository_root / "novaops-enterprise-agent-dataset"
    database = Database(tmp_path / "novaops.sqlite3", dataset_dir)
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [
            DocumentChunk(
                source="documents/handbook/getting-started.md",
                title="Getting Started",
                text="Maya onboarding.",
                audience="all",
                metadata={},
                chunk_index=0,
            )
        ],
        embedder,
    )
    tools = NovaOpsTools(database, store, embedder, dataset_dir, ApprovalStore(database.path))

    report = tools.report_team_webex_seat_assignment_gap("E018")
    assert report["seat_assignments_recorded"] is False
    assert {member["employee_id"] for member in report["team_members"]} == {
        "E001",
        "E003",
        "E010",
    }
    with pytest.raises(PermissionError, match="not a manager"):
        tools.report_team_webex_seat_assignment_gap("E001")


def test_search_uses_manager_relationship_and_keeps_unrestricted_documents(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    dataset_dir = repository_root / "novaops-enterprise-agent-dataset"
    database = Database(tmp_path / "novaops.sqlite3", dataset_dir)
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [
            DocumentChunk(
                source="documents/manager_playbook/giving-feedback.md",
                title="Giving Feedback",
                text="Manager feedback guide for coaching.",
                audience="manager",
                metadata={},
                chunk_index=0,
            ),
            DocumentChunk(
                source="documents/it_kb/vpn_access.md",
                title="VPN Access",
                text="VPN access instructions for employees.",
                audience=None,
                metadata={},
                chunk_index=0,
            ),
        ],
        embedder,
    )
    tools = NovaOpsTools(database, store, embedder, dataset_dir, ApprovalStore(database.path))

    employee_results = tools.search_knowledge_base("manager feedback", "E001", limit=2)
    manager_results = tools.search_knowledge_base("manager feedback", "E006", limit=2)
    general_results = tools.search_knowledge_base("VPN", "E001", limit=2)

    assert all(
        result["source"] != "documents/manager_playbook/giving-feedback.md"
        for result in employee_results
    )
    assert any(
        result["source"] == "documents/manager_playbook/giving-feedback.md"
        for result in manager_results
    )
    assert any(result["source"] == "documents/it_kb/vpn_access.md" for result in general_results)


def test_access_request_tool_requires_persisted_exact_scope_approval(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    dataset_dir = repository_root / "novaops-enterprise-agent-dataset"
    database = Database(tmp_path / "novaops.sqlite3", dataset_dir)
    approvals = ApprovalStore(database.path)
    embedder = FakeEmbedder()
    store = FaissDocumentStore.build(
        [
            DocumentChunk(
                source="documents/handbook/getting-started.md",
                title="Getting Started",
                text="Maya onboarding.",
                audience="all",
                metadata={},
                chunk_index=0,
            )
        ],
        embedder,
    )
    tools = NovaOpsTools(database, store, embedder, dataset_dir, approvals)
    request = {
        "employee_id": "E010",
        "caller_employee_id": "E004",
        "thread_id": "thread-approval-test",
        "scope": "webex",
        "system_name": "Webex",
        "access_level": "standard_user",
        "business_justification": "Customer onboarding calls require Webex.",
        "idempotency_key": "test-access-write-1",
    }

    with pytest.raises(PermissionError, match="persisted approval"):
        tools.create_access_request(**request)
    with pytest.raises(PermissionError, match="persisted approval"):
        tools.create_ticket(
            employee_id="E010",
            caller_employee_id="E004",
            thread_id=request["thread_id"],
            scope="webex",
            system_name="Webex",
            subject="Login issue",
            description="Webex is unavailable.",
            idempotency_key="test-ticket-write-1",
        )

    approvals.record_decision(
        thread_id=request["thread_id"],
        scope=request["scope"],
        employee_id=request["employee_id"],
        tool_name="create_access_request",
        system_name=request["system_name"],
        access_level=request["access_level"],
        business_justification=request["business_justification"],
        status="approved",
        approver_employee_id="E006",
        reason="Authorized for test.",
    )

    created = tools.create_access_request(**request)
    assert created == {
        "request_id": "AR001",
        "status": "blocked",
        "reused": True,
    }

    changed_request = {**request, "business_justification": "A different action."}
    with pytest.raises(PermissionError, match="persisted approval"):
        tools.create_access_request(**changed_request)
