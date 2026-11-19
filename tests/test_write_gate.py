from pathlib import Path

from novaops.agent.policy import LOADOUTS, WRITE_TOOLS, select_tools
from novaops.agent.write_gate import ApprovalStore, can_release_write


def _record(store: ApprovalStore, *, status: str = "approved") -> None:
    store.record_decision(
        thread_id="thread-1",
        scope="webex",
        employee_id="E010",
        tool_name="create_access_request",
        system_name="Webex",
        access_level="standard_user",
        business_justification="Customer onboarding calls require Webex.",
        status=status,
        approver_employee_id="E006",
        reason="Test decision.",
    )


def _select(
    store: ApprovalStore,
    *,
    thread_id: str = "thread-1",
    scope: str = "webex",
    intent: str = "access_request",
    employee_id: str | None = "E010",
    system_name: str | None = "Webex",
    access_level: str | None = "standard_user",
    business_justification: str | None = "Customer onboarding calls require Webex.",
    action_requested: bool = True,
) -> list[str]:
    return select_tools(
        scope=scope,
        intent=intent,
        action_requested=action_requested,
        caller_group="Regular Employee",
        thread_id=thread_id,
        employee_id=employee_id,
        system_name=system_name,
        access_level=access_level,
        business_justification=business_justification,
        approval_store=store,
    )


def test_read_tool_visibility_is_scoped_and_write_is_not_inferred(
    tmp_path: Path,
) -> None:
    store = ApprovalStore(tmp_path / "approvals.sqlite3")

    webex_tools = _select(store)
    maya_tools = _select(store, scope="maya", intent="employee_lookup")
    ticket_tools = _select(store, intent="ticket_status", action_requested=True)

    assert "check_software_subscription" in webex_tools
    assert "list_employee_tickets" in webex_tools
    assert "get_employee" in maya_tools
    assert "check_software_subscription" not in maya_tools
    assert ticket_tools == ["list_employee_tickets"]
    assert WRITE_TOOLS.isdisjoint(webex_tools + maya_tools + ticket_tools)


def test_only_exact_persisted_approval_releases_the_access_write(
    tmp_path: Path,
) -> None:
    approval_path = tmp_path / "approvals.sqlite3"
    store = ApprovalStore(approval_path)
    assert "create_access_request" not in _select(store)

    _record(store)

    reopened_store = ApprovalStore(approval_path)
    approved_tools = _select(reopened_store)
    assert "create_access_request" in approved_tools
    assert "create_ticket" not in approved_tools
    assert "create_access_request" not in _select(reopened_store, thread_id="other-thread")
    assert "create_access_request" not in _select(reopened_store, scope="maya")
    assert "create_access_request" not in _select(reopened_store, employee_id="E001")
    assert "create_access_request" not in _select(reopened_store, system_name="Salesforce")
    assert "create_access_request" not in _select(reopened_store, access_level="admin")
    assert "create_access_request" not in _select(
        reopened_store, business_justification="A different request."
    )
    assert "create_access_request" not in _select(reopened_store, action_requested=False)


def test_rejected_decision_does_not_release_write(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "approvals.sqlite3")
    _record(store, status="rejected")

    assert "create_access_request" not in _select(store)
    assert not can_release_write(
        store,
        thread_id="thread-1",
        scope="webex",
        employee_id="E010",
        tool_name="create_ticket",
        system_name="Webex",
        access_level="standard_user",
        business_justification="Customer onboarding calls require Webex.",
    )


def test_loadouts_have_separate_maya_and_webex_scopes() -> None:
    assert LOADOUTS["maya"]["onboarding_status"] != LOADOUTS["webex"][
        "onboarding_status"
    ]
