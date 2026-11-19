"""M10: API probes, renewal worker entry point, and the packaging files themselves."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from novaops.api.app import create_app, mcp_health_url
from novaops.core.config import PROJECT_ROOT, Settings


def _settings(tmp_path: Path) -> Settings:
    return replace(
        Settings.from_env(load_env_file=False),
        data_dir=PROJECT_ROOT / "novaops-enterprise-agent-dataset",
        database_path=tmp_path / "api.sqlite3",
        checkpoint_path=tmp_path / "api-checkpoints.sqlite3",
        index_path=tmp_path / "missing-index",
        mcp_url="http://127.0.0.1:9/mcp",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )


def test_health_is_process_only_and_ready_reports_missing_dependencies(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    assert client.get("/health").json() == {"status": "ok", "service": "novaops-agent"}
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "FAISS index missing" in detail
    assert any(item.startswith("MCP unreachable") for item in detail)


def test_chat_and_approval_route_to_the_shared_agent(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_agent(settings: Settings, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"request_id": kwargs.get("x_request_id"), "status": "completed", "answer": "ok"}

    async def fake_approval(settings: Settings, **kwargs: Any) -> dict[str, Any]:
        raise PermissionError("not authorized")

    client = TestClient(create_app(_settings(tmp_path), agent_runner=fake_agent, approval_runner=fake_approval))
    response = client.post(
        "/chat",
        json={"message": "hi", "caller_employee_id": "E010", "thread_id": "t1"},
        headers={"X-Request-ID": "req-1"},
    )
    assert response.json() == {"request_id": "req-1", "status": "completed", "answer": "ok"}
    assert calls[0]["caller_employee_id"] == "E010"
    assert client.post("/chat", json={"message": "hi", "caller_employee_id": "bob", "thread_id": "t"}).status_code == 422
    denied = client.post(
        "/approvals",
        json={"thread_id": "t1", "approver_employee_id": "E010", "decision": "approved", "reason": "x"},
    )
    assert denied.status_code == 403


def test_mcp_health_url_is_derived_from_the_mcp_url() -> None:
    assert mcp_health_url("http://mcp:9876/mcp") == "http://mcp:9876/health"


def test_renewal_worker_tick_runs_schedule_once_and_consumes_inbox(tmp_path: Path, monkeypatch) -> None:
    from novaops.workflows.renewal import worker
    from novaops.workflows.renewal.model import RulesRecommender
    from novaops.workflows.renewal.service import EVENTS_FILE, build_workflow

    monkeypatch.setenv("NOVAOPS_RENEWAL_AS_OF", "2026-07-01")
    settings = _settings(tmp_path)
    workflow = build_workflow(settings, database_path=tmp_path / "r.sqlite3", recommender=RulesRecommender(), offline=True)
    inbox = tmp_path / "inbox"
    (inbox / "events").mkdir(parents=True)
    (inbox / "replies").mkdir()
    first = json.loads((settings.data_dir / EVENTS_FILE).read_text(encoding="utf-8").splitlines()[0])
    (inbox / "events" / "e1.json").write_text(json.dumps(first), encoding="utf-8")

    last, results = worker.tick(workflow, inbox, None)
    assert str(last) == "2026-07-01"
    assert "schedule" in results[0]
    assert results[1]["event"] == "e1.json"
    assert (inbox / "processed" / "e1.json").exists()

    _, again = worker.tick(workflow, inbox, last)
    assert again == []


def test_dockerfile_runs_non_root_with_per_entry_point_healthchecks() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    stages = dict(re.findall(r"^FROM \S+ AS (\S+)\n(.*?)(?=^FROM |\Z)", dockerfile, re.M | re.S))
    assert {"base", "mcp", "api", "renewal-worker"} <= set(stages)
    assert "--uid 10001" in stages["base"]
    for name in ("mcp", "api", "renewal-worker"):
        assert "USER appuser" in stages[name]
    assert "/health" in stages["mcp"] and "/health" in stages["api"]
    assert "HEALTHCHECK NONE" in stages["renewal-worker"]
    assert "GIT_SHA" in stages["base"]


def test_compose_tags_every_image_with_the_commit_sha() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"mcp", "api", "renewal-worker"}
    for service in services.values():
        assert "${GIT_SHA" in service["image"]
        assert "latest" not in service["image"]
    assert services["api"]["depends_on"]["mcp"]["condition"] == "service_healthy"
    assert "/ready" in " ".join(services["api"]["healthcheck"]["test"])
    assert services["api"]["environment"]["NOVAOPS_MCP_URL"] == "http://mcp:9876/mcp"
