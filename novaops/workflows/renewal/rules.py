"""Deterministic renewal business rules (PRD v1.3 §7.1, contract_renewal_policy, Q3 memos)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

REVIEW_WINDOW_DAYS = 90
URGENT_NOTICE_DAYS = 45
FINANCE_INCREMENT_THRESHOLD_USD = 7500  # saas_renewal_freeze_q3.md supersedes the 5,000 policy figure
LOW_UTILIZATION_RATIO = 0.6

ACTIONS = ("renew_as_is", "expand", "reduce", "cancel", "escalate")

# security_review_update.md (2026-06-25)
SECURITY_REVIEW_MEMO_VENDORS = frozenset(
    {
        "okta",
        "bamboohr",
        "datadog",
        "aws",
        "securesign",
        "cloudbackup",
        "mailguard",
        "salesforce",
        "zoominfo",
    }
)

# Supplier quotes on file. The workflow does not invent prices: a run that needs a
# commercial change and has no quote on file stays with a human.
REFERENCE_QUOTES: dict[str, dict[str, object]] = {
    "C001": {
        "seat_limit": 50,
        "annual_cost_usd": 22000,
        "source": "NovaOps Renewal PRD v1.3 §7.2 — Webex reference case, proposed annual price",
    }
}


@dataclass(frozen=True, slots=True)
class Timing:
    days_to_end: int
    review_opens: date
    notice_deadline: date
    in_window: bool
    expired: bool
    notice_overdue: bool
    urgent: bool


def timing(as_of: date, end_date: date, notice_days: int) -> Timing:
    days_to_end = (end_date - as_of).days
    notice_deadline = end_date - timedelta(days=notice_days)
    expired = end_date < as_of
    notice_overdue = notice_deadline < as_of
    urgent = not expired and (notice_overdue or (notice_deadline - as_of).days <= URGENT_NOTICE_DAYS)
    return Timing(
        days_to_end=days_to_end,
        review_opens=end_date - timedelta(days=REVIEW_WINDOW_DAYS),
        notice_deadline=notice_deadline,
        in_window=0 <= days_to_end <= REVIEW_WINDOW_DAYS,
        expired=expired,
        notice_overdue=notice_overdue,
        urgent=urgent,
    )


def allowed_actions(*, has_conflict: bool, over_capacity: bool, utilization: float | None) -> tuple[str, ...]:
    """The facts bound what a recommender may propose; the model chooses inside the bound."""
    if has_conflict:
        return ("escalate",)
    if over_capacity:
        return ("expand", "escalate")
    if utilization is not None and utilization < LOW_UTILIZATION_RATIO:
        return ("reduce", "renew_as_is", "escalate")
    return ("renew_as_is", "reduce", "escalate")


def rule_action(*, has_conflict: bool, over_capacity: bool, utilization: float | None) -> str:
    return allowed_actions(
        has_conflict=has_conflict, over_capacity=over_capacity, utilization=utilization
    )[0]


def next_term(end_date: date) -> tuple[date, date]:
    start = end_date + timedelta(days=1)
    try:
        end = start.replace(year=start.year + 1) - timedelta(days=1)
    except ValueError:  # Feb 29
        end = start.replace(year=start.year + 1, day=28)
    return start, end


def finance_required_by_threshold(current_cost: int, proposed_cost: int) -> bool:
    return proposed_cost - current_cost > FINANCE_INCREMENT_THRESHOLD_USD
