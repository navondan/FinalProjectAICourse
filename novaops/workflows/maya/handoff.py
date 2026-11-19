"""Prepare a durable, read-only Webex workflow handoff from Maya's checklist."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WebexHandoff:
    idempotency_key: str
    thread_id: str
    employee_id: str
    system_name: str
    status: str
    reason: str
    evidence_sources: tuple[str, ...]


def prepare_webex_handoff(
    checklist: dict[str, Any], *, thread_id: str
) -> WebexHandoff | None:
    blocked_by_id = {
        item["task_id"]: item for item in checklist.get("blocked_items", [])
    }
    task = next(
        (
            item
            for item in checklist.get("tasks", [])
            if "webex" in item["description"].casefold()
            and item["task_id"] in blocked_by_id
        ),
        None,
    )
    if task is None:
        return None

    blocked = blocked_by_id[task["task_id"]]
    evidence_sources = tuple(
        sorted({citation["source"] for citation in blocked["evidence"]})
    )
    payload = {
        "thread_id": thread_id,
        "employee_id": checklist["employee_id"],
        "system_name": "Webex",
        "task_id": task["task_id"],
        "status": "blocked",
        "reason": blocked["reason"],
        "evidence_sources": evidence_sources,
    }
    idempotency_key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return WebexHandoff(
        idempotency_key=idempotency_key,
        thread_id=thread_id,
        employee_id=checklist["employee_id"],
        system_name="Webex",
        status="blocked",
        reason=blocked["reason"],
        evidence_sources=evidence_sources,
    )
