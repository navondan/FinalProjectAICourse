"""Focused evaluators for context, branch, replay, and restart invariants."""

from __future__ import annotations

import re
from typing import Any

from novaops.agent.policy import LOADOUTS
from novaops.evals.checks import CheckResult

_CITATION = re.compile(r"\[[^\]\r\n]+\]")


def check_constraint_carry(
    prior_turns: list[str],
    current_answer: str,
    required_constraints: list[str],
) -> CheckResult:
    prior_context = "\n".join(prior_turns).casefold()
    answer = current_answer.casefold()
    missing_context = [
        constraint
        for constraint in required_constraints
        if not any(
            alternative.strip().casefold() in prior_context
            for alternative in constraint.split("|")
        )
    ]
    missing_acknowledgement = [
        constraint
        for constraint in required_constraints
        if not any(
            alternative.strip().casefold() in answer
            for alternative in constraint.split("|")
        )
    ]
    passed = not missing_context and not missing_acknowledgement
    details = []
    if missing_context:
        details.append(f"not present in prior turns: {missing_context!r}")
    if missing_acknowledgement:
        details.append(f"not reflected in answer: {missing_acknowledgement!r}")
    return CheckResult(
        "constraint_carry",
        passed,
        "Prior constraints were retained and acknowledged."
        if passed
        else "; ".join(details),
    )


def check_closed_branch_tool_choice(
    scope: str,
    intent: str,
    selected_tools: list[str],
    *,
    allowed_additional_tools: tuple[str, ...] = (),
) -> CheckResult:
    allowed = LOADOUTS.get(scope, {}).get(intent)
    if allowed is None:
        return CheckResult(
            "closed_branch_tool_choice",
            False,
            f"No tool loadout exists for {scope!r}/{intent!r}.",
        )
    allowed_tools = set(allowed) | set(allowed_additional_tools)
    unexpected = sorted(set(selected_tools) - allowed_tools)
    return CheckResult(
        "closed_branch_tool_choice",
        not unexpected,
        "Selected tools match the active branch."
        if not unexpected
        else f"Tools from a closed or unauthorized branch: {unexpected!r}.",
    )


def check_citation_coverage(
    answer: str,
    factual_claims: list[str],
) -> CheckResult:
    if not factual_claims:
        return CheckResult("citation_coverage", None, "No factual claims were supplied.")
    sentences = re.split(r"(?<=[.!?])\s+|\n+", answer)
    uncited = [
        claim
        for claim in factual_claims
        if not any(
            claim.casefold() in sentence.casefold() and _CITATION.search(sentence)
            for sentence in sentences
        )
    ]
    return CheckResult(
        "citation_coverage",
        not uncited,
        "Every supplied factual claim has a citation."
        if not uncited
        else f"Uncited claims: {uncited!r}.",
    )


def check_idempotent_replay(
    first_write_ids: list[str],
    replay_write_ids: list[str],
) -> CheckResult:
    first_counts = _counts(first_write_ids)
    replay_counts = _counts(replay_write_ids)
    passed = first_counts == replay_counts
    return CheckResult(
        "idempotent_replay",
        passed,
        "Replay preserved the same write records."
        if passed
        else f"Write records changed: {first_counts!r} -> {replay_counts!r}.",
    )


def check_restart_lineage(
    saved_state: dict[str, Any],
    resumed_state: dict[str, Any],
    *,
    expected_status: str,
) -> CheckResult:
    passed = (
        saved_state.get("workflow_request_id") is not None
        and resumed_state.get("workflow_request_id")
        == saved_state.get("workflow_request_id")
        and resumed_state.get("status") == expected_status
    )
    return CheckResult(
        "restart_lineage",
        passed,
        "The resumed run retained workflow correlation and reached its expected status."
        if passed
        else "The resumed run lost its workflow correlation or ended in the wrong status.",
    )


def _counts(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
