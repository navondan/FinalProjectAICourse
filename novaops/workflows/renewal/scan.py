"""Portfolio scan: which contracts need attention on `as_of`, and the facts behind each.

Pure reads. Every fact carries the record it came from so a reviewer can check it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from novaops.workflows.renewal import rules
from novaops.workflows.renewal.store import RenewalStore

OWNER_MEMO = "documents/internal_memos/contract_owner_cleanup_memo.md"
SECURITY_MEMO = "documents/internal_memos/security_review_update.md"
FINANCE_MEMO = "documents/internal_memos/saas_renewal_freeze_q3.md"
POLICY = "documents/policies/contract_renewal_policy.md"
_OWNER_LINE = re.compile(r"^owner_department:\s*(.+)$", re.MULTILINE)


def scan_portfolio(store: RenewalStore, as_of: date) -> list[dict[str, Any]]:
    contracts = store.all(
        """SELECT c.*, v.vendor_name, d.name AS db_owner_department, d.default_manager_id
           FROM contracts c
           JOIN vendors v ON v.vendor_id = c.vendor_id
           LEFT JOIN departments d ON d.department_id = c.owner_department_id
           ORDER BY c.end_date, c.contract_id"""
    )
    open_runs = {
        row["contract_id"]
        for row in store.all(
            "SELECT contract_id FROM renewal_runs WHERE stage NOT IN ('applied', 'rejected_internally')"
        )
    }
    cases = []
    for contract in contracts:
        timing = rules.timing(
            as_of, date.fromisoformat(contract["end_date"]), int(contract["renewal_notice_days"])
        )
        if not timing.in_window and not (timing.expired and contract["contract_id"] in open_runs):
            continue
        cases.append(_case(store, contract, timing, as_of))
    return cases


def _case(store: RenewalStore, contract: dict[str, Any], timing: rules.Timing, as_of: date) -> dict[str, Any]:
    contract_id = contract["contract_id"]
    subscription = store.one(
        "SELECT * FROM software_subscriptions WHERE contract_id = ?", (contract_id,)
    )
    blocked = store.all(
        """SELECT request_id, employee_id, status, decision_reason FROM access_requests
           WHERE system_id = ? AND status = 'blocked' ORDER BY request_id""",
        (contract["system_id"],),
    )
    blocked_ids = [row["request_id"] for row in blocked]
    open_approvals = (
        store.all(
            f"""SELECT approval_id, request_type, request_id, approver_id, status, decision_reason
                FROM approvals WHERE status IN ('needed', 'pending')
                AND request_id IN ({','.join('?' * len(blocked_ids))}) ORDER BY approval_id""",
            tuple(blocked_ids),
        )
        if blocked_ids
        else []
    )
    document_owner = _document_owner(store.dataset_dir, contract["document_path"])
    db_owner = contract["db_owner_department"]
    owner_conflict = bool(document_owner and db_owner and document_owner != db_owner)
    ambiguous_terms = "ambiguous" in str(contract["renewal_type"]).lower()

    seat_limit = subscription["seat_limit"] if subscription else None
    active = subscription["active_seats"] if subscription else None
    utilization = round(active / seat_limit, 3) if seat_limit else None
    over_capacity = bool(seat_limit and active is not None and active > seat_limit) or bool(blocked)

    security_required = bool(contract["security_review_required"]) or (
        _vendor_key(contract["vendor_name"]) in rules.SECURITY_REVIEW_MEMO_VENDORS
    )
    signals: list[str] = []
    if timing.expired:
        signals.append(f"term ended {contract['end_date']} while its renewal run is still unresolved")
    elif timing.notice_overdue:
        signals.append(
            f"notice deadline {timing.notice_deadline} has passed; contract not expired (ends {contract['end_date']})"
        )
    elif timing.urgent:
        signals.append(f"notice deadline {timing.notice_deadline} is within {rules.URGENT_NOTICE_DAYS} days")
    if seat_limit is not None:
        signals.append(f"{active} active of {seat_limit} contracted seats (utilization {utilization})")
    if blocked:
        signals.append(f"blocked access requests waiting on capacity: {', '.join(blocked_ids)}")

    open_issues: list[str] = []
    if owner_conflict:
        open_issues.append(
            f"owner conflict: database says {db_owner}, contract document says {document_owner}; "
            f"{OWNER_MEMO} says contract documents govern until cleanup completes, cleanup not confirmed"
        )
    if ambiguous_terms:
        open_issues.append(f"renewal type is '{contract['renewal_type']}': Finance must rule ({POLICY})")
    for approval in open_approvals:
        open_issues.append(
            f"open approval {approval['approval_id']} ({approval['request_type']}) with {approval['approver_id']}: {approval['status']}"
        )
    if security_required:
        open_issues.append("security review required before renewal or material expansion")
    quote = rules.REFERENCE_QUOTES.get(contract_id)
    if over_capacity and quote is None:
        open_issues.append("no supplier quote on file for an expansion")

    has_conflict = owner_conflict or ambiguous_terms
    return {
        "contract_id": contract_id,
        "vendor": contract["vendor_name"],
        "system_id": contract["system_id"],
        "subscription_id": subscription["subscription_id"] if subscription else None,
        "as_of": as_of.isoformat(),
        "current_terms": {
            "start_date": contract["start_date"],
            "end_date": contract["end_date"],
            "renewal_type": contract["renewal_type"],
            "renewal_notice_days": contract["renewal_notice_days"],
            "annual_cost_usd": contract["annual_cost"],
            "seat_limit": seat_limit,
        },
        "timing": {
            "days_to_end": timing.days_to_end,
            "review_opened": timing.review_opens.isoformat(),
            "notice_deadline": timing.notice_deadline.isoformat(),
            "notice_overdue": timing.notice_overdue,
            "expired": timing.expired,
            "urgent": timing.urgent,
        },
        "capacity": {
            "active_seats": active,
            "seat_limit": seat_limit,
            "utilization": utilization,
            "blocked_requests": blocked,
        },
        "owner": {
            "database": db_owner,
            "contract_document": document_owner,
            "conflict": owner_conflict,
            "approver_id": None if owner_conflict else contract["default_manager_id"],
        },
        "open_approvals": open_approvals,
        "security_review_required": security_required,
        "reference_quote": quote,
        "signals": signals,
        "open_issues": open_issues,
        "allowed_actions": list(
            rules.allowed_actions(
                has_conflict=has_conflict, over_capacity=over_capacity, utilization=utilization
            )
        ),
        "sources": [
            source
            for source in (
            f"database: contracts/{contract_id}",
            f"database: software_subscriptions/{subscription['subscription_id']}" if subscription else None,
            contract["document_path"],
            POLICY,
            SECURITY_MEMO if security_required else None,
            OWNER_MEMO if owner_conflict else None,
            FINANCE_MEMO if over_capacity else None,
            )
            if source
        ],
    }


def _document_owner(dataset_dir: Path, document_path: str | None) -> str | None:
    if not document_path:
        return None
    path = dataset_dir / document_path
    if not path.is_file():
        return None
    match = _OWNER_LINE.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else None


def _vendor_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())
