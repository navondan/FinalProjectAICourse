"""HTTP entry point for the shared agent (Maya + Webex).

    uvicorn novaops.api.app:create_app --factory --host 0.0.0.0 --port 8000

    GET  /health     liveness: this process answers HTTP. Nothing else.
    GET  /ready      readiness: database seeded, FAISS index present, MCP answering.
    POST /chat       one agent turn on one thread
    POST /approvals  resume a thread paused for a human approval

Neither probe calls the model: probes run every few seconds forever, and a probe that
spends tokens is a standing bill for information we already have.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from novaops.agent.graph import decide_agent_approval, invoke_agent
from novaops.core.config import Settings
from novaops.core.database import Database

SERVICE_NAME = "novaops-agent"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    caller_employee_id: str = Field(pattern=r"^E\d{3}$")
    thread_id: str = Field(min_length=1, max_length=200)


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    approver_employee_id: str = Field(pattern=r"^E\d{3}$")
    decision: Literal["approved", "rejected"]
    reason: str = Field(min_length=1, max_length=2000)


class TurnResponse(BaseModel):
    request_id: str | None
    status: str
    answer: str


def mcp_health_url(mcp_url: str) -> str:
    parts = urlsplit(mcp_url)
    return urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


def create_app(
    settings: Settings | None = None,
    *,
    agent_runner: Any = invoke_agent,
    approval_runner: Any = decide_agent_approval,
) -> FastAPI:
    settings = settings or Settings.from_env()
    timeout_seconds = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120"))
    capacity = asyncio.Semaphore(int(os.environ.get("MAX_CONCURRENCY", "4")))
    Database(settings.database_path, settings.data_dir)
    app = FastAPI(title="NovaOps Company Brain", version="1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness. Deliberately ignores dependencies: restarting this process will not
        fix an MCP outage, and a dependency-aware liveness probe turns one into a crash loop."""
        return {"status": "ok", "service": SERVICE_NAME}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        """Readiness: can a turn be served right now? Checked every call, so it never latches green."""
        problems: list[str] = []
        if not settings.database_path.exists():
            problems.append("database missing")
        if not settings.index_path.exists():
            problems.append("FAISS index missing")
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(mcp_health_url(settings.mcp_url))
                response.raise_for_status()
        except httpx.HTTPError as exc:
            problems.append(f"MCP unreachable: {type(exc).__name__}")
        if problems:
            raise HTTPException(status_code=503, detail=problems)
        return {"status": "ready", "service": SERVICE_NAME, "mcp_server": settings.mcp_url}

    async def bounded(turn: Awaitable[dict[str, Any]]) -> TurnResponse:
        try:
            async with capacity:
                async with asyncio.timeout(timeout_seconds):
                    result = await turn
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="agent turn timed out") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return TurnResponse(
            request_id=result.get("request_id"),
            status=str(result.get("status", "unknown")),
            answer=str(result.get("answer", "")),
        )

    @app.post("/chat", response_model=TurnResponse)
    async def chat(body: ChatRequest, x_request_id: str | None = Header(default=None)) -> TurnResponse:
        return await bounded(
            agent_runner(
                settings,
                message=body.message,
                caller_employee_id=body.caller_employee_id,
                thread_id=body.thread_id,
                x_request_id=x_request_id,
            )
        )

    @app.post("/approvals", response_model=TurnResponse)
    async def approvals(body: ApprovalRequest, x_request_id: str | None = Header(default=None)) -> TurnResponse:
        return await bounded(
            approval_runner(
                settings,
                thread_id=body.thread_id,
                approver_employee_id=body.approver_employee_id,
                decision=body.decision,
                reason=body.reason,
                x_request_id=x_request_id,
            )
        )

    return app
