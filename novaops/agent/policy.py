"""Scope-specific tool visibility and the persisted write-approval gate."""

from __future__ import annotations

from novaops.agent.write_gate import ApprovalStore, can_release_write

WRITE_TOOLS = frozenset({"create_access_request", "create_ticket"})

LOADOUTS: dict[str, dict[str, tuple[str, ...]]] = {
    "maya": {
        "policy_question": ("search_knowledge_base", "list_policies"),
        "employee_lookup": ("get_employee",),
        "onboarding_status": (
            "get_employee",
            "list_onboarding_tasks",
            "check_asset_inventory",
            "check_software_subscription",
            "get_policy",
            "search_knowledge_base",
        ),
        "equipment_request": (
            "check_asset_inventory",
            "search_knowledge_base",
            "get_policy",
        ),
        "subscription_review": ("check_software_subscription", "search_knowledge_base"),
        "access_request": (
            "get_employee",
            "check_software_subscription",
            "search_knowledge_base",
        ),
        "ticket_status": ("list_employee_tickets",),
        "other": ("search_knowledge_base",),
    },
    "webex": {
        "policy_question": ("search_knowledge_base", "get_policy", "list_policies"),
        "employee_lookup": ("get_employee",),
        "onboarding_status": (
            "get_employee",
            "list_onboarding_tasks",
            "check_asset_inventory",
        ),
        "equipment_request": ("check_asset_inventory", "search_knowledge_base"),
        "subscription_review": (
            "check_software_subscription",
            "report_team_webex_seat_assignment_gap",
            "search_knowledge_base",
        ),
        "access_request": (
            "get_employee",
            "check_software_subscription",
            "list_employee_access_requests",
            "list_employee_tickets",
            "search_knowledge_base",
            "list_policies",
            "get_policy",
        ),
        "ticket_status": ("list_employee_tickets",),
        "other": ("search_knowledge_base",),
    },
}


def select_tools(
    *,
    scope: str,
    intent: str,
    action_requested: bool,
    caller_group: str | None,
    thread_id: str,
    employee_id: str | None,
    system_name: str | None,
    access_level: str | None,
    business_justification: str | None,
    approval_store: ApprovalStore,
) -> list[str]:
    scope_loadouts = LOADOUTS.get(scope)
    if scope_loadouts is None:
        return []
    selected = list(scope_loadouts.get(intent, ()))

    if "search_hr_documents" in selected and caller_group != "HR Group":
        selected.remove("search_hr_documents")

    if scope == "maya" and intent == "onboarding_status" and caller_group == "HR Group":
        selected.append("search_hr_documents")

    if (
        action_requested
        and scope == "webex"
        and intent == "access_request"
        and can_release_write(
            approval_store,
            thread_id=thread_id,
            scope=scope,
            employee_id=employee_id,
            tool_name="create_access_request",
            system_name=system_name,
            access_level=access_level,
            business_justification=business_justification,
        )
    ):
        selected.append("create_access_request")

    return list(dict.fromkeys(selected))
