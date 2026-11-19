import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from novaops.agent.graph import (
    _parse_json_object,
    build_agent_graph,
    open_checkpointer,
)
from novaops.agent.write_gate import ApprovalStore
from novaops.evals.checks import evaluate_turn


class FakeModel:
    model_id = "test-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str | None]] = []

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1200,
        temperature: float = 0.1,
    ) -> str:
        self.calls.append((prompt, system))
        if not self.responses:
            raise AssertionError("Fake model has no remaining response")
        return self.responses.pop(0)


class FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, dict(arguments)))
        if name == "search_knowledge_base":
            return [{"source": "documents/policies/remote_work_policy.md", "text": "Policy"}]
        if name == "list_employee_tickets":
            return [{"ticket_id": "T001", "status": "open"}]
        return {}


def _classification(
    *,
    scope: str,
    intent: str,
    action_requested: bool = False,
    target_employee_id: str | None = None,
    system_name: str | None = None,
    access_level: str | None = None,
    business_justification: str | None = None,
) -> str:
    return json.dumps(
        {
            "scope": scope,
            "intent": intent,
            "action_requested": action_requested,
            "target_employee_id": target_employee_id,
            "system_name": system_name,
            "access_level": access_level,
            "business_justification": business_justification,
        }
    )


@pytest.mark.parametrize(
    "response",
    [
        '{"scope": "maya"}',
        '```json\n{"scope": "maya"}\n```',
        '```\n{"scope": "maya"}\n```',
        '  ```JSON\n{"scope": "maya"}\n```  ',
    ],
)
def test_json_object_parser_accepts_plain_or_fenced_json(response: str) -> None:
    assert _parse_json_object(response, "classification") == {"scope": "maya"}


@pytest.mark.parametrize(
    "response",
    [
        "The answer is {\"scope\": \"maya\"}.",
        "```json\n{\"scope\": \"maya\"}\n```\nextra text",
        "```python\nprint('not JSON')\n```",
        "[]",
    ],
)
def test_json_object_parser_rejects_non_object_or_unfenced_prose(
    response: str,
) -> None:
    with pytest.raises(ValueError):
        _parse_json_object(response, "classification")


def test_shared_graph_preserves_thread_and_forces_trusted_caller_context(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        checkpoint_path = tmp_path / "checkpoints.sqlite3"
        approval_store = ApprovalStore(tmp_path / "approvals.sqlite3")
        tool_executor = FakeToolExecutor()
        model = FakeModel(
            [
                _classification(scope="maya", intent="policy_question"),
                "The remote work policy is available "
                "[documents/policies/remote_work_policy.md].",
                _classification(
                    scope="webex",
                    intent="ticket_status",
                    target_employee_id="E010",
                    system_name="Webex",
                ),
                json.dumps(
                    {
                        "tool_calls": [
                            {
                                "name": "list_employee_tickets",
                                "arguments": {
                                    "employee_id": "E010",
                                    "caller_employee_id": "E999",
                                },
                            }
                        ],
                        "answer": None,
                    }
                ),
                "Rachel's Webex ticket T001 is open.",
            ]
        )
        config = {"configurable": {"thread_id": "conversation-1"}}
        async with open_checkpointer(checkpoint_path) as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=tool_executor,
                approval_store=approval_store,
                checkpointer=checkpointer,
            )
            first = await graph.ainvoke(
                {
                    "message": "What is the remote work policy?",
                    "caller_employee_id": "E004",
                    "caller_group": "HR Group",
                    "thread_id": "conversation-1",
                },
                config=config,
            )
        assert first["scope"] == "maya"
        assert "search_knowledge_base" in first["selected_tools"]

        async with open_checkpointer(checkpoint_path) as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=tool_executor,
                approval_store=approval_store,
                checkpointer=checkpointer,
            )
            second = await graph.ainvoke(
                {
                    "message": "What is the status of Rachel's Webex ticket?",
                    "caller_employee_id": "E004",
                    "caller_group": "HR Group",
                    "thread_id": "conversation-1",
                },
                config=config,
            )
            restored = await graph.aget_state(config)

        assert second["scope"] == "webex"
        assert second["intent"] == "ticket_status"
        assert "list_employee_tickets" in second["selected_tools"]
        assert "check_software_subscription" not in second["selected_tools"]
        assert len(second["history"]) == 4
        assert second["status"] == "completed"
        assert "T001 is open" in second["answer"]
        assert first["tool_history"][0]["name"] == "search_knowledge_base"
        assert restored.values["history"] == second["history"]
        assert tool_executor.calls[0][0] == "search_knowledge_base"
        assert tool_executor.calls[1] == (
            "list_employee_tickets",
            {"employee_id": "E010", "caller_employee_id": "E004"},
        )
        second_classifier_prompt = model.calls[2][0]
        assert "What is the remote work policy?" in second_classifier_prompt
        second_answer_prompt = model.calls[4][0]
        assert "What is the remote work policy?" in second_answer_prompt

    import asyncio

    asyncio.run(run())


def test_graph_does_not_expose_or_execute_an_unapproved_write(tmp_path: Path) -> None:
    async def run() -> None:
        approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
        approvals.record_decision(
            thread_id="conversation-2",
            scope="webex",
            employee_id="E010",
            tool_name="create_access_request",
            system_name="Webex",
            access_level="standard_user",
            business_justification="Customer onboarding calls require Webex.",
            status="approved",
            approver_employee_id="E018",
            reason="Approved for another action.",
        )
        model = FakeModel(
            [
                _classification(
                    scope="webex",
                    intent="ticket_status",
                    target_employee_id="E010",
                    system_name="Webex",
                ),
                json.dumps(
                    {
                        "tool_calls": [
                            {
                                "name": "create_access_request",
                                "arguments": {},
                            }
                        ],
                        "answer": None,
                    }
                ),
            ]
        )
        tool_executor = FakeToolExecutor()
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=tool_executor,
                approval_store=approvals,
                checkpointer=checkpointer,
            )
            with pytest.raises(PermissionError, match="not visible"):
                await graph.ainvoke(
                    {
                        "message": "Please request Webex access.",
                        "caller_employee_id": "E004",
                        "caller_group": "HR Group",
                        "thread_id": "conversation-2",
                    },
                    config={"configurable": {"thread_id": "conversation-2"}},
                )
        assert not any(name == "create_access_request" for name, _ in tool_executor.calls)

    import asyncio

    asyncio.run(run())


def test_conditional_edges_use_named_routers_for_readable_traces(tmp_path: Path) -> None:
    async def run() -> None:
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=FakeModel([]),
                tool_executor=FakeToolExecutor(),
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
        router_names = {
            name for branches in graph.builder.branches.values() for name in branches
        }
        assert router_names == {
            "route_after_retrieve",
            "route_after_diagnosis",
            "route_after_answer",
            "route_after_tools",
        }

    import asyncio

    asyncio.run(run())


def test_thread_history_cannot_be_reused_by_a_different_caller(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model = FakeModel(
            [
                _classification(scope="maya", intent="other"),
                json.dumps(
                    {
                        "tool_calls": [],
                        "answer": "I do not have enough context [conversation:turn-1].",
                    }
                ),
            ]
        )
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=FakeToolExecutor(),
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            config = {"configurable": {"thread_id": "owned-thread"}}
            await graph.ainvoke(
                {
                    "message": "Hello.",
                    "caller_employee_id": "E001",
                    "caller_group": "Regular Employee",
                    "thread_id": "owned-thread",
                },
                config=config,
            )
            with pytest.raises(PermissionError, match="different caller"):
                await graph.ainvoke(
                    {
                        "message": "Continue from that private context.",
                        "caller_employee_id": "E020",
                        "caller_group": "Regular Employee",
                        "thread_id": "owned-thread",
                    },
                    config=config,
                )

    import asyncio

    asyncio.run(run())


def test_policy_followup_retrieves_evidence_for_the_current_request(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model = FakeModel(
            [
                _classification(scope="maya", intent="policy_question"                ),
                "The policy explains the remote work schedule "
                "[documents/policies/remote_work_policy.md].",
                _classification(scope="maya", intent="policy_question"),
                "IT owns the remote work policy "
                "[documents/policies/remote_work_policy.md].",
            ]
        )
        executor = FakeToolExecutor()
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=executor,
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            config = {"configurable": {"thread_id": "policy-followup"}}
            await graph.ainvoke(
                {
                    "message": "What is the remote work policy?",
                    "caller_employee_id": "E001",
                    "caller_group": "Regular Employee",
                    "thread_id": "policy-followup",
                },
                config=config,
            )
            followup = await graph.ainvoke(
                {
                    "message": "Which team owns it?",
                    "caller_employee_id": "E001",
                    "caller_group": "Regular Employee",
                    "thread_id": "policy-followup",
                },
                config=config,
            )

        assert len(executor.calls) == 2
        assert [call[0] for call in executor.calls] == [
            "search_knowledge_base",
            "search_knowledge_base",
        ]
        assert [call["name"] for call in followup["tool_history"]] == [
            "search_knowledge_base"
        ]
        assert "IT owns" in followup["answer"]
        checks = evaluate_turn(
            {
                "required_facts": ["IT"],
                "expected_sources": [
                    "documents/policies/remote_work_policy.md"
                ],
                "should_use_tools": True,
            },
            followup,
        )
        assert all(check.passed is not False for check in checks)

    import asyncio

    asyncio.run(run())


def test_maya_manager_playbook_request_uses_permission_filtered_search(
    tmp_path: Path,
) -> None:
    class NoManagerDocumentsExecutor(FakeToolExecutor):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            if name == "search_knowledge_base":
                self.calls.append((name, dict(arguments)))
                if arguments["caller_employee_id"] == "E006":
                    return [
                        {
                            "source": (
                                "documents/manager_playbook/giving-feedback.md"
                            ),
                            "text": "Give feedback with specific examples.",
                        }
                    ]
                return [
                    {
                        "source": "documents/policies/remote_work_policy.md",
                        "text": "Remote work policy.",
                    }
                ]
            return await super().call_tool(name, arguments)

    async def run() -> None:
        model = FakeModel(
            [
                _classification(scope="maya", intent="policy_question"),
            ]
        )
        executor = NoManagerDocumentsExecutor()
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=executor,
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            result = await graph.ainvoke(
                {
                    "message": "What does the manager playbook say about giving feedback?",
                    "caller_employee_id": "E001",
                    "caller_group": "Regular Employee",
                    "thread_id": "maya-manager-playbook",
                },
                config={
                    "configurable": {"thread_id": "maya-manager-playbook"}
                },
            )

        assert result["answer"] == (
            "I couldn't find information available to you for that request "
            "[conversation:turn-1] [database:search_knowledge_base]."
        )
        assert result["selected_tools"] == [
            "search_knowledge_base",
            "list_policies",
        ]
        assert [name for name, _ in executor.calls] == ["search_knowledge_base"]
        assert all(
            "manager_playbook" not in str(item)
            for item in result["tool_history"]
        )
        assert len(model.calls) == 1
        assert result["tool_history"][0]["result"] == []

        manager_model = FakeModel(
            [
                _classification(scope="maya", intent="policy_question"),
                (
                    "The manager guide recommends specific examples "
                    "[documents/manager_playbook/giving-feedback.md]."
                ),
            ]
        )
        async with open_checkpointer(
            tmp_path / "manager-checkpoints.sqlite3"
        ) as checkpointer:
            manager_graph = build_agent_graph(
                model=manager_model,
                tool_executor=executor,
                approval_store=ApprovalStore(
                    tmp_path / "manager-approvals.sqlite3"
                ),
                checkpointer=checkpointer,
            )
            manager_result = await manager_graph.ainvoke(
                {
                    "message": "What does the manager guide say about giving feedback?",
                    "caller_employee_id": "E006",
                    "caller_group": "Regular Employee",
                    "thread_id": "manager-playbook",
                },
                config={"configurable": {"thread_id": "manager-playbook"}},
            )
        assert "specific examples" in manager_result["answer"]
        assert manager_result["tool_history"][0]["result"][0]["source"] == (
            "documents/manager_playbook/giving-feedback.md"
        )

    import asyncio

    asyncio.run(run())


class FlakyTicketExecutor(FakeToolExecutor):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "list_employee_tickets" and arguments.get("employee_id") != "E010":
            self.calls.append((name, dict(arguments)))
            raise RuntimeError("Caller 'E004' is not permitted to access employee 'Rachel'")
        return await super().call_tool(name, arguments)


def test_malformed_plan_is_retried_and_tool_errors_are_returned_to_the_model(
    tmp_path: Path,
) -> None:
    def plan(employee_id: str) -> str:
        return json.dumps(
            {
                "tool_calls": [
                    {
                        "name": "list_employee_tickets",
                        "arguments": {"employee_id": employee_id},
                    }
                ],
                "answer": None,
            }
        )

    async def run() -> dict[str, Any]:
        model = FakeModel(
            [
                _classification(scope="webex", intent="ticket_status", system_name="Webex"),
                '{"tool_calls": ["list_employee_tickets"]}',
                plan("Rachel"),
                plan("E010"),
                "Rachel's Webex ticket T001 is open.",
            ]
        )
        executor = FlakyTicketExecutor()
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=model,
                tool_executor=executor,
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            result = await graph.ainvoke(
                {
                    "message": "What is the status of Rachel's Webex ticket?",
                    "caller_employee_id": "E004",
                    "caller_group": "HR Group",
                    "thread_id": "flaky",
                },
                config={"configurable": {"thread_id": "flaky"}},
            )
        assert "plan_feedback" in model.calls[2][0]
        assert "error" in json.loads(model.calls[3][0])["tool_results"][0]["result"]
        return result

    result = asyncio.run(run())

    assert result["status"] == "completed"
    assert result["answer"] == "Rachel's Webex ticket T001 is open."
    assert [entry["result"] for entry in result["tool_history"]] == [
        {"error": "Caller 'E004' is not permitted to access employee 'Rachel'"},
        [{"ticket_id": "T001", "status": "open"}],
    ]


class UnknownEmployeeExecutor(FakeToolExecutor):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_employee":
            self.calls.append((name, dict(arguments)))
            raise RuntimeError("Caller 'E004' is not permitted to access employee 'rrachel'")
        return await super().call_tool(name, arguments)


def test_unresolvable_target_blocks_the_turn_without_any_further_calls(
    tmp_path: Path,
) -> None:
    async def run() -> tuple[dict[str, Any], UnknownEmployeeExecutor]:
        executor = UnknownEmployeeExecutor()
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=FakeModel(
                    [
                        _classification(
                            scope="webex",
                            intent="access_request",
                            action_requested=True,
                            target_employee_id="rrachel",
                            system_name="Webex",
                        )
                    ]
                ),
                tool_executor=executor,
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            result = await graph.ainvoke(
                {
                    "message": "file webex for rrachel",
                    "caller_employee_id": "E004",
                    "caller_group": "HR Group",
                    "thread_id": "unknown-target",
                },
                config={"configurable": {"thread_id": "unknown-target"}},
            )
        return result, executor

    result, executor = asyncio.run(run())

    assert result["status"] == "blocked"
    assert "no action was taken" in result["answer"]
    assert [name for name, _ in executor.calls if name != "search_knowledge_base"] == [
        "get_employee"
    ]


def test_mislabeled_classifier_scope_and_intent_fall_back_instead_of_failing(
    tmp_path: Path,
) -> None:
    async def run() -> dict[str, Any]:
        async with open_checkpointer(tmp_path / "checkpoints.sqlite3") as checkpointer:
            graph = build_agent_graph(
                model=FakeModel(
                    [
                        _classification(scope="other", intent="ground_rules"),
                        json.dumps(
                            {"tool_calls": [], "answer": "Noted [conversation:turn-1]."}
                        ),
                    ]
                ),
                tool_executor=FakeToolExecutor(),
                approval_store=ApprovalStore(tmp_path / "approvals.sqlite3"),
                checkpointer=checkpointer,
            )
            return await graph.ainvoke(
                {
                    "message": "Ground rules: don't file anything yet. Got it?",
                    "caller_employee_id": "E004",
                    "caller_group": "HR Group",
                    "thread_id": "rules",
                },
                config={"configurable": {"thread_id": "rules"}},
            )

    result = asyncio.run(run())

    assert (result["scope"], result["intent"], result["status"]) == (
        "maya",
        "other",
        "completed",
    )
    assert not {"create_access_request", "create_ticket"} & set(result["selected_tools"])
