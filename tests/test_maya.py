import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from novaops.agent.graph import build_agent_graph, open_checkpointer
from novaops.agent.write_gate import ApprovalStore
from novaops.workflows.maya.checklist import (
    _evidence_sources,
    generate_onboarding_checklist,
    render_checklist,
)
from novaops.workflows.maya.handoff import prepare_webex_handoff


EMPLOYEE = {"employee_id": "E001", "full_name": "Maya Cohen"}
TASKS = [
    {
        "task_id": "OT001",
        "description": "Collect signed offer letter and work eligibility confirmation.",
        "owner_group": "HR",
        "status": "completed",
        "due_date": "2026-07-10",
        "related_system_id": "S017",
    },
    {
        "task_id": "OT002",
        "description": "Create Okta profile and baseline groups.",
        "owner_group": "IT",
        "status": "pending",
        "due_date": "2026-07-25",
        "related_system_id": "S007",
    },
    {
        "task_id": "OT003",
        "description": "Prepare laptop, monitor, dock, and MDM enrollment.",
        "owner_group": "IT",
        "status": "planned",
        "due_date": "2026-07-26",
        "related_system_id": "S015",
    },
    {
        "task_id": "OT004",
        "description": "Request Salesforce Customer Success profile.",
        "owner_group": "IT",
        "status": "planned",
        "due_date": "2026-07-28",
        "related_system_id": "S002",
    },
    {
        "task_id": "OT005",
        "description": "Request Webex standard user license.",
        "owner_group": "IT",
        "status": "blocked",
        "due_date": "2026-07-28",
        "related_system_id": "S001",
    },
    {
        "task_id": "OT006",
        "description": "Add employee self-service profile in BambooHR.",
        "owner_group": "HR",
        "status": "planned",
        "due_date": "2026-07-28",
        "related_system_id": "S008",
    },
]
ASSETS = [
    {
        "asset_id": "ASSET-001",
        "asset_type": "laptop",
        "status": "assigned",
        "location": "Israel",
    }
]
SUBSCRIPTION = {
    "system_name": "Webex",
    "seat_limit": 40,
    "active_seats": 42,
    "status": "over_limit",
}
POLICY = {
    "name": "onboarding_policy",
    "source": "documents/policies/onboarding_policy.md",
    "content": (
        "Do not mark onboarding tasks complete unless the database says completed. "
        "Blocked access stays blocked until the seat issue is resolved."
    ),
}


class FakeModel:
    model_id = "test-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> str:
        if not self.responses:
            raise AssertionError("Fake model has no remaining response")
        return self.responses.pop(0)


class FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        results = {
            "search_knowledge_base": [
                {
                    "source": POLICY["source"],
                    "text": POLICY["content"],
                }
            ],
            "get_employee": EMPLOYEE,
            "list_onboarding_tasks": TASKS,
            "check_asset_inventory": ASSETS,
            "check_software_subscription": SUBSCRIPTION,
            "get_policy": POLICY,
        }
        return results[name]


class MayaS9ToolExecutor(FakeToolExecutor):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "search_knowledge_base":
            self.calls.append((name, dict(arguments)))
            if "expand SaaS" in arguments["query"]:
                return [
                    {
                        "source": "documents/internal_memos/saas_renewal_freeze_q3.md",
                        "text": (
                            "Q3 expansions above USD 7,500 annualized require Finance approval."
                        ),
                    }
                ]
            return []
        return await super().call_tool(name, arguments)


def _citation(source: str, quote: str) -> dict[str, str]:
    return {"source": source, "quote": quote}


def _checklist_output(*, blocked_reason: str | None = None) -> str:
    result = {
        "employee_id": EMPLOYEE["employee_id"],
        "employee_name": EMPLOYEE["full_name"],
        "tasks": [
            {
                "task_id": task["task_id"],
                "description": task["description"],
                "owner_group": task["owner_group"],
                "status": task["status"],
                "due_date": task["due_date"],
                "evidence": [
                    _citation(
                        f"database:list_onboarding_tasks#{task['task_id']}",
                        task["description"],
                    )
                ],
            }
            for task in TASKS
        ],
        "blocked_items": [
            {
                "task_id": "OT005",
                "reason": blocked_reason
                or "Webex is blocked: 42 active seats exceed the 40-seat limit.",
                "evidence": [
                    _citation(
                        "database:list_onboarding_tasks#OT005",
                        TASKS[4]["description"],
                    ),
                    _citation(
                        "database:check_software_subscription#Webex",
                        '"active_seats":42',
                    ),
                ],
            }
        ],
    }
    return json.dumps(result)


def _classification(
    *,
    scope: str = "maya",
    intent: str = "onboarding_status",
) -> str:
    return json.dumps(
        {
            "scope": scope,
            "intent": intent,
            "action_requested": False,
            "target_employee_id": None,
            "system_name": None,
            "access_level": None,
            "business_justification": None,
        }
    )


def test_maya_onboarding_no_writes_and_checklist_is_cited(tmp_path: Path) -> None:
    async def run() -> None:
        executor = FakeToolExecutor()
        model = FakeModel([_classification(), _checklist_output()])
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=executor,
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            result = await graph.ainvoke(
                {
                    "message": "Show me my onboarding checklist.",
                    "caller_employee_id": "E001",
                    "caller_group": "Regular Employee",
                    "thread_id": "maya-own-account",
                },
                config={"configurable": {"thread_id": "maya-own-account"}},
            )

        assert result["status"] == "blocked"
        assert len(result["checklist"]["tasks"]) == 6
        assert result["checklist"]["blocked_items"][0]["task_id"] == "OT005"
        assert "42 active seats exceed the 40-seat limit" in result["answer"]
        assert "[database:check_software_subscription#Webex]" in result["answer"]
        assert result["webex_handoff"]["employee_id"] == "E001"
        assert result["webex_handoff"]["status"] == "blocked"
        assert result["webex_handoff"]["evidence_sources"] == (
            "database:check_software_subscription#Webex",
            "database:list_onboarding_tasks#OT005",
        )
        assert all(
            name not in {"create_access_request", "create_ticket"}
            for name, _ in executor.calls
        )
        assert {name for name, _ in executor.calls} == {
            "get_employee",
            "list_onboarding_tasks",
            "check_asset_inventory",
            "check_software_subscription",
            "get_policy",
        }
        assert not {"create_access_request", "create_ticket"} & set(
            result["selected_tools"]
        )

    asyncio.run(run())


def test_maya_checklist_rejects_an_unsupported_blocked_reason() -> None:
    bad = _checklist_output(blocked_reason="Webex access is approved and ready.")
    model = FakeModel([bad, bad])

    checklist = generate_onboarding_checklist(
        model,
        employee=EMPLOYEE,
        tasks=TASKS,
        assets=ASSETS,
        subscription=SUBSCRIPTION,
        policy=POLICY,
    )

    assert "does not match its evidence" not in render_checklist(checklist)
    sources = _evidence_sources(EMPLOYEE, TASKS, ASSETS, SUBSCRIPTION, POLICY)
    citations = [
        citation
        for entry in (*checklist.tasks, *checklist.blocked_items)
        for citation in entry.evidence
    ]
    assert all(c.quote in sources[c.source] for c in citations)
    reasons = [item.reason for item in checklist.blocked_items]
    assert reasons and all("approved" not in reason for reason in reasons)
    assert any("active seats exceed" in reason for reason in reasons)
    rendered = render_checklist(checklist)
    assert "[database:check_software_subscription#Webex]" in rendered
    assert [task.task_id for task in checklist.tasks] == [row["task_id"] for row in TASKS]


def test_webex_handoff_is_dynamic_and_idempotent() -> None:
    checklist = {
        "employee_id": "E019",
        "tasks": [
            {"task_id": "OT999", "description": "Request Webex access.", "status": "blocked"}
        ],
        "blocked_items": [
            {
                "task_id": "OT999",
                "reason": "Webex is blocked by seat capacity.",
                "evidence": [
                    {"source": "database:check_software_subscription#Webex"},
                    {"source": "database:list_onboarding_tasks#OT999"},
                ],
            }
        ],
    }

    first = prepare_webex_handoff(checklist, thread_id="thread-1")
    replay = prepare_webex_handoff(checklist, thread_id="thread-1")
    other_thread = prepare_webex_handoff(checklist, thread_id="thread-2")

    assert first is not None
    assert replay is not None
    assert other_thread is not None
    assert first.employee_id == "E019"
    assert first.idempotency_key == replay.idempotency_key
    assert first.idempotency_key != other_thread.idempotency_key


def test_s9_maya_no_writes_and_own_account_is_cited(tmp_path: Path) -> None:
    async def run() -> None:
        session_path = Path(__file__).parent / "fixtures" / "maya_s9.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        turns = session["turns"]
        responses: list[str] = []
        answers = [
            "I cannot provide manager-only guidance [conversation:turn-1].",
            "Person B agreed to review the draft on Wednesday [conversation:turn-2].",
            (
                "Q3 SaaS expansions above USD 7,500 annualized require Finance approval "
                "[documents/internal_memos/saas_renewal_freeze_q3.md]."
            ),
            "I cannot provide manager-only guidance [conversation:turn-4].",
        ]
        for turn, answer in zip(turns[:4], answers):
            responses.append(
                _classification(scope=turn["scope"], intent=turn["intent"])
            )
            if turn["message"] == "Can we expand SaaS spending this quarter?":
                responses.append(answer)
            elif turn["message"].startswith("Forwarded email thread:"):
                responses.append(
                    json.dumps({"tool_calls": [], "answer": answer})
                )
        responses.extend(
            [
                _classification(
                    scope=turns[4]["scope"], intent=turns[4]["intent"]
                ),
                _checklist_output(),
            ]
        )

        model = FakeModel(responses)
        executor = MayaS9ToolExecutor()
        results = []
        for turn_number, turn in enumerate(turns, start=1):
            async with open_checkpointer(tmp_path / "s9-checkpoints.sqlite3") as checkpointer:
                graph = build_agent_graph(
                    model=model,
                    tool_executor=executor,
                    approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                    checkpointer=checkpointer,
                )
                result = await graph.ainvoke(
                    {
                        "message": turn["message"],
                        "caller_employee_id": session["caller_employee_id"],
                        "caller_group": "Regular Employee",
                        "thread_id": session["id"],
                    },
                    config={"configurable": {"thread_id": session["id"]}},
                )
            assert result["scope"] == turn["scope"]
            assert result["intent"] == turn["intent"]
            for citation in turn["expected_citations"]:
                assert f"[{citation}]" in result["answer"]
            for fact in turn.get("required_facts", []):
                assert fact in result["answer"]
            assert not set(turn.get("forbidden_tools", [])) & set(
                result["selected_tools"]
            )
            results.append(result)

        assert all(
            name not in {"create_access_request", "create_ticket"}
            for name, _ in executor.calls
        )
        assert all(
            {"create_access_request", "create_ticket"}.isdisjoint(
                result["selected_tools"]
            )
            for result in results
        )

    asyncio.run(run())


def _policy_classification() -> str:
    return json.dumps(
        {
            "scope": "maya",
            "intent": "policy_question",
            "action_requested": False,
            "target_employee_id": None,
            "system_name": None,
            "access_level": None,
            "business_justification": None,
        }
    )


async def _run_policy_turn(tmp_path: Path, responses: list[str]) -> dict[str, Any]:
    async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
        graph = build_agent_graph(
            model=FakeModel([_policy_classification(), *responses]),
            tool_executor=FakeToolExecutor(),
            approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
            checkpointer=checkpointer,
        )
        return await graph.ainvoke(
            {
                "message": "What is the onboarding policy?",
                "caller_employee_id": "E010",
                "caller_group": "Regular Employee",
                "thread_id": "citation-thread",
            },
            config={"configurable": {"thread_id": "citation-thread"}},
        )


def test_uncited_maya_answer_is_retried_with_feedback(tmp_path: Path) -> None:
    cited = (
        "## Onboarding\n"
        "Tasks are only complete when the database says so "
        f"[{POLICY['source']}]."
    )
    result = asyncio.run(
        _run_policy_turn(tmp_path, ["Tasks are complete when done.", cited])
    )

    assert result["status"] == "completed"
    assert result["answer"] == cited


def test_maya_answer_falls_back_to_a_cited_refusal_instead_of_failing(
    tmp_path: Path,
) -> None:
    result = asyncio.run(
        _run_policy_turn(tmp_path, ["No citation here.", "Still none."])
    )

    assert result["status"] == "completed"
    assert "couldn't produce a fully cited answer" in result["answer"]
    assert f"[{POLICY['source']}]" in result["answer"]
    assert "[conversation:turn-1]" in result["answer"]
