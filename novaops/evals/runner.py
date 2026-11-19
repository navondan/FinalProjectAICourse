"""Score recorded turn results against the binding golden-data expectations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novaops.core.config import PROJECT_ROOT
from novaops.evals.checks import CheckResult, evaluate_turn


def evaluate_records(
    golden: dict[str, Any],
    runs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for session in golden.get("sessions", []):
        session_id = session.get("id")
        for turn in session.get("turns", []):
            case_id = f"{session_id}:{turn.get('n')}"
            cases.append(
                _evaluate_case(case_id, turn, runs.get(case_id))
            )

    for case in golden.get("single_turn_cases", []):
        case_id = case.get("case_id")
        expectations = {
            "expected_sources": case.get("expected_sources"),
            "should_use_tools": case.get("requires_tool_call"),
        }
        cases.append(
            _evaluate_case(case_id, expectations, runs.get(case_id))
        )

    checks = [
        check
        for case in cases
        for check in case["checks"]
        if check["passed"] is not None
    ]
    passed = sum(check["passed"] is True for check in checks)
    return {
        "score": passed / len(checks) if checks else None,
        "passed": passed,
        "scored": len(checks),
        "missing_cases": [case["case_id"] for case in cases if case["missing"]],
        "cases": cases,
    }


def evaluate_files(
    *,
    runs_path: Path,
    golden_path: Path = PROJECT_ROOT / "GOLDEN-DATASETS.json",
) -> dict[str, Any]:
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    runs = json.loads(runs_path.read_text(encoding="utf-8"))
    if not isinstance(golden, dict) or not isinstance(runs, dict):
        raise ValueError("Golden dataset and run records must be JSON objects")
    if not all(isinstance(value, dict) for value in runs.values()):
        raise ValueError("Every recorded run must be a JSON object")
    return evaluate_records(golden, runs)


def _evaluate_case(
    case_id: str,
    expectations: dict[str, Any],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    if result is None:
        return {"case_id": case_id, "missing": True, "checks": []}
    checks: list[CheckResult] = evaluate_turn(expectations, result)
    return {
        "case_id": case_id,
        "missing": False,
        "checks": [
            {"name": check.name, "passed": check.passed, "detail": check.detail}
            for check in checks
        ],
    }
