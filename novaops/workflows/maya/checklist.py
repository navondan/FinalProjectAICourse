"""Grounded structured output for Maya's read-only onboarding checklist."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import validate


class ChecklistModel(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> str: ...


CHECKLIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["employee_id", "employee_name", "tasks", "blocked_items"],
    "properties": {
        "employee_id": {"type": "string"},
        "employee_name": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "task_id",
                    "description",
                    "owner_group",
                    "status",
                    "due_date",
                    "evidence",
                ],
                "properties": {
                    "task_id": {"type": "string"},
                    "description": {"type": "string"},
                    "owner_group": {"type": "string"},
                    "status": {"type": "string", "minLength": 1},
                    "due_date": {"type": ["string", "null"]},
                    "evidence": {"$ref": "#/$defs/evidence"},
                },
            },
        },
        "blocked_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["task_id", "reason", "evidence"],
                "properties": {
                    "task_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "evidence": {"$ref": "#/$defs/evidence"},
                },
            },
        },
    },
    "$defs": {
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "quote"],
                "properties": {
                    "source": {"type": "string"},
                    "quote": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}


@dataclass(frozen=True, slots=True)
class EvidenceCitation:
    source: str
    quote: str


@dataclass(frozen=True, slots=True)
class ChecklistTask:
    task_id: str
    description: str
    owner_group: str
    status: str
    due_date: str | None
    evidence: tuple[EvidenceCitation, ...]


@dataclass(frozen=True, slots=True)
class BlockedItem:
    task_id: str
    reason: str
    evidence: tuple[EvidenceCitation, ...]


@dataclass(frozen=True, slots=True)
class OnboardingChecklist:
    employee_id: str
    employee_name: str
    tasks: tuple[ChecklistTask, ...]
    blocked_items: tuple[BlockedItem, ...]


def generate_onboarding_checklist(
    model: ChecklistModel,
    *,
    employee: dict[str, Any],
    tasks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    subscription: dict[str, Any],
    policy: dict[str, Any],
) -> OnboardingChecklist:
    evidence_sources = _evidence_sources(employee, tasks, assets, subscription, policy)
    prompt = json.dumps(
        {
            "employee": employee,
            "onboarding_tasks": tasks,
            "asset_inventory": assets,
            "webex_subscription": subscription,
            "onboarding_policy": policy,
            "required_output_schema": CHECKLIST_SCHEMA,
            "instructions": (
                "Copy every task exactly from onboarding_tasks, without adding, removing, "
                "or changing fields. Include one blocked_items entry for every blocked task. "
                "For a blocked Webex task, state the actual active-seat and seat-limit counts. "
                "Every task and blocked item must cite the exact database row source and an "
                "exact quote from that row. The Webex blocked item must also cite the "
                "subscription row. Return JSON only."
            ),
        },
        ensure_ascii=False,
    )
    response = model.generate(
        prompt,
        system=(
            "Produce the requested OnboardingChecklist JSON object. The database is the "
            "source of truth for task fields and status. Never mark a task complete unless "
            "the onboarding database says completed. Do not claim that a write or handoff "
            "occurred. Do not omit blocked tasks."
        ),
        max_tokens=1800,
        temperature=0,
    )
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError("The model returned invalid JSON for the onboarding checklist") from exc
    validate(instance=parsed, schema=CHECKLIST_SCHEMA)
    return _validate_checklist(
        parsed,
        employee=employee,
        tasks=tasks,
        subscription=subscription,
        evidence_sources=evidence_sources,
    )


def render_checklist(checklist: OnboardingChecklist) -> str:
    employee_source = f"database:get_employee#{checklist.employee_id}"
    lines = [
        f"Onboarding checklist for {checklist.employee_name} "
        f"({checklist.employee_id}) [{employee_source}]"
    ]
    for task in checklist.tasks:
        due_date = f", due {task.due_date}" if task.due_date else ""
        citations = _render_citations(task.evidence)
        lines.append(
            f"- [{task.status}] {task.description} "
            f"(owner: {task.owner_group}{due_date}) {citations}"
        )
    for item in checklist.blocked_items:
        lines.append(f"- Blocked: {item.reason} {_render_citations(item.evidence)}")
    return "\n".join(lines)


def _validate_checklist(
    value: dict[str, Any],
    *,
    employee: dict[str, Any],
    tasks: list[dict[str, Any]],
    subscription: dict[str, Any],
    evidence_sources: dict[str, str],
) -> OnboardingChecklist:
    employee_id = employee["employee_id"]
    employee_name = employee["full_name"]
    if value["employee_id"] != employee_id or value["employee_name"] != employee_name:
        raise ValueError("Checklist employee does not match the authorized employee record")

    expected_tasks = {row["task_id"]: row for row in tasks}
    actual_tasks = {item["task_id"]: item for item in value["tasks"]}
    if len(actual_tasks) != len(value["tasks"]) or actual_tasks.keys() != expected_tasks.keys():
        raise ValueError("Checklist must include each onboarding task exactly once")

    checklist_tasks: list[ChecklistTask] = []
    for task_id, expected in expected_tasks.items():
        item = actual_tasks[task_id]
        for field in ("description", "owner_group", "status", "due_date"):
            if item[field] != expected[field]:
                raise ValueError(f"Checklist task {task_id} changed database field {field!r}")
        citations = _validate_citations(item["evidence"], evidence_sources)
        task_source = f"database:list_onboarding_tasks#{task_id}"
        if task_source not in {citation.source for citation in citations}:
            raise ValueError(f"Checklist task {task_id} must cite its database row")
        checklist_tasks.append(
            ChecklistTask(
                task_id=task_id,
                description=item["description"],
                owner_group=item["owner_group"],
                status=item["status"],
                due_date=item["due_date"],
                evidence=citations,
            )
        )

    blocked_rows = {row["task_id"]: row for row in tasks if row["status"] == "blocked"}
    blocked_values = {item["task_id"]: item for item in value["blocked_items"]}
    if (
        len(blocked_values) != len(value["blocked_items"])
        or blocked_values.keys() != blocked_rows.keys()
    ):
        raise ValueError("Checklist must preserve every blocked onboarding task")

    blocked_items: list[BlockedItem] = []
    for task_id, row in blocked_rows.items():
        item = blocked_values[task_id]
        expected_reason, required_sources = _blocked_reason(row, subscription)
        if item["reason"] != expected_reason:
            raise ValueError(f"Checklist blocked item {task_id} does not match its evidence")
        citations = _validate_citations(item["evidence"], evidence_sources)
        actual_sources = {citation.source for citation in citations}
        if not required_sources <= actual_sources:
            raise ValueError(f"Checklist blocked item {task_id} is missing required citations")
        blocked_items.append(
            BlockedItem(task_id=task_id, reason=item["reason"], evidence=citations)
        )

    return OnboardingChecklist(
        employee_id=employee_id,
        employee_name=employee_name,
        tasks=tuple(checklist_tasks),
        blocked_items=tuple(blocked_items),
    )


def _blocked_reason(
    task: dict[str, Any], subscription: dict[str, Any]
) -> tuple[str, set[str]]:
    task_source = f"database:list_onboarding_tasks#{task['task_id']}"
    if "webex" in task["description"].casefold():
        active = subscription["active_seats"]
        limit = subscription["seat_limit"]
        if active > limit:
            subscription_source = (
                f"database:check_software_subscription#{subscription['system_name']}"
            )
            reason = (
                f"Webex is blocked: {active} active seats exceed the {limit}-seat limit."
            )
            return reason, {task_source, subscription_source}
    return (
        f"{task['description']} remains blocked in the onboarding database.",
        {task_source},
    )


def _evidence_sources(
    employee: dict[str, Any],
    tasks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    subscription: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, str]:
    sources = {
        f"database:get_employee#{employee['employee_id']}": _canonical(employee),
        f"database:check_software_subscription#{subscription['system_name']}": _canonical(
            subscription
        ),
        policy["source"]: policy["content"],
    }
    sources.update(
        {
            f"database:list_onboarding_tasks#{row['task_id']}": _canonical(row)
            for row in tasks
        }
    )
    sources.update(
        {
            f"database:check_asset_inventory#{row.get('asset_id', index)}": _canonical(row)
            for index, row in enumerate(assets)
        }
    )
    return sources


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_citations(
    citations: list[dict[str, str]], evidence_sources: dict[str, str]
) -> tuple[EvidenceCitation, ...]:
    validated: list[EvidenceCitation] = []
    for citation in citations:
        source = citation["source"]
        quote = citation["quote"]
        source_text = evidence_sources.get(source)
        if source_text is None or quote not in source_text:
            raise ValueError(f"Checklist citation is not supported by evidence: {source!r}")
        validated.append(EvidenceCitation(source=source, quote=quote))
    return tuple(validated)


def _render_citations(citations: tuple[EvidenceCitation, ...]) -> str:
    return " ".join(f"[{citation.source}]" for citation in citations)
