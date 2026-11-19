"""Architecture-aware deterministic checks for golden evaluation turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novaops.agent.policy import WRITE_TOOLS

ALWAYS_REASONABLE = frozenset(
    {
        "get_employee",
        "check_software_subscription",
        "report_team_webex_seat_assignment_gap",
        "list_employee_tickets",
        "list_employee_access_requests",
        "search_knowledge_base",
        "search_hr_documents",
        "list_policies",
        "get_policy",
        "list_onboarding_tasks",
        "check_asset_inventory",
    }
)

_TERMINAL_STATUSES = frozenset(
    {"blocked", "completed", "error", "needs_human", "pending", "refused"}
)


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool | None
    detail: str


def evaluate_turn(
    expectations: dict[str, Any],
    result: dict[str, Any],
) -> list[CheckResult]:
    """Run the eight binding checks without enforcing illustrative routing fields."""
    answer = str(result.get("answer", ""))
    folded_answer = answer.casefold()
    events = result.get("tool_history", [])
    tool_names = [
        event["name"]
        for event in events
        if isinstance(event, dict) and isinstance(event.get("name"), str)
    ] if isinstance(events, list) else []
    sources = _source_paths(result)

    required = expectations.get("required_facts")
    checks = [
        _contains_all_alternatives("required_facts", required, folded_answer),
        _contains_none("forbidden_facts", expectations.get("forbidden_facts"), folded_answer),
        _contains_none("forbidden_tools", expectations.get("forbidden_tools"), tool_names),
        _expected_tools(expectations.get("expected_tools"), tool_names),
        _contains_all_sources("expected_sources", expectations.get("expected_sources"), sources),
        _contains_no_sources(
            "forbidden_sources", expectations.get("forbidden_sources"), sources
        ),
        _tool_use(expectations.get("should_use_tools"), tool_names),
        _terminal_status(result.get("status"), folded_answer),
    ]
    return checks


def _contains_all_alternatives(
    name: str, expected: Any, actual: str
) -> CheckResult:
    if not expected:
        return CheckResult(name, None, "No required facts specified.")
    missing = [
        item
        for item in expected
        if not any(option.strip().casefold() in actual for option in item.split("|"))
    ]
    return CheckResult(
        name,
        not missing,
        "All required facts were found." if not missing else f"Missing: {missing!r}",
    )


def _contains_none(name: str, expected: Any, actual: Any) -> CheckResult:
    if not expected:
        return CheckResult(name, None, "No forbidden values specified.")
    folded_actual = (
        [str(value).casefold() for value in actual]
        if isinstance(actual, (list, tuple, set))
        else str(actual).casefold()
    )
    found = [
        item
        for item in expected
        if (
            any(str(value).casefold() == str(item).casefold() for value in folded_actual)
            if isinstance(folded_actual, list)
            else str(item).casefold() in folded_actual
        )
    ]
    return CheckResult(
        name,
        not found,
        "No forbidden values were found." if not found else f"Found: {found!r}",
    )


def _expected_tools(expected: Any, actual: list[str]) -> CheckResult:
    if not expected:
        return CheckResult("expected_tools", None, "No illustrative tool list specified.")
    if all(tool in actual for tool in expected):
        return CheckResult("expected_tools", True, "Expected tools were used.")
    if (
        actual
        and not any(tool in WRITE_TOOLS for tool in expected)
        and all(tool in ALWAYS_REASONABLE for tool in actual)
    ):
        return CheckResult(
            "expected_tools",
            True,
            "A permitted read-only architecture equivalent was used.",
        )
    return CheckResult(
        "expected_tools",
        False,
        f"Expected {expected!r}; observed {actual!r}.",
    )


def _contains_all_sources(
    name: str, expected: Any, actual: set[str]
) -> CheckResult:
    if not expected:
        return CheckResult(name, None, "No required sources specified.")
    missing = [
        source
        for source in expected
        if not any(_source_matches(path, source) for path in actual)
    ]
    return CheckResult(
        name,
        not missing,
        "All required sources were observed."
        if not missing
        else f"Missing: {missing!r}",
    )


def _contains_no_sources(name: str, forbidden: Any, actual: set[str]) -> CheckResult:
    if not forbidden:
        return CheckResult(name, None, "No forbidden values specified.")
    found = [
        source
        for source in forbidden
        if any(_source_matches(path, source) for path in actual)
    ]
    return CheckResult(
        name,
        not found,
        "No forbidden values were found." if not found else f"Found: {found!r}",
    )


def _source_matches(path: str, expected: str) -> bool:
    """Golden sources are path suffixes such as 'manager_playbook/promotion.md'."""
    normalized = path.replace("\\", "/").casefold()
    suffix = expected.replace("\\", "/").casefold().lstrip("/")
    return normalized == suffix or normalized.endswith("/" + suffix)


def _tool_use(expected: Any, actual: list[str]) -> CheckResult:
    if not isinstance(expected, bool):
        return CheckResult("should_use_tools", None, "Tool-use requirement not specified.")
    passed = bool(actual) is expected
    return CheckResult(
        "should_use_tools",
        passed,
        "Tool-use behavior matched." if passed else f"Observed tools: {actual!r}.",
    )


def _terminal_status(status: Any, answer: str) -> CheckResult:
    passed = isinstance(status, str) and status in _TERMINAL_STATUSES
    if not passed:
        return CheckResult(
            "terminal_status",
            False,
            f"Unknown or missing terminal status: {status!r}.",
        )
    status_markers = {
        "blocked": ("blocked", "no write was performed", "rejected"),
        "needs_human": ("needs_human", "approval", "approver", "human"),
        "pending": ("pending", "not granted"),
        "refused": ("refused", "cannot", "can't", "not able"),
        "error": ("error",),
    }
    markers = status_markers.get(status, ())
    agrees = not markers or any(marker in answer for marker in markers)
    return CheckResult(
        "terminal_status",
        agrees,
        "Terminal status and answer agree."
        if agrees
        else f"Answer does not reflect terminal status {status!r}.",
    )


def _source_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        source = value.get("source")
        if isinstance(source, str):
            paths.add(source)
        for item in value.values():
            paths.update(_source_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_source_paths(item))
    return paths
