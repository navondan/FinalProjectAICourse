"""FastMCP server exposing NovaOps database and retrieval tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from novaops.agent.write_gate import ApprovalStore
from novaops.core.config import Settings
from novaops.core.database import Database
from novaops.mcp_server.tools.functions import NovaOpsTools
from novaops.retrieval.embeddings import BedrockEmbedder
from novaops.retrieval.store import FaissDocumentStore

if TYPE_CHECKING:
    from fastmcp import FastMCP


def create_server(
    settings: Settings,
    *,
    database: Database | None = None,
    document_store: FaissDocumentStore | None = None,
    embedder: BedrockEmbedder | None = None,
    approval_store: ApprovalStore | None = None,
) -> FastMCP:
    from fastmcp import FastMCP

    database = database or Database(settings.database_path, settings.data_dir)
    embedder = embedder or BedrockEmbedder(
        settings.bedrock_embedding_model_id, settings.aws_region
    )
    document_store = document_store or FaissDocumentStore.load(
        settings.index_path,
        embedding_model_id=settings.bedrock_embedding_model_id,
    )
    approval_store = approval_store or ApprovalStore(settings.database_path)
    tools = NovaOpsTools(
        database, document_store, embedder, settings.data_dir, approval_store
    )
    server = FastMCP("NovaOps")

    @server.tool
    def get_employee(employee_id: str, caller_employee_id: str) -> dict:
        """Return an employee record the caller is permitted to view."""
        return tools.get_employee(employee_id, caller_employee_id)

    @server.tool
    def check_software_subscription(system_name: str) -> dict:
        """Return the subscription and seat-count record for a system."""
        return tools.check_software_subscription(system_name)

    @server.tool
    def report_team_webex_seat_assignment_gap(caller_employee_id: str) -> dict:
        """Report team eligibility and request evidence without guessing seat assignments."""
        return tools.report_team_webex_seat_assignment_gap(caller_employee_id)

    @server.tool
    def list_employee_tickets(employee_id: str, caller_employee_id: str) -> list[dict]:
        """List tickets visible to the caller for one employee."""
        return tools.list_employee_tickets(employee_id, caller_employee_id)

    @server.tool
    def list_employee_access_requests(
        employee_id: str,
        caller_employee_id: str,
        system_name: str | None = None,
    ) -> list[dict]:
        """List access requests visible to the caller for one employee."""
        return tools.list_employee_access_requests(
            employee_id, caller_employee_id, system_name
        )

    @server.tool
    def create_access_request(
        employee_id: str,
        caller_employee_id: str,
        thread_id: str,
        scope: str,
        system_name: str,
        access_level: str,
        business_justification: str,
        idempotency_key: str,
    ) -> dict:
        """Record a pending access request; this does not grant system access."""
        return tools.create_access_request(
            employee_id,
            caller_employee_id,
            thread_id,
            scope,
            system_name,
            access_level,
            business_justification,
            idempotency_key,
        )

    @server.tool
    def search_knowledge_base(
        query: str, caller_employee_id: str | None = None, limit: int = 5
    ) -> list[dict]:
        """Search documents available to the caller under audience permissions."""
        return tools.search_knowledge_base(query, caller_employee_id, limit)

    @server.tool
    def search_hr_documents(
        query: str, caller_employee_id: str, limit: int = 5
    ) -> list[dict]:
        """Search employment documents; available only to HR Group callers."""
        return tools.search_hr_documents(query, caller_employee_id, limit)

    @server.tool
    def list_policies() -> list[dict[str, str]]:
        """List policy documents and their source paths."""
        return tools.list_policies()

    @server.tool
    def get_policy(policy_name: str) -> dict[str, str]:
        """Return a policy document by exact file name or stem."""
        return tools.get_policy(policy_name)

    @server.tool
    def list_onboarding_tasks(
        employee_id: str, caller_employee_id: str
    ) -> list[dict]:
        """List onboarding tasks visible to the caller for one employee."""
        return tools.list_onboarding_tasks(employee_id, caller_employee_id)

    @server.tool
    def check_asset_inventory(
        employee_id: str, caller_employee_id: str
    ) -> list[dict]:
        """Check assigned assets and available local inventory for an employee."""
        return tools.check_asset_inventory(employee_id, caller_employee_id)

    @server.tool
    def create_ticket(
        employee_id: str,
        caller_employee_id: str,
        thread_id: str,
        scope: str,
        system_name: str,
        subject: str,
        description: str,
        idempotency_key: str,
        priority: str = "medium",
    ) -> dict:
        """Create an IT ticket for an employee."""
        return tools.create_ticket(
            employee_id=employee_id,
            caller_employee_id=caller_employee_id,
            thread_id=thread_id,
            scope=scope,
            system_name=system_name,
            subject=subject,
            description=description,
            idempotency_key=idempotency_key,
            priority=priority,
        )

    @server.custom_route("/health", methods=["GET"])
    async def health(request):  # noqa: ARG001 - Starlette passes the request
        """Liveness for the container HEALTHCHECK: process only, no model or embedding call."""
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok", "service": "novaops-mcp"})

    return server
