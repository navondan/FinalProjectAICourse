"""Human-gated eval loop: evidence -> diagnose -> change -> re-evaluate.

Ported from Lesson 12 (`03-eval-loop/loop.py`, `shared/journal.py`). Differences:

* Evidence comes from local ``runs.json`` files written by ``novaops.cli run-set``
  (the Langfuse v1 trace API the lesson reader used is gone).
* Each round records the git commit it was measured on, so "restore the best build" is
  ``git checkout <sha>``; the runs file is pinned by SHA-256.
* Nothing is promoted automatically. The gate (``targets.compare``) recommends; a named
  human reviewer decides with ``review``. A broken floor or INVALID evidence cannot be
  promoted at all.

Budgets: at most ``max_rounds`` candidate rounds, and at most ``max_stalled`` rounds
in a row without a promotion. When either is spent, ``propose`` refuses.

Usage::

    python -m novaops.evals.loop baseline --runs eval-runs/runs-golden.json
    python -m novaops.evals.loop diagnose
    python -m novaops.evals.loop propose --runs eval-runs/runs-r1.json --change "..."
    python -m novaops.evals.loop review --round 1 --reviewer "Name" --decision promote
    python -m novaops.evals.loop report --output eval-runs/loop-report.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novaops.core.config import PROJECT_ROOT
from novaops.evals import targets
from novaops.evals.runner import evaluate_files

DEFAULT_JOURNAL = PROJECT_ROOT / "eval-runs" / "loop-journal.json"
DEFAULT_GOLDEN = PROJECT_ROOT / "GOLDEN-DATASETS.json"
DEFAULT_BUDGETS = {"max_rounds": 5, "max_stalled": 2}


class LoopError(RuntimeError):
    """A loop rule refused the requested step."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def load(journal: Path) -> dict[str, Any]:
    if not journal.exists():
        raise LoopError(f"No loop journal at {journal}; run 'baseline' first")
    return json.loads(journal.read_text(encoding="utf-8"))


def save(journal: Path, state: dict[str, Any]) -> None:
    # Written after every step: a crash must not lose earlier rounds.
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _measure(runs: Path, golden: Path, *, git_sha: str | None) -> dict[str, Any]:
    report = evaluate_files(runs_path=runs, golden_path=golden)
    return {
        "runs": _display(runs),
        "runs_sha256": _sha256(runs),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "metrics": targets.metrics(report),
        "report": report,
    }


def _summary(measurement: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in measurement.items() if k != "report"}


def start_baseline(
    *,
    runs: Path,
    journal: Path = DEFAULT_JOURNAL,
    golden: Path = DEFAULT_GOLDEN,
    git_sha: str | None = None,
    note: str = "",
    budgets: dict[str, int] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if journal.exists() and not force:
        raise LoopError(f"{journal} already exists; pass --force to start a new loop")
    measured = _measure(runs, golden, git_sha=git_sha)
    if measured["metrics"]["gate"] == "INVALID":
        raise LoopError(
            "Baseline evidence is INVALID (missing cases); an honest baseline must be complete"
        )
    state = {
        "created_at": _now(),
        "golden": _display(golden),
        "targets": {
            "quality": targets.TARGET_QUALITY,
            "each": targets.TARGET_EACH,
            "floors": targets.FLOORS,
            "noise_floor": targets.NOISE_FLOOR,
        },
        "budgets": {**DEFAULT_BUDGETS, **(budgets or {})},
        "best_round": 0,
        "rounds": [
            {
                "round": 0,
                "kind": "baseline",
                "recorded_at": _now(),
                "change": note or "honest baseline before any change",
                **_summary(measured),
                "distance": targets.distance(measured["metrics"]),
                "floor_broken": targets.broken_floors(measured["metrics"]["scores"]),
                "review": {"status": "baseline"},
            }
        ],
    }
    save(journal, state)
    return state


def diagnose(state: dict[str, Any], *, round_no: int | None = None, top: int = 10) -> dict[str, Any]:
    """Evidence for the next change: per-check shortfall and the worst failing cases."""

    round_no = state["best_round"] if round_no is None else round_no
    entry = _round(state, round_no)
    report = evaluate_files(
        runs_path=PROJECT_ROOT / entry["runs"],
        golden_path=PROJECT_ROOT / state["golden"],
    )
    failing = []
    for case in report["cases"]:
        failed = [c for c in case["checks"] if c["passed"] is False]
        if failed:
            failing.append(
                {
                    "case_id": case["case_id"],
                    "failed": [f"{c['name']}: {c['detail']}" for c in failed],
                    "floor": any(c["name"] in targets.FLOORS for c in failed),
                }
            )
    failing.sort(key=lambda item: (not item["floor"], -len(item["failed"]), item["case_id"]))
    metrics = entry["metrics"]
    return {
        "round": round_no,
        "distance": targets.distance(metrics),
        "floor_broken": targets.broken_floors(metrics["scores"]),
        "short": targets.shortfalls(metrics["quality"], metrics["scores"]),
        "failing_cases": len(failing),
        "worst": failing[:top],
    }


def _round(state: dict[str, Any], round_no: int) -> dict[str, Any]:
    for entry in state["rounds"]:
        if entry["round"] == round_no:
            return entry
    raise LoopError(f"No round {round_no} in the journal")


def budget_left(state: dict[str, Any]) -> dict[str, int]:
    candidates = [r for r in state["rounds"] if r["kind"] == "candidate"]
    stalled = 0
    for entry in reversed(candidates):
        if entry["review"]["status"] == "promoted":
            break
        stalled += 1
    return {
        "rounds": state["budgets"]["max_rounds"] - len(candidates),
        "stalled": state["budgets"]["max_stalled"] - stalled,
    }


def propose(
    state: dict[str, Any],
    *,
    runs: Path,
    change: str,
    git_sha: str | None = None,
) -> dict[str, Any]:
    if not change.strip():
        raise LoopError("Describe the change (hypothesis) this round tests")
    pending = [r["round"] for r in state["rounds"] if r["review"]["status"] == "pending"]
    if pending:
        raise LoopError(f"Round {pending[0]} is awaiting human review")
    left = budget_left(state)
    if left["rounds"] <= 0:
        raise LoopError("Round budget spent; report the distance and stop")
    if left["stalled"] <= 0:
        raise LoopError("Stall budget spent (no promotion in a row); stop and rethink")

    best = _round(state, state["best_round"])
    measured = _measure(runs, PROJECT_ROOT / state["golden"], git_sha=git_sha)
    comparison = targets.compare(best["metrics"], measured["metrics"])
    entry = {
        "round": max(r["round"] for r in state["rounds"]) + 1,
        "kind": "candidate",
        "recorded_at": _now(),
        "change": change.strip(),
        "compared_to": best["round"],
        **_summary(measured),
        "distance": targets.distance(measured["metrics"]),
        "floor_broken": targets.broken_floors(measured["metrics"]["scores"]),
        "comparison": comparison,
        "review": {"status": "pending"},
    }
    state["rounds"].append(entry)
    return entry


def review(
    state: dict[str, Any],
    *,
    round_no: int,
    reviewer: str,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    if not reviewer.strip():
        raise LoopError("A named human reviewer is required")
    if decision not in {"promote", "reject"}:
        raise LoopError("Decision must be 'promote' or 'reject'")
    entry = _round(state, round_no)
    if entry["review"]["status"] != "pending":
        raise LoopError(f"Round {round_no} is not awaiting review")
    if decision == "promote":
        comparison = entry["comparison"]
        if comparison["state"] == "INVALID":
            raise LoopError("INVALID evidence cannot be promoted; rerun the set")
        if comparison["floor_broken"]:
            raise LoopError(
                "Floors are not tradeable: " + "; ".join(comparison["floor_broken"])
            )
        state["best_round"] = round_no
    entry["review"] = {
        "status": "promoted" if decision == "promote" else "rejected",
        "reviewer": reviewer.strip(),
        "decided_at": _now(),
        "gate_recommendation": entry["comparison"].get("recommendation"),
        "note": note,
    }
    return entry


def render_report(state: dict[str, Any]) -> str:
    base = _round(state, 0)
    best = _round(state, state["best_round"])
    names = sorted(set(base["metrics"]["scores"]) | set(best["metrics"]["scores"]))
    lines = [
        "# Eval loop report",
        "",
        f"Golden set: `{state['golden']}`. Targets: quality {state['targets']['quality']}, "
        f"each check {state['targets']['each']}, floors "
        + ", ".join(f"{k}={v}" for k, v in state["targets"]["floors"].items())
        + f", noise floor {state['targets']['noise_floor']}.",
        "",
        "The gate reports a distance to target, not a pass. 0.0 means every target is met.",
        "",
        "## Before and after",
        "",
        "| | Baseline (round 0) | Best (round "
        f"{best['round']}) |",
        "|---|---|---|",
        f"| Build | `{base.get('git_sha')}` | `{best.get('git_sha')}` |",
        f"| Quality | {base['metrics']['quality']:.3f} ({base['metrics']['passed']}/"
        f"{base['metrics']['scored']}) | {best['metrics']['quality']:.3f} "
        f"({best['metrics']['passed']}/{best['metrics']['scored']}) |",
        f"| Distance | {base['distance']:.3f} | {best['distance']:.3f} |",
        f"| Floors broken | {', '.join(base['floor_broken']) or 'none'} | "
        f"{', '.join(best['floor_broken']) or 'none'} |",
    ]
    for name in names:
        before = base["metrics"]["scores"].get(name)
        after = best["metrics"]["scores"].get(name)
        tag = " (floor)" if name in targets.FLOORS else ""
        lines.append(
            f"| {name}{tag} | {_fmt(before)} | {_fmt(after)} |"
        )
    lines += [
        "",
        "## Rounds",
        "",
        "| Round | Build | Change | Quality | Distance | Floors broken | Gate | Human decision |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for entry in state["rounds"]:
        comparison = entry.get("comparison", {})
        review_info = entry["review"]
        decision = review_info["status"]
        if review_info.get("reviewer"):
            decision += f" by {review_info['reviewer']}"
        if review_info.get("note"):
            decision += f": {review_info['note']}"
        lines.append(
            f"| {entry['round']} | `{entry.get('git_sha')}` | {entry['change']} | "
            f"{entry['metrics']['quality']:.3f} | {entry['distance']:.3f} | "
            f"{', '.join(entry['floor_broken']) or 'none'} | "
            f"{comparison.get('recommendation', '-')} | {decision} |"
        )
    left = budget_left(state)
    lines += [
        "",
        f"Budget left: {left['rounds']} rounds, {left['stalled']} stalled rounds.",
        "",
    ]
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m novaops.evals.loop")
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    commands = parser.add_subparsers(dest="command", required=True)

    base = commands.add_parser("baseline", help="Record the honest baseline (round 0)")
    base.add_argument("--runs", type=Path, required=True)
    base.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    base.add_argument("--git-sha", help="Build the runs were recorded on (default: HEAD)")
    base.add_argument("--note", default="")
    base.add_argument("--max-rounds", type=int, default=DEFAULT_BUDGETS["max_rounds"])
    base.add_argument("--max-stalled", type=int, default=DEFAULT_BUDGETS["max_stalled"])
    base.add_argument("--force", action="store_true")

    diag = commands.add_parser("diagnose", help="Show evidence for the next change")
    diag.add_argument("--round", type=int)
    diag.add_argument("--top", type=int, default=10)

    prop = commands.add_parser("propose", help="Measure a candidate against the best build")
    prop.add_argument("--runs", type=Path, required=True)
    prop.add_argument("--change", required=True)
    prop.add_argument("--git-sha")

    rev = commands.add_parser("review", help="Human decision on a pending round")
    rev.add_argument("--round", type=int, required=True)
    rev.add_argument("--reviewer", required=True)
    rev.add_argument("--decision", choices=("promote", "reject"), required=True)
    rev.add_argument("--note", default="")

    rep = commands.add_parser("report", help="Render the before/after report")
    rep.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "baseline":
            state = start_baseline(
                runs=args.runs,
                journal=args.journal,
                golden=args.golden,
                git_sha=args.git_sha,
                note=args.note,
                budgets={"max_rounds": args.max_rounds, "max_stalled": args.max_stalled},
                force=args.force,
            )
            entry = state["rounds"][0]
            print(json.dumps({k: entry[k] for k in ("round", "git_sha", "distance", "floor_broken")}
                             | {"quality": entry["metrics"]["quality"]}, indent=2))
        elif args.command == "diagnose":
            print(json.dumps(diagnose(load(args.journal), round_no=args.round, top=args.top),
                             indent=2, ensure_ascii=False))
        elif args.command == "propose":
            state = load(args.journal)
            entry = propose(state, runs=args.runs, change=args.change, git_sha=args.git_sha)
            save(args.journal, state)
            print(json.dumps({"round": entry["round"], **entry["comparison"]}, indent=2))
        elif args.command == "review":
            state = load(args.journal)
            entry = review(state, round_no=args.round, reviewer=args.reviewer,
                           decision=args.decision, note=args.note)
            save(args.journal, state)
            print(json.dumps({"round": entry["round"], **entry["review"]}, indent=2))
        elif args.command == "report":
            text = render_report(load(args.journal))
            if args.output:
                args.output.write_text(text, encoding="utf-8")
            print(text)
    except LoopError as exc:
        print(f"refused: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
