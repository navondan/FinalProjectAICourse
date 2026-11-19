import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from novaops.agent.graph import invoke_agent
from novaops.core.config import PROJECT_ROOT, Settings
from novaops.evals.harness import (
    EVALUATION_AS_OF_DATE,
    HarnessItem,
    HarnessPlan,
    HarnessTurn,
    load_plan,
    run_plan,
)
from novaops.evals.runner import evaluate_records

DATASET = PROJECT_ROOT / "novaops-enterprise-agent-dataset"


def _settings(tmp_path: Path) -> Settings:
    return replace(
        Settings.from_env(load_env_file=False),
        data_dir=DATASET,
        database_path=tmp_path / "developer.sqlite3",
        checkpoint_path=tmp_path / "developer-checkpoints.sqlite3",
        langfuse_public_key=None,
        langfuse_secret_key=None,
    )


def _session(item_id: str, *messages: str, caller: str = "E010") -> HarnessItem:
    return HarnessItem(
        item_id=item_id,
        caller_employee_id=caller,
        turns=tuple(
            HarnessTurn(run_key=f"{item_id}:{n}", n=n, message=message)
            for n, message in enumerate(messages, start=1)
        ),
        kind="session",
    )


def _one_shot(item_id: str, message: str, caller: str = "E010") -> HarnessItem:
    return HarnessItem(
        item_id=item_id,
        caller_employee_id=caller,
        turns=(HarnessTurn(run_key=item_id, n=None, message=message),),
        kind="input",
    )


def test_golden_plan_maps_callers_and_run_keys_to_the_scorer_format() -> None:
    plan = load_plan(PROJECT_ROOT / "GOLDEN-DATASETS.json")
    items = {item.item_id: item for item in plan.items}

    assert not plan.skipped
    assert len([item for item in plan.items if item.kind == "session"]) == 9
    assert len([item for item in plan.items if item.kind == "input"]) == 20
    assert items["S1-quick-policy"].caller_employee_id == "E010"  # from persona
    assert items["S2-onboarding-maya"].caller_employee_id == "E004"  # from caller_id
    assert items["SIMPLE_007"].caller_employee_id == "E004"  # HR Group
    assert [turn.run_key for turn in items["S1-quick-policy"].turns] == [
        "S1-quick-policy:1",
        "S1-quick-policy:2",
        "S1-quick-policy:3",
        "S1-quick-policy:4",
    ]
    assert items["SIMPLE_001"].turns[0].run_key == "SIMPLE_001"


def test_evaluation_plan_runs_the_27_required_turns_and_reports_skips() -> None:
    plan = load_plan(PROJECT_ROOT / "EVALUATION-INPUTS.yaml")

    turns = [turn.run_key for item in plan.items for turn in item.turns]
    assert len(turns) == 27
    assert {item.workflow for item in plan.items} == {"maya", "webex"}
    assert "M-I-01" in turns and "M-S-01:12" in turns and "W-S-01:4" in turns
    assert sorted(skip["item_id"] for skip in plan.skipped) == [
        "R-I-01", "R-I-02", "R-I-03", "V-I-01", "V-I-02", "V-I-03",
    ]


def test_each_item_gets_a_fresh_seeded_database_and_sessions_share_one_thread(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    seen: list[dict[str, Any]] = []

    async def fake_runner(case_settings: Settings, **kwargs: Any) -> dict[str, Any]:
        connection = sqlite3.connect(case_settings.database_path)
        try:
            tickets = connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
            connection.execute(
                "INSERT INTO tickets SELECT 'HARNESS-' || ?, employee_id, category, "
                "subcategory, priority, status, subject, description, created_at, "
                "assigned_team, related_system_id FROM tickets LIMIT 1",
                (kwargs["x_request_id"],),
            )
            connection.commit()
        finally:
            connection.close()
        seen.append(
            {
                "database": case_settings.database_path,
                "checkpoint": case_settings.checkpoint_path,
                "as_of": case_settings.as_of_date,
                "tickets": tickets,
                **kwargs,
            }
        )
        return {"answer": "ok", "status": "completed", "tool_history": []}

    plan = HarnessPlan(
        items=[_session("S-A", "first", "second"), _one_shot("I-B", "hello")]
    )
    runs, trace_index = asyncio.run(
        run_plan(
            plan,
            settings,
            executor_factory=lambda case_settings, request_id: object(),
            run_tag="test",
            work_dir=tmp_path,
            agent_runner=fake_runner,
        )
    )

    seed_tickets = seen[0]["tickets"]
    # Session turns share a database and thread; the write from turn 1 is visible on turn 2.
    assert seen[1]["tickets"] == seed_tickets + 1
    assert seen[0]["database"] == seen[1]["database"]
    assert seen[0]["thread_id"] == seen[1]["thread_id"] == "S-A-test"
    # The next item starts from a reseeded copy, not the session's database.
    assert seen[2]["tickets"] == seed_tickets
    assert seen[2]["database"] != seen[0]["database"]
    assert seen[2]["checkpoint"] != seen[0]["checkpoint"]
    assert all(entry["as_of"] == EVALUATION_AS_OF_DATE for entry in seen)
    assert [entry["x_request_id"] for entry in seen] == ["S-A:1", "S-A:2", "I-B:1"]
    assert [entry["caller_employee_id"] for entry in seen] == ["E010"] * 3
    # The developer database is never created or touched.
    assert not settings.database_path.exists()
    assert set(runs) == {"S-A:1", "S-A:2", "I-B"}
    assert trace_index["S-A:2"] == {
        "item_id": "S-A",
        "workflow": None,
        "turn": 2,
        "caller_employee_id": "E010",
        "session_id": "S-A-test",
        "request_id": "S-A:2",
        "trace_id": None,
        "status": "completed",
    }


def test_failed_turn_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    async def flaky_runner(case_settings: Settings, **kwargs: Any) -> dict[str, Any]:
        if kwargs["x_request_id"] == "S-A:1":
            raise RuntimeError("model unavailable")
        return {"answer": "ok", "status": "completed", "tool_history": []}

    runs, trace_index = asyncio.run(
        run_plan(
            HarnessPlan(items=[_session("S-A", "first", "second")]),
            _settings(tmp_path),
            executor_factory=lambda case_settings, request_id: object(),
            run_tag="test",
            work_dir=tmp_path,
            agent_runner=flaky_runner,
        )
    )

    assert runs["S-A:1"]["status"] == "failed"
    assert runs["S-A:1"]["error"] == "RuntimeError: model unavailable"
    assert trace_index["S-A:1"]["status"] == "failed"
    assert runs["S-A:2"]["status"] == "completed"


class _FakeModel:
    model_id = "test-model"

    def __init__(self) -> None:
        self.classify_prompts: list[str] = []

    def generate(self, prompt: str, *, system: str | None = None, **_: Any) -> str:
        if system and system.startswith("Classify"):
            self.classify_prompts.append(prompt)
            return json.dumps(
                {
                    "scope": "webex",
                    "intent": "other",
                    "action_requested": False,
                    "target_employee_id": None,
                    "system_name": None,
                    "access_level": None,
                    "business_justification": None,
                }
            )
        return json.dumps({"tool_calls": [], "answer": "Nothing to do; completed."})


def test_real_agent_session_carries_history_and_produces_a_scorable_run_file(
    tmp_path: Path,
) -> None:
    models: list[_FakeModel] = []

    def model_factory(case_settings: Settings) -> _FakeModel:
        models.append(_FakeModel())
        return models[-1]

    plan = HarnessPlan(
        items=[
            _session("S1-quick-policy", "turn one question", "turn two question"),
            _one_shot("SIMPLE_001", "single question"),
        ]
    )
    runs, _ = asyncio.run(
        run_plan(
            plan,
            _settings(tmp_path),
            executor_factory=lambda case_settings, request_id: object(),
            model_factory=model_factory,
            run_tag="test",
            work_dir=tmp_path,
            agent_runner=invoke_agent,
        )
    )

    session_model, one_shot_model = models
    assert "turn one question" in session_model.classify_prompts[1]
    assert "turn one question" not in one_shot_model.classify_prompts[0]
    for record in runs.values():
        assert record["status"] == "completed"
        assert record["answer"] == "Nothing to do; completed."
        assert record["tool_history"] == []
    golden = json.loads((PROJECT_ROOT / "GOLDEN-DATASETS.json").read_text(encoding="utf-8"))
    report = evaluate_records(golden, runs)
    assert "S1-quick-policy:1" not in report["missing_cases"]
    assert "SIMPLE_001" not in report["missing_cases"]
