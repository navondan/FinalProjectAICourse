"""Truthful, evidence-linked outcomes for Webex access diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class DecisionFact:
    text: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccessDecision:
    status: str
    employee_id: str
    system_name: str
    observed_facts: tuple[DecisionFact, ...]
    actions_taken: tuple[str, ...]
    recommended_next_steps: tuple[DecisionFact, ...]
    approval_required: bool = False
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def access_decision_from_dict(value: dict[str, Any]) -> AccessDecision:
    return AccessDecision(
        status=value["status"],
        employee_id=value["employee_id"],
        system_name=value["system_name"],
        observed_facts=tuple(
            DecisionFact(text=fact["text"], sources=tuple(fact["sources"]))
            for fact in value["observed_facts"]
        ),
        actions_taken=tuple(value["actions_taken"]),
        recommended_next_steps=tuple(
            DecisionFact(text=step["text"], sources=tuple(step["sources"]))
            for step in value["recommended_next_steps"]
        ),
        approval_required=value.get("approval_required", False),
        request_id=value.get("request_id"),
    )


def record_request_result(
    decision: AccessDecision, result: dict[str, Any]
) -> AccessDecision:
    request_id = result.get("request_id")
    status = result.get("status")
    if not isinstance(request_id, str) or not isinstance(status, str):
        raise ValueError("Access request tool returned an invalid result")
    return replace(
        decision,
        status="pending" if status in {"pending", "pending_approval"} else status,
        actions_taken=(
            f"Recorded access request {request_id} with status {status}; "
            f"access was not granted. [database:create_access_request]",
        ),
        approval_required=False,
        request_id=request_id,
    )


def build_access_decision(
    *,
    employee: dict[str, Any],
    system_name: str,
    action_requested: bool,
    access_level: str | None,
    business_justification: str | None,
    subscription: dict[str, Any] | None,
    access_requests: list[dict[str, Any]],
    tickets: list[dict[str, Any]],
    policy: dict[str, Any],
    knowledge_evidence: list[dict[str, Any]],
) -> AccessDecision:
    employee_id = employee["employee_id"]
    facts: list[DecisionFact] = [
        DecisionFact(
            text=f"{employee['full_name']} is recorded as {employee['status']}.",
            sources=(f"database:get_employee#{employee_id}",),
        )
    ]
    subscription_source: str | None = None
    capacity_blocked = False
    if subscription is not None:
        subscription_source = (
            f"database:check_software_subscription#{subscription['system_name']}"
        )
        active = subscription["active_seats"]
        limit = subscription["seat_limit"]
        capacity_blocked = active > limit
        facts.append(
            DecisionFact(
                text=(
                    f"{system_name} has {active} active seats against a limit of {limit}."
                ),
                sources=(subscription_source,),
            )
        )

    relevant_tickets = [
        ticket
        for ticket in tickets
        if subscription is None
        or ticket.get("related_system_id") == subscription.get("system_id")
    ]
    for ticket in relevant_tickets:
        facts.append(
            DecisionFact(
                text=(
                    f"Existing ticket {ticket['ticket_id']} is marked "
                    f"{ticket['status']}: {ticket['subject']}."
                ),
                sources=(f"database:list_employee_tickets#{ticket['ticket_id']}",),
            )
        )

    existing = access_requests[0] if access_requests else None
    if existing is not None:
        facts.append(
            DecisionFact(
                text=(
                    f"Existing access request {existing['request_id']} is marked "
                    f"{existing['status']}."
                ),
                sources=(
                    f"database:list_employee_access_requests#{existing['request_id']}",
                ),
            )
        )

    if capacity_blocked and subscription_source is not None:
        knowledge_sources = tuple(
            item["source"]
            for item in knowledge_evidence
            if isinstance(item.get("source"), str)
        )
        next_steps = (
            DecisionFact(
                text=(
                    "Treat the over-limit subscription as a capacity issue; do not "
                    "assign a new license until capacity is resolved."
                ),
                sources=(
                    subscription_source,
                    policy["source"],
                    *knowledge_sources,
                ),
            ),
        )
        if existing is not None:
            next_steps = (
                DecisionFact(
                    text="Resolve the recorded blocker before submitting another request.",
                    sources=(
                        f"database:list_employee_access_requests#{existing['request_id']}",
                    ),
                ),
                *next_steps,
            )
        return AccessDecision(
            status="blocked",
            employee_id=employee_id,
            system_name=system_name,
            observed_facts=tuple(facts),
            actions_taken=(),
            recommended_next_steps=next_steps,
            request_id=existing["request_id"] if existing else None,
        )

    if existing is not None:
        existing_status = existing["status"]
        next_step = (
            "Review the existing blocked request before submitting another request."
            if existing_status == "blocked"
            else "Continue with the existing request; no duplicate was submitted."
        )
        return AccessDecision(
            status="blocked" if existing_status == "blocked" else "pending",
            employee_id=employee_id,
            system_name=system_name,
            observed_facts=tuple(facts),
            actions_taken=(),
            recommended_next_steps=(
                DecisionFact(
                    text=next_step,
                    sources=(
                        f"database:list_employee_access_requests#{existing['request_id']}",
                    ),
                ),
            ),
            request_id=existing["request_id"],
        )

    if not action_requested:
        return AccessDecision(
            status="completed",
            employee_id=employee_id,
            system_name=system_name,
            observed_facts=tuple(facts),
            actions_taken=(),
            recommended_next_steps=(
                DecisionFact(
                    text="No access request was submitted.",
                    sources=(f"database:list_employee_access_requests",),
                ),
            ),
        )

    if not access_level or not business_justification:
        return AccessDecision(
            status="needs_human",
            employee_id=employee_id,
            system_name=system_name,
            observed_facts=tuple(facts),
            actions_taken=(),
            recommended_next_steps=(
                DecisionFact(
                    text=(
                        "Provide the requested access level and business justification "
                        "before an approver can decide."
                    ),
                    sources=(f"database:get_employee#{employee_id}",),
                ),
            ),
        )

    facts.append(
        DecisionFact(
            text="No existing access request was found for this employee and system.",
            sources=("database:list_employee_access_requests",),
        )
    )
    return AccessDecision(
        status="needs_human",
        employee_id=employee_id,
        system_name=system_name,
        observed_facts=tuple(facts),
        actions_taken=(),
        recommended_next_steps=(
            DecisionFact(
                text="A manager decision is required before submitting this request.",
                sources=(f"database:get_employee#{employee_id}", policy["source"]),
            ),
        ),
        approval_required=True,
    )


def render_access_decision(decision: AccessDecision) -> str:
    lines = [
        f"Access decision for {decision.employee_id} / {decision.system_name}: "
        f"{decision.status} [database:get_employee#{decision.employee_id}]."
    ]
    for fact in decision.observed_facts:
        lines.append(f"- Observed: {fact.text} {_citations(fact.sources)}")
    if decision.actions_taken:
        for action in decision.actions_taken:
            lines.append(f"- Action taken: {action}")
    else:
        lines.append("- Actions taken: no write was performed.")
    for step in decision.recommended_next_steps:
        lines.append(f"- Next step: {step.text} {_citations(step.sources)}")
    return "\n".join(lines)


def _citations(sources: tuple[str, ...]) -> str:
    return " ".join(f"[{source}]" for source in dict.fromkeys(sources))
