"""Compare deterministic evaluation reports without rerunning the agent."""

from __future__ import annotations

from typing import Any


def compare_reports(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    current_checks = _checks_by_case(current)
    baseline_checks = _checks_by_case(baseline)
    regressions: list[dict[str, str]] = []
    improvements: list[dict[str, str]] = []
    unchanged = 0

    for case_id in sorted(current_checks.keys() & baseline_checks.keys()):
        for check_name in sorted(
            current_checks[case_id].keys() & baseline_checks[case_id].keys()
        ):
            new = current_checks[case_id][check_name]
            old = baseline_checks[case_id][check_name]
            if new is old:
                unchanged += 1
            elif new is False and old is True:
                regressions.append({"case_id": case_id, "check": check_name})
            elif new is True and old is False:
                improvements.append({"case_id": case_id, "check": check_name})

    return {
        "baseline_score": baseline.get("score"),
        "current_score": current.get("score"),
        "regressions": regressions,
        "improvements": improvements,
        "unchanged": unchanged,
    }


def _checks_by_case(report: dict[str, Any]) -> dict[str, dict[str, bool | None]]:
    output: dict[str, dict[str, bool | None]] = {}
    for case in report.get("cases", []):
        if case.get("missing"):
            continue
        output[case["case_id"]] = {
            check["name"]: check["passed"] for check in case.get("checks", [])
        }
    return output
