import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from novaops.agent.graph import decide_agent_approval, invoke_agent
from novaops.agent.mcp_client import MCPToolExecutor
from novaops.agent.write_gate import ApprovalStore
from novaops.core.config import Settings
from novaops.core.database import Database
from novaops.mcp_server.server import create_server
from novaops.retrieval.ingest import DocumentChunk
from novaops.retrieval.store import FaissDocumentStore

DATASET = Path(__file__).resolve().parents[1] / "novaops-enterprise-agent-dataset"


class FakeEmbedder:
    model_id = "test-embedder"

    def embed(self, text: str) -> list[float]:
        return [float(text.casefold().count("webex")), 1.0]


class FakeModel:
    model_id = "test-model"

    def __init__(self, response: str) -> None:
        self.response = response

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> str:
        response = self.response
        self.response = ""
        if not response:
            raise AssertionError("Unexpected additional model call")
        return response


def _classification() -> str:
    return json.dumps(
        {
            "scope": "webex",
            "intent": "access_request",
            "action_requested": True,
            "target_employee_id": "E001",
            "system_name": "Webex",
            "access_level": "standard_user",
            "business_justification": "Customer onboarding calls require Webex.",
        }
    )


def _runtime(tmp_path: Path, *, available_webex_seats: bool):
    settings = replace(
        Settings.from_env(load_env_file=False),
        data_dir=DATASET,
        database_path=tmp_path / "novaops.sqlite3",
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        index_path=tmp_path / "index",
    )
    database = Database(settings.database_path, DATASET)
    if available_webex_seats:
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE software_subscriptions SET active_seats = 39 "
                "WHERE system_id = 'S001'"
            )
    embedder = FakeEmbedder()
    document_store = FaissDocumentStore.build(
        [
            DocumentChunk(
                source="documents/it_kb/webex_license_assignment.md",
                title="Webex License Assignment",
                text=(
                    "Verify manager approval and seat capacity. Do not assign a license "
                    "when active seats exceed the contract limit."
                ),
                audience=None,
                metadata={},
                chunk_index=0,
            )
        ],
        embedder,
    )
    server = create_server(
        settings,
        database=database,
        document_store=document_store,
        embedder=embedder,
        approval_store=ApprovalStore(settings.database_path),
    )
    return settings, database, MCPToolExecutor(server)


def test_webex_idempotent_restart_resume_after_approval(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        settings, database, executor = _runtime(
            tmp_path, available_webex_seats=True
        )
        initial = await invoke_agent(
            settings,
            message=(
                "Request standard Webex access. Customer onboarding calls require Webex."
            ),
            caller_employee_id="E001",
            thread_id="webex-restart-test",
            x_request_id="webex-initial-request",
            model=FakeModel(_classification()),
            tool_executor=executor,
        )

        assert initial["request_id"] == "webex-initial-request"
        assert initial["workflow_request_id"] == "webex-initial-request"
        assert initial["status"] == "needs_human"
        assert initial["access_decision"]["approval_required"] is True
        assert not database.list_employee_access_requests("E001", "Webex")
        assert initial["__interrupt__"]

        resumed = await decide_agent_approval(
            settings,
            thread_id="webex-restart-test",
            approver_employee_id="E018",
            decision="approved",
            reason="Business need confirmed for the role.",
            x_request_id="webex-approval-request",
            model=FakeModel(""),
            tool_executor=executor,
        )

        assert resumed["request_id"] == "webex-approval-request"
        assert resumed["workflow_request_id"] == "webex-initial-request"
        assert resumed["status"] == "pending"
        assert "access was not granted" in resumed["answer"]
        assert resumed["access_decision"]["request_id"] == "AR005"
        assert resumed["selected_tools"].count("create_access_request") == 1
        assert len(database.list_employee_access_requests("E001", "Webex")) == 1
        assert database.fetch_one(
            "SELECT COUNT(*) AS count FROM write_idempotency"
        ) == {"count": 1}

        replay = await invoke_agent(
            settings,
            message=(
                "Request standard Webex access. Customer onboarding calls require Webex."
            ),
            caller_employee_id="E001",
            thread_id="webex-restart-test",
            model=FakeModel(_classification()),
            tool_executor=executor,
        )
        assert replay["status"] == "pending"
        assert replay["access_decision"]["request_id"] == "AR005"
        assert len(database.list_employee_access_requests("E001", "Webex")) == 1

    asyncio.run(run())


def test_webex_over_limit_reuses_blocked_request_without_writing(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        settings, database, executor = _runtime(
            tmp_path, available_webex_seats=False
        )
        before_requests = database.fetch_one(
            "SELECT COUNT(*) AS count FROM access_requests"
        )
        result = await invoke_agent(
            settings,
            message="Request Webex access for my team.",
            caller_employee_id="E018",
            thread_id="webex-capacity-block",
            model=FakeModel(
                json.dumps(
                    {
                        **json.loads(_classification()),
                        "target_employee_id": "E010",
                    }
                )
            ),
            tool_executor=executor,
        )

        assert result["status"] == "blocked"
        assert "42 active seats against a limit of 40" in result["answer"]
        assert "AR001" in result["answer"]
        assert not result.get("__interrupt__")
        after_requests = database.fetch_one(
            "SELECT COUNT(*) AS count FROM access_requests"
        )
        assert after_requests == before_requests
        assert not database.fetch_one(
            "SELECT 1 FROM write_idempotency LIMIT 1"
        )

    asyncio.run(run())


def test_unauthorized_cli_approver_cannot_release_an_interrupted_write(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        settings, _, executor = _runtime(tmp_path, available_webex_seats=True)
        await invoke_agent(
            settings,
            message="Request standard Webex access.",
            caller_employee_id="E001",
            thread_id="webex-unauthorized-approver",
            model=FakeModel(_classification()),
            tool_executor=executor,
        )

        try:
            await decide_agent_approval(
                settings,
                thread_id="webex-unauthorized-approver",
                approver_employee_id="E004",
                decision="approved",
                reason="Test unauthorized approver.",
                model=FakeModel(""),
                tool_executor=executor,
            )
        except PermissionError as exc:
            assert "not authorized" in str(exc)
        else:
            raise AssertionError("Unauthorized approver was accepted")

        database = Database(settings.database_path, DATASET)
        assert not ApprovalStore(settings.database_path).has_approved(
            thread_id="webex-unauthorized-approver",
            scope="webex",
            employee_id="E001",
            tool_name="create_access_request",
            system_name="Webex",
            access_level="standard_user",
            business_justification="Customer onboarding calls require Webex.",
        )
        assert not database.list_employee_access_requests("E001", "Webex")

    asyncio.run(run())


def test_rejected_decision_resumes_without_a_write(tmp_path: Path) -> None:
    async def run() -> None:
        settings, database, executor = _runtime(
            tmp_path, available_webex_seats=True
        )
        await invoke_agent(
            settings,
            message="Request standard Webex access.",
            caller_employee_id="E001",
            thread_id="webex-rejected-decision",
            model=FakeModel(_classification()),
            tool_executor=executor,
        )

        rejected = await decide_agent_approval(
            settings,
            thread_id="webex-rejected-decision",
            approver_employee_id="E018",
            decision="rejected",
            reason="The business need was not confirmed.",
            model=FakeModel(""),
            tool_executor=executor,
        )

        assert rejected["status"] == "blocked"
        assert "rejected this request" in rejected["answer"]
        assert not database.list_employee_access_requests("E001", "Webex")
        assert database.fetch_one(
            "SELECT COUNT(*) AS count FROM write_idempotency"
        ) == {"count": 0}

    asyncio.run(run())
