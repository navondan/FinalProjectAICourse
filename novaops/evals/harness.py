"""Replay a golden or evaluation set through the shared agent with per-case isolation."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from novaops.agent.graph import AgentModel, invoke_agent
from novaops.agent.mcp_client import ToolExecutor
from novaops.core.config import Settings
from novaops.core.database import Database
from novaops.core.tracing import clear_last_trace_id, last_trace_id

EVALUATION_AS_OF_DATE = date(2026, 7, 1)

# Golden single-turn cases name only a user group; each group replays as one fixed,
# ordinary member of that group. The group itself is still resolved from the database.
GOLDEN_GROUP_CALLERS = {
    "Regular Employee": "E010",
    "HR Group": "E004",
    "IT Group": "E007",
}

_PERSONA_EMPLOYEE_ID = re.compile(r"\b(E\d{3})\b")
_RECORDED_STATE_KEYS = ("answer", "status", "tool_history", "evidence", "scope", "intent")


@dataclass(frozen=True, slots=True)
class HarnessTurn:
    run_key: str
    n: int | None
    message: str


@dataclass(frozen=True, slots=True)
class HarnessItem:
    item_id: str
    caller_employee_id: str
    turns: tuple[HarnessTurn, ...]
    kind: str  # "session" or "input"
    workflow: str | None = None


@dataclass(slots=True)
class WorkflowItem:
    """A caller-less optional workflow input (vendor document or renewal provider reply)."""

    item_id: str
    workflow: str
    payload: dict[str, Any]


@dataclass(slots=True)
class HarnessPlan:
    items: list[HarnessItem] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    workflow_items: list[WorkflowItem] = field(default_factory=list)


WORKFLOW_RUNNERS = ("vendor", "renewal")


ExecutorFactory = Callable[[Settings, str], ToolExecutor]
ModelFactory = Callable[[Settings], AgentModel | None]
AgentRunner = Callable[..., Awaitable[dict[str, Any]]]


def load_plan(path: Path) -> HarnessPlan:
    """Parse GOLDEN-DATASETS.json or EVALUATION-INPUTS.yaml into runnable items."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    if "workflows" in data:
        return _plan_from_evaluation(data)
    if "sessions" in data or "single_turn_cases" in data:
        return _plan_from_golden(data)
    raise ValueError(f"{path} is neither a golden dataset nor an evaluation input set")


def _plan_from_golden(data: dict[str, Any]) -> HarnessPlan:
    plan = HarnessPlan()
    for session in data.get("sessions", []):
        session_id = _required(session, "id")
        caller = session.get("caller_id") or _persona_employee_id(session)
        if caller is None:
            plan.skipped.append({"item_id": session_id, "reason": "no caller employee id"})
            continue
        turns = tuple(
            HarnessTurn(
                run_key=f"{session_id}:{turn['n']}",
                n=int(turn["n"]),
                message=_required(turn, "user"),
            )
            for turn in sorted(session.get("turns", []), key=lambda turn: turn["n"])
        )
        plan.items.append(HarnessItem(session_id, caller, turns, "session"))
    for case in data.get("single_turn_cases", []):
        case_id = _required(case, "case_id")
        caller = GOLDEN_GROUP_CALLERS.get(case.get("user_group", ""))
        if caller is None:
            plan.skipped.append(
                {"item_id": case_id, "reason": f"unmapped user group {case.get('user_group')!r}"}
            )
            continue
        turn = HarnessTurn(run_key=case_id, n=None, message=_required(case, "input"))
        plan.items.append(HarnessItem(case_id, caller, (turn,), "input"))
    return plan


def _plan_from_evaluation(data: dict[str, Any]) -> HarnessPlan:
    plan = HarnessPlan()
    for workflow in data.get("workflows", []):
        workflow_id = workflow.get("id")
        for item in workflow.get("inputs", []):
            item_id = _required(item, "id")
            caller = (item.get("caller") or {}).get("employee_id")
            if not caller and workflow_id in WORKFLOW_RUNNERS:
                payload = dict(item)
                if workflow_id == "renewal":
                    payload["internal_events"] = workflow.get("internal_events", [])
                plan.workflow_items.append(WorkflowItem(item_id, workflow_id, payload))
                continue
            if not caller:
                plan.skipped.append(
                    {
                        "item_id": item_id,
                        "reason": f"workflow {workflow_id!r} has no caller; "
                        "no harness runner for it yet",
                    }
                )
                continue
            turn = HarnessTurn(run_key=item_id, n=None, message=_required(item, "message"))
            plan.items.append(HarnessItem(item_id, caller, (turn,), "input", workflow_id))
        for session in workflow.get("sessions", []):
            session_id = _required(session, "id")
            caller = (session.get("caller") or {}).get("employee_id")
            if not caller:
                plan.skipped.append({"item_id": session_id, "reason": "no caller employee id"})
                continue
            turns = tuple(
                HarnessTurn(
                    run_key=f"{session_id}:{turn['n']}",
                    n=int(turn["n"]),
                    message=_required(turn, "message"),
                )
                for turn in sorted(session.get("turns", []), key=lambda turn: turn["n"])
            )
            plan.items.append(
                HarnessItem(session_id, caller, turns, "session", workflow_id)
            )
    return plan


def _persona_employee_id(session: dict[str, Any]) -> str | None:
    match = _PERSONA_EMPLOYEE_ID.search(str(session.get("persona", "")))
    return match.group(1) if match else None


def _required(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Item is missing a non-empty {key!r}: {mapping!r}")
    return value


def in_process_executor_factory(settings: Settings) -> ExecutorFactory:
    """Serve MCP tools in-process against each case's own database.

    The FAISS index and embedder are loaded once and shared, read-only, by every case.
    """
    from novaops.agent.mcp_client import MCPToolExecutor
    from novaops.mcp_server.server import create_server
    from novaops.retrieval.embeddings import BedrockEmbedder
    from novaops.retrieval.store import FaissDocumentStore

    embedder = BedrockEmbedder(settings.bedrock_embedding_model_id, settings.aws_region)
    document_store = FaissDocumentStore.load(
        settings.index_path, embedding_model_id=settings.bedrock_embedding_model_id
    )
    servers: dict[Path, Any] = {}

    def factory(case_settings: Settings, request_id: str) -> ToolExecutor:
        server = servers.get(case_settings.database_path)
        if server is None:
            server = create_server(
                case_settings, document_store=document_store, embedder=embedder
            )
            servers.clear()
            servers[case_settings.database_path] = server
        return MCPToolExecutor(server, request_id=request_id)

    return factory


async def run_plan(
    plan: HarnessPlan,
    settings: Settings,
    *,
    executor_factory: ExecutorFactory,
    model_factory: ModelFactory = lambda settings: None,
    run_tag: str,
    work_dir: Path | None = None,
    agent_runner: AgentRunner = invoke_agent,
    progress: Callable[[str], None] = lambda message: None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Run every item; return (runs, trace_index) keyed by run key."""
    runs: dict[str, dict[str, Any]] = {}
    trace_index: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(
        prefix="novaops-harness-",
        dir=str(work_dir) if work_dir else None,
        ignore_cleanup_errors=True,
    ) as root:
        for index, item in enumerate(plan.items, start=1):
            case_dir = Path(root) / f"{index:03d}-{_safe_name(item.item_id)}"
            case_dir.mkdir(parents=True)
            case_settings = replace(
                settings,
                database_path=case_dir / "novaops.sqlite3",
                checkpoint_path=case_dir / "checkpoints.sqlite3",
                as_of_date=EVALUATION_AS_OF_DATE,
            )
            Database(case_settings.database_path, case_settings.data_dir)
            thread_id = f"{item.item_id}-{run_tag}"
            model = model_factory(case_settings)
            for turn in item.turns:
                request_id = f"{item.item_id}:{turn.n or 1}"
                progress(f"[{index}/{len(plan.items)}] {turn.run_key}")
                record, trace_id = await _run_turn(
                    agent_runner,
                    case_settings,
                    item=item,
                    turn=turn,
                    thread_id=thread_id,
                    request_id=request_id,
                    model=model,
                    executor=executor_factory(case_settings, request_id),
                )
                runs[turn.run_key] = record
                trace_index[turn.run_key] = {
                    "item_id": item.item_id,
                    "workflow": item.workflow,
                    "turn": turn.n,
                    "caller_employee_id": item.caller_employee_id,
                    "session_id": thread_id,
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "status": record["status"],
                }
    return runs, trace_index


async def _run_turn(
    agent_runner: AgentRunner,
    settings: Settings,
    *,
    item: HarnessItem,
    turn: HarnessTurn,
    thread_id: str,
    request_id: str,
    model: AgentModel | None,
    executor: ToolExecutor,
) -> tuple[dict[str, Any], str | None]:
    clear_last_trace_id()
    try:
        result = await agent_runner(
            settings,
            message=turn.message,
            caller_employee_id=item.caller_employee_id,
            thread_id=thread_id,
            x_request_id=request_id,
            model=model,
            tool_executor=executor,
        )
    except Exception as exc:  # recorded, never dropped
        return (
            {
                "answer": "",
                "status": "failed",
                "tool_history": [],
                "error": f"{type(exc).__name__}: {exc}",
            },
            last_trace_id(),
        )
    record = {key: result[key] for key in _RECORDED_STATE_KEYS if key in result}
    record.setdefault("answer", "")
    record.setdefault("tool_history", [])
    if "status" not in record:
        record["status"] = "needs_human" if result.get("__interrupt__") else "unknown"
    return _json_safe(record), last_trace_id()


def run_workflow_items(
    items: list[WorkflowItem],
    settings: Settings,
    *,
    work_dir: Path,
    client: Any | None = None,
    renewal_options: dict[str, Any] | None = None,
    progress: Callable[[str], None] = lambda message: None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Run vendor/renewal inputs: one trace per input, each renewal on its own fresh database."""
    from novaops.workflows.renewal.service import run_renewal_evaluation
    from novaops.workflows.vendor.service import run_vendor_extraction

    runs: dict[str, dict[str, Any]] = {}
    trace_index: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        progress(f"[workflow {index}/{len(items)}] {item.item_id}")
        clear_last_trace_id()
        session_id = f"{item.workflow}-{item.item_id}"
        try:
            if item.workflow == "vendor":
                result = run_vendor_extraction(
                    settings,
                    item.payload["document"],
                    source_id=item.item_id,
                    source_name=item.payload.get("source_name"),
                    client=client,
                    session_id=session_id,
                )
                status = "completed"
            else:
                work_dir.mkdir(parents=True, exist_ok=True)
                result = run_renewal_evaluation(
                    settings,
                    item_id=item.item_id,
                    provider_reply=item.payload["provider_reply"],
                    internal_events=item.payload["internal_events"],
                    database_path=work_dir / f"{_safe_name(item.item_id)}.sqlite3",
                    client=client,
                    **(renewal_options or {}),
                )
                status = result["summary"]["run_stage"] or "no_run"
            record = {"workflow": item.workflow, "status": status, "result": _json_safe(result)}
        except Exception as exc:  # recorded, never dropped
            record = {"workflow": item.workflow, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        runs[item.item_id] = record
        trace_index[item.item_id] = {
            "item_id": item.item_id,
            "workflow": item.workflow,
            "turn": None,
            "caller_employee_id": None,
            "session_id": session_id,
            "request_id": item.item_id,
            "trace_id": last_trace_id(),
            "status": record["status"],
        }
    return runs, trace_index


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:60]


def summarize(
    plan: HarnessPlan, runs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for record in runs.values():
        statuses[record["status"]] = statuses.get(record["status"], 0) + 1
    return {
        "items": len(plan.items) + len(plan.workflow_items),
        "turns": len(runs),
        "statuses": statuses,
        "skipped": plan.skipped,
    }
