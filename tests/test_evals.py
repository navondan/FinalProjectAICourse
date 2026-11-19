import json

from novaops.evals.checks import ALWAYS_REASONABLE, evaluate_turn
from novaops.evals.compare import compare_reports
from novaops.evals.judges import JUDGE_RUBRICS, webex_overstatement_score
from novaops.evals.runner import evaluate_records
from novaops.evals.register_judges import build_judge_requests, register_judges
from novaops.evals.session_checks import (
    check_citation_coverage,
    check_closed_branch_tool_choice,
    check_constraint_carry,
    check_idempotent_replay,
    check_restart_lineage,
)


def test_evaluate_turn_checks_binding_expectations_and_reasonable_tool_mapping() -> None:
    checks = evaluate_turn(
        {
            "required_facts": ["five|5", "laptop"],
            "forbidden_facts": ["password"],
            "forbidden_tools": ["create_access_request"],
            "expected_tools": ["get_policy"],
            "expected_sources": ["documents/policies/remote_work.md"],
            "forbidden_sources": ["documents/hr/private.md"],
            "should_use_tools": True,
        },
        {
            "answer": "The policy allows five days with a work laptop.",
            "status": "completed",
            "tool_history": [
                {
                    "name": "search_knowledge_base",
                    "arguments": {},
                    "result": {
                        "source": "documents/policies/remote_work.md",
                        "text": "Policy",
                    },
                }
            ],
        },
    )

    assert len(checks) == 8
    assert all(check.passed is True for check in checks)
    assert "search_knowledge_base" in ALWAYS_REASONABLE


def test_golden_sources_match_as_path_suffixes() -> None:
    result = {
        "answer": "ok",
        "status": "completed",
        "evidence": [
            {"source": "documents/policies/equipment_policy.md"},
            {"source": "documents/manager_playbook/promotion.md"},
        ],
    }

    checks = {
        check.name: check
        for check in evaluate_turn(
            {
                "expected_sources": ["equipment_policy.md"],
                "forbidden_sources": ["manager_playbook/promotion.md"],
            },
            result,
        )
    }

    assert checks["expected_sources"].passed is True
    assert checks["forbidden_sources"].passed is False  # a leak is now caught
    assert evaluate_turn({"expected_sources": ["policy.md"]}, result)[4].passed is False


def test_expected_tools_allow_only_known_read_only_architecture_equivalents() -> None:
    result = {
        "answer": "The policy says five days.",
        "status": "completed",
        "tool_history": [{"name": "create_ticket"}],
    }

    checks = evaluate_turn({"expected_tools": ["get_policy"]}, result)

    assert checks[3].passed is False


def test_webex_overstatement_judge_rejects_pending_access_claims() -> None:
    assert webex_overstatement_score("pending", "Your access has been granted.") == 0.0
    assert webex_overstatement_score(
        "pending_approval", "Access has not been granted; the request is pending."
    ) == 1.0
    assert webex_overstatement_score("completed", "Access has been granted.") == 1.0
    assert "webex_overstatement" in JUDGE_RUBRICS


def test_langfuse_judges_build_with_numeric_bedrock_schemas() -> None:
    requests = build_judge_requests("us.amazon.nova-2-lite-v1:0")

    assert len(requests) == 4
    assert requests[-1].model_config_.provider == "bedrock"
    assert requests[-1].model_config_.model == "us.amazon.nova-2-lite-v1:0"
    assert requests[-1].output_definition.data_type == "NUMERIC"


def test_judge_registration_dry_run_does_not_send_credentials(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret-test")

    requests = register_judges()
    rendered = json.dumps(requests)

    assert len(requests) == 4
    assert "secret-test" not in rendered
    assert "public-test" not in rendered


def test_eval_runner_and_report_comparer() -> None:
    golden = {
        "sessions": [
            {
                "id": "S-test",
                "turns": [
                    {
                        "n": 1,
                        "required_facts": ["Webex"],
                        "forbidden_tools": ["create_ticket"],
                        "should_use_tools": True,
                    }
                ],
            }
        ],
        "single_turn_cases": [],
    }
    current = evaluate_records(
        golden,
        {
            "S-test:1": {
                "answer": "Webex is pending.",
                "status": "pending",
                "tool_history": [{"name": "list_employee_tickets"}],
            }
        },
    )
    baseline = {
        **current,
        "score": 0.0,
        "cases": [
            {
                "case_id": "S-test:1",
                "missing": False,
                "checks": [
                    {"name": "terminal_status", "passed": True},
                    {"name": "should_use_tools", "passed": False},
                ],
            }
        ],
    }

    comparison = compare_reports(current, baseline)

    assert current["score"] == 1.0
    assert comparison["improvements"] == [
        {"case_id": "S-test:1", "check": "should_use_tools"}
    ]


def test_unshipped_session_checks_cover_context_branch_citations_and_replay() -> None:
    assert check_constraint_carry(
        ["The Q3 freeze applies to this purchase."],
        "The Q3 freeze means Finance must review this.",
        ["Q3 freeze"],
    ).passed
    assert check_closed_branch_tool_choice(
        "webex", "ticket_status", ["list_employee_tickets"]
    ).passed
    assert not check_closed_branch_tool_choice(
        "webex", "ticket_status", ["search_hr_documents"]
    ).passed
    assert check_citation_coverage(
        "The request is pending [database:access_requests#AR005].",
        ["The request is pending"],
    ).passed
    assert check_idempotent_replay(["AR005"], ["AR005"]).passed


def test_restart_check_requires_the_saved_workflow_request_id() -> None:
    result = check_restart_lineage(
        {"workflow_request_id": "request-1"},
        {"workflow_request_id": "request-1", "request_id": "request-2", "status": "pending"},
        expected_status="pending",
    )

    assert result.passed is True


def test_eval_cli_scores_recorded_json_without_agent_calls(
    tmp_path, monkeypatch, capsys
) -> None:
    from novaops.cli import main

    runs_path = tmp_path / "runs.json"
    runs_path.write_text(
        json.dumps(
            {
                "S1-quick-policy:1": {
                    "answer": "The policy allows five days with a work laptop.",
                    "status": "completed",
                    "tool_history": [{"name": "get_policy", "arguments": {}, "result": {}}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["novaops", "eval", "--runs", str(runs_path)],
    )

    main()

    report = json.loads(capsys.readouterr().out)
    assert report["score"] == 1.0
    assert report["cases"][0]["case_id"] == "S1-quick-policy:1"
