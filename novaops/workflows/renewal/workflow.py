"""Workflow 4 — event-driven contract renewal as an explicit, durable state machine.

Stages of a renewal run (each transition is persisted in `renewal_run_transitions`):

    awaiting_internal_decision -> awaiting_clearances -> awaiting_supplier
        -> confirmed_awaiting_effective_date -> applied
    any stage -> needs_human | rejected_internally

Nothing lives only in memory: a new process re-reads the run and continues. Every inbound
event / reply id is processed at most once, and every outbound message is keyed by its
business effect, so a replay cannot create a second update or a second notification.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from novaops.core.tracing import workflow_step
from novaops.workflows.renewal import rules
from novaops.workflows.renewal.matching import evaluate_reply
from novaops.workflows.renewal.model import ModelOutputError, Recommender, ReplyExtractor, RulesRecommender
from novaops.workflows.renewal.ports import (
    DeliveryFailed,
    DeliveryUncertain,
    NotificationAdapter,
    OutboundMessage,
    ProviderEmailAdapter,
    parse_reply,
)
from novaops.workflows.renewal.scan import scan_portfolio
from novaops.workflows.renewal.store import RenewalStore, dumps, loads, utc_now

OPEN_STAGES = ("awaiting_internal_decision", "awaiting_clearances")
EVENT_KINDS = ("internal_approval", "finance_clearance", "security_review")


class RenewalWorkflow:
    def __init__(
        self,
        store: RenewalStore,
        *,
        notifications: NotificationAdapter,
        provider: ProviderEmailAdapter,
        recommender: Recommender,
        extractor: ReplyExtractor | None,
        decision_schema: Mapping[str, Any],
    ) -> None:
        self.store = store
        self.notifications = notifications
        self.provider = provider
        self.recommender = recommender
        self.extractor = extractor
        self.decision_validator = Draft202012Validator(decision_schema, format_checker=FormatChecker())

    # == schedule ===================================================================

    def run_schedule(self, as_of: date) -> dict[str, Any]:
        with workflow_step("renewal.schedule", input={"as_of": as_of.isoformat()}) as step:
            applied = self._apply_due(as_of)
            with workflow_step("renewal.scan", input={"as_of": as_of.isoformat()}) as scan_step:
                cases = scan_portfolio(self.store, as_of)
                scan_step.update(
                    output=[
                        {
                            "contract_id": case["contract_id"],
                            "vendor": case["vendor"],
                            "urgent": case["timing"]["urgent"],
                            "notice_overdue": case["timing"]["notice_overdue"],
                            "expired": case["timing"]["expired"],
                        }
                        for case in cases
                    ]
                )
            recommendations = self._recommend(cases, as_of)
            self._record_cases(cases, recommendations, as_of)
            opened = self._open_runs(cases, recommendations, as_of)
            delivery = self.dispatch()
            summary = {
                "as_of": as_of.isoformat(),
                "cases": [
                    {
                        "contract_id": case["contract_id"],
                        "vendor": case["vendor"],
                        "urgent": case["timing"]["urgent"],
                        "notice_overdue": case["timing"]["notice_overdue"],
                        "expired": case["timing"]["expired"],
                        "recommendation": recommendations[case["contract_id"]]["action"],
                    }
                    for case in cases
                ],
                "runs_opened": opened,
                "agreements_applied": applied,
                "delivery": delivery,
            }
            step.update(output=summary)
        return summary

    def _recommend(self, cases: list[dict[str, Any]], as_of: date) -> dict[str, dict[str, Any]]:
        stored: dict[str, dict[str, Any]] = {}
        pending = []
        for case in cases:
            row = self.store.one(
                "SELECT recommendation_json FROM renewal_recommendations WHERE contract_id = ? AND term_end_date = ?",
                (case["contract_id"], case["current_terms"]["end_date"]),
            )
            if row:
                stored[case["contract_id"]] = loads(row["recommendation_json"])
            else:
                pending.append(case)
        if not pending:
            return stored

        source = "model"
        try:
            proposals = self.recommender.recommend(pending)
        except ModelOutputError as exc:
            proposals, source = RulesRecommender().recommend(pending), f"rules (model failed: {exc})"
        with self.store.transaction() as connection:
            for case in pending:
                recommendation = _bounded_recommendation(case, proposals.get(case["contract_id"]), source)
                connection.execute(
                    "INSERT OR IGNORE INTO renewal_recommendations VALUES (?, ?, ?, ?)",
                    (case["contract_id"], case["current_terms"]["end_date"], dumps(recommendation), as_of.isoformat()),
                )
                stored[case["contract_id"]] = recommendation
        return stored

    def _record_cases(self, cases: list[dict[str, Any]], recommendations: dict[str, Any], as_of: date) -> None:
        with self.store.transaction() as connection:
            for case in cases:
                recommendation = recommendations[case["contract_id"]]
                owner, next_action = _case_next_action(case, recommendation)
                deadline = case["current_terms"]["end_date"] if case["timing"]["notice_overdue"] else case["timing"]["notice_deadline"]
                connection.execute(
                    "INSERT OR REPLACE INTO renewal_cases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        case["contract_id"],
                        as_of.isoformat(),
                        dumps(case),
                        dumps(recommendation),
                        int(case["timing"]["urgent"]),
                        owner,
                        "; ".join(case["signals"] + case["open_issues"]) or "in review window",
                        next_action,
                        deadline,
                    ),
                )

    def _open_runs(self, cases: list[dict[str, Any]], recommendations: dict[str, Any], as_of: date) -> list[str]:
        opened = []
        for case in cases:
            if recommendations[case["contract_id"]]["action"] != "expand" or not case["reference_quote"]:
                continue
            if not case["owner"]["approver_id"]:
                continue
            run_id = f"RR-{_vendor_key(case['vendor']).upper()}-{case['current_terms']['end_date'][:4]}"
            scope = _requested_scope(case)
            clearances = _required_clearances(case, scope)
            with self.store.transaction() as connection:
                created = connection.execute(
                    """INSERT OR IGNORE INTO renewal_runs
                       (run_id, contract_id, subscription_id, approver_id, stage, status_reason, next_owner,
                        recommendation_json, requested_scope_json, required_clearances_json, created_as_of, updated_at)
                       VALUES (?, ?, ?, ?, 'awaiting_internal_decision', ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        case["contract_id"],
                        case["subscription_id"],
                        case["owner"]["approver_id"],
                        "internal approval requested for the scoped expansion",
                        case["owner"]["approver_id"],
                        dumps(recommendations[case["contract_id"]]),
                        dumps(scope),
                        dumps(clearances),
                        as_of.isoformat(),
                        utc_now(),
                    ),
                ).rowcount
                if not created:
                    continue
                connection.execute(
                    """INSERT INTO renewal_run_transitions (run_id, business_date, from_stage, to_stage, cause, recorded_at)
                       VALUES (?, ?, NULL, 'awaiting_internal_decision', 'schedule scan opened run', ?)""",
                    (run_id, as_of.isoformat(), utc_now()),
                )
                approver = _employee(connection, case["owner"]["approver_id"])
                RenewalStore.enqueue(
                    connection,
                    message_key=f"approval-request:{run_id}",
                    run_id=run_id,
                    channel="internal",
                    recipient=approver["email"],
                    subject=f"[{run_id}] Approval needed: {case['vendor']} renewal ({case['contract_id']})",
                    body=_approval_request_body(case, recommendations[case["contract_id"]], scope, clearances, approver),
                    created_on=as_of.isoformat(),
                )
            opened.append(run_id)
        return opened

    # == internal events ==============================================================

    def handle_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_id = str(event.get("event_id") or "")
        with workflow_step(
            "renewal.event",
            input={"event_id": event_id, "event_type": event.get("event_type"), "decision": event.get("decision")},
        ) as step:
            if not event_id or event.get("event_type") not in EVENT_KINDS:
                raise ValueError(f"Unsupported renewal event: {event_id or '<no id>'}")
            with self.store.transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM renewal_processed_events WHERE event_id = ?", (event_id,)
                ).fetchone():
                    result = {"event_id": event_id, "outcome": "duplicate_ignored"}
                else:
                    result = self._apply_event(connection, event)
                    connection.execute(
                        "INSERT INTO renewal_processed_events VALUES (?, ?, ?, ?, ?)",
                        (event_id, event.get("renewal_run_id"), event["event_type"], result["outcome"], utc_now()),
                    )
            result["delivery"] = self.dispatch()
            step.update(output=result)
        return result

    def _apply_event(self, connection: sqlite3.Connection, event: Mapping[str, Any]) -> dict[str, Any]:
        event_id = event["event_id"]
        run_id = event.get("renewal_run_id")
        run = connection.execute("SELECT * FROM renewal_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None or run["contract_id"] != event.get("contract_id"):
            return {"event_id": event_id, "outcome": "unmatched_no_effect"}
        kind = event["event_type"]
        business_date = str(event.get("decided_at", ""))[:10] or utc_now()[:10]
        actor = _employee(connection, str(event.get("actor_employee_id")))
        authorized, note = _authorize(kind, event, run, actor)

        connection.execute(
            "INSERT INTO renewal_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                run_id,
                kind,
                event.get("actor_employee_id"),
                event.get("decision"),
                event.get("approval_id"),
                dumps(event.get("approved_scope")) if event.get("approved_scope") else None,
                event.get("reason"),
                event.get("channel"),
                event.get("decided_at"),
                int(authorized),
                note,
            ),
        )
        if not authorized:
            return {"event_id": event_id, "outcome": "rejected_unauthorized", "note": note}

        stage = run["stage"]
        if kind == "internal_approval" and event.get("decision") == "rejected":
            if stage in OPEN_STAGES:
                RenewalStore.move_run(
                    connection, run_id, to_stage="rejected_internally",
                    reason=f"internal approver rejected: {event.get('reason')}", next_owner=run["approver_id"],
                    business_date=business_date, cause=event_id, approved_scope_json=None, approval_event_id=None,
                )
                return {"event_id": event_id, "outcome": "run_rejected_internally"}
            RenewalStore.move_run(
                connection, run_id, to_stage="needs_human",
                reason=f"internal rejection arrived after stage '{stage}'; supplier commitment must be reviewed by a human",
                next_owner=run["approver_id"], business_date=business_date, cause=event_id,
            )
            return {"event_id": event_id, "outcome": "escalated_late_rejection"}

        if stage not in OPEN_STAGES:
            return {"event_id": event_id, "outcome": f"recorded_no_transition (stage {stage})"}

        if kind == "internal_approval":
            scope = event.get("approved_scope") or {}
            problems = _scope_problems(scope, loads(run["requested_scope_json"]))
            if problems:
                RenewalStore.move_run(
                    connection, run_id, to_stage="needs_human", reason="approval unusable: " + "; ".join(problems),
                    next_owner=run["approver_id"], business_date=business_date, cause=event_id,
                )
                return {"event_id": event_id, "outcome": "escalated_bad_scope", "problems": problems}
            connection.execute(
                "UPDATE renewal_runs SET approved_scope_json = ?, approval_event_id = ? WHERE run_id = ?",
                (dumps(scope), event_id, run_id),
            )
        elif kind == "finance_clearance" and event.get("approval_id"):
            updated = connection.execute(
                """UPDATE approvals SET status = 'approved', decision_reason = ?
                   WHERE approval_id = ? AND status IN ('needed', 'pending')""",
                (f"{event.get('reason')} [{event_id}]", event["approval_id"]),
            ).rowcount
            if updated:
                connection.execute(
                    "INSERT OR IGNORE INTO audit_log VALUES (?, 'approval', ?, 'finance_clearance', ?, ?, ?)",
                    (f"AUD-{event_id}", event["approval_id"], event.get("actor_employee_id"), event.get("decided_at"),
                     f"{run_id}: {event.get('reason')}"),
                )
        stage_after = self._advance(connection, run_id, business_date, cause=event_id)
        return {"event_id": event_id, "outcome": "recorded", "stage": stage_after}

    def _advance(self, connection: sqlite3.Connection, run_id: str, business_date: str, *, cause: str) -> str:
        run = connection.execute("SELECT * FROM renewal_runs WHERE run_id = ?", (run_id,)).fetchone()
        if run["stage"] not in OPEN_STAGES or not run["approved_scope_json"]:
            return run["stage"]
        missing = _missing_clearances(connection, run)
        if missing:
            if run["stage"] != "awaiting_clearances" or run["status_reason"] != _waiting_reason(missing):
                RenewalStore.move_run(
                    connection, run_id, to_stage="awaiting_clearances", reason=_waiting_reason(missing),
                    next_owner=", ".join(item["owner"] for item in missing), business_date=business_date, cause=cause,
                )
            return "awaiting_clearances"

        contract = connection.execute(
            """SELECT c.*, v.vendor_name FROM contracts c JOIN vendors v ON v.vendor_id = c.vendor_id
               WHERE c.contract_id = ?""",
            (run["contract_id"],),
        ).fetchone()
        subscription = connection.execute(
            "SELECT * FROM software_subscriptions WHERE subscription_id = ?", (run["subscription_id"],)
        ).fetchone()
        scope = loads(run["approved_scope_json"])
        RenewalStore.enqueue(
            connection,
            message_key=f"supplier-request:{run_id}",
            run_id=run_id,
            channel="provider_email",
            recipient=f"renewals@{_vendor_key(contract['vendor_name'])}-vendor.example",
            subject=f"NovaOps renewal request for contract {contract['contract_id']}",
            body=_supplier_request_body(contract, subscription, scope),
            created_on=business_date,
            headers={"Message-ID": f"<{run_id}@novaops.example>"},
        )
        RenewalStore.move_run(
            connection, run_id, to_stage="awaiting_supplier",
            reason="approval and all required clearances recorded; supplier request issued for the approved scope",
            next_owner="supplier", business_date=business_date, cause=cause,
        )
        return "awaiting_supplier"

    # == supplier reply =================================================================

    def handle_reply(self, raw_email: str) -> dict[str, Any]:
        reply = parse_reply(raw_email)
        with workflow_step(
            "renewal.reply", input={"message_id": reply.message_id, "in_reply_to": reply.in_reply_to}
        ) as step:
            if self.store.one("SELECT 1 FROM renewal_provider_replies WHERE message_id = ?", (reply.message_id,)):
                result = {"message_id": reply.message_id, "outcome": "duplicate_ignored"}
                step.update(output=result)
                return result
            run_id = (reply.in_reply_to or "").strip("<>").split("@")[0] or None
            run = self.store.one("SELECT * FROM renewal_runs WHERE run_id = ?", (run_id,)) if run_id else None

            extracted: dict[str, Any] | None = None
            if run is None or run["stage"] != "awaiting_supplier":
                evaluation = {
                    "verdict": "escalate",
                    "category": "unexpected",
                    "reasons": [f"no renewal run awaiting a supplier reply for {reply.in_reply_to!r}"],
                    "next_owner": "procurement: review the unexpected supplier email",
                }
            else:
                try:
                    proposal = self.extractor.extract(reply) if self.extractor else None
                    if proposal is None:
                        raise ModelOutputError("no extractor configured")
                    extracted = {**proposal, "fixture_id": reply.fixture_id or reply.message_id, "message_id": reply.message_id}
                    errors = sorted(self.decision_validator.iter_errors(extracted), key=lambda e: list(e.path))
                    if errors:
                        raise ModelOutputError("extraction failed schema: " + "; ".join(e.message for e in errors[:3]))
                    with workflow_step("renewal.reply.match", input={"extracted": extracted}) as match_step:
                        evaluation = evaluate_reply(
                            extracted,
                            reply_text=reply.body,
                            contract_id=run["contract_id"],
                            approved_scope=loads(run["approved_scope_json"]),
                        )
                        match_step.update(output=evaluation)
                except ModelOutputError as exc:
                    evaluation = {
                        "verdict": "escalate",
                        "category": "extraction_failed",
                        "reasons": [str(exc)],
                        "next_owner": "procurement: read the supplier reply manually",
                    }
            result = self._record_reply(reply, run, extracted, evaluation)
            result["delivery"] = self.dispatch()
            step.update(output=result)
        return result

    def _record_reply(self, reply: Any, run: dict[str, Any] | None, extracted: dict[str, Any] | None,
                      evaluation: dict[str, Any]) -> dict[str, Any]:
        business_date = reply.received_on or utc_now()[:10]
        with self.store.transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO renewal_provider_replies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    reply.message_id,
                    run["run_id"] if run else None,
                    reply.fixture_id,
                    business_date,
                    reply.raw,
                    dumps(extracted) if extracted else None,
                    evaluation["verdict"] if evaluation["verdict"] == "record" else evaluation["category"],
                    dumps(evaluation["reasons"]),
                    utc_now(),
                ),
            ).rowcount
            if not inserted:
                return {"message_id": reply.message_id, "outcome": "duplicate_ignored"}
            if run is None or run["stage"] != "awaiting_supplier":
                return {"message_id": reply.message_id, "outcome": "unexpected_no_effect", **evaluation}

            run_id = run["run_id"]
            if evaluation["verdict"] == "record":
                connection.execute(
                    """INSERT INTO renewal_agreements
                       (agreement_id, run_id, contract_id, subscription_id, confirmation_id, seat_limit, annual_cost_usd,
                        term_start_date, term_end_date, effective_date, status, recorded_on, source_message_id,
                        approval_event_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed_awaiting_effective_date', ?, ?, ?)""",
                    (
                        f"AG-{run_id}",
                        run_id,
                        run["contract_id"],
                        run["subscription_id"],
                        extracted["confirmation_id"],
                        extracted["new_seat_limit"],
                        extracted["annual_cost_usd"],
                        extracted["term_start_date"],
                        extracted["term_end_date"],
                        extracted["term_start_date"],
                        business_date,
                        reply.message_id,
                        run["approval_event_id"],
                    ),
                )
                reason = (
                    f"supplier confirmation {extracted['confirmation_id']} recorded on {business_date}: "
                    f"confirmed, awaiting effective date {extracted['term_start_date']}; terms in effect unchanged"
                )
                RenewalStore.move_run(
                    connection, run_id, to_stage="confirmed_awaiting_effective_date", reason=reason,
                    next_owner="scheduler", business_date=business_date, cause=reply.message_id,
                )
            else:
                reason = f"supplier reply not recordable ({evaluation['category']}): " + "; ".join(evaluation["reasons"])
                RenewalStore.move_run(
                    connection, run_id, to_stage="needs_human", reason=reason,
                    next_owner=evaluation["next_owner"], business_date=business_date, cause=reply.message_id,
                )
            approver = _employee(connection, run["approver_id"])
            RenewalStore.enqueue(
                connection,
                message_key=f"reply-outcome:{reply.message_id}",
                run_id=run_id,
                channel="internal",
                recipient=approver["email"],
                subject=(
                    f"[{run_id}] Supplier confirmed the renewal; terms apply on the effective date"
                    if evaluation["verdict"] == "record"
                    else f"[{run_id}] Supplier reply needs a person: {evaluation['category']}"
                ),
                body=reason + (f"\nNext: {evaluation['next_owner']}" if evaluation["next_owner"] else ""),
                created_on=business_date,
            )
        return {"message_id": reply.message_id, "outcome": "recorded" if evaluation["verdict"] == "record" else "escalated", **evaluation}

    # == effective date ===================================================================

    def _apply_due(self, as_of: date) -> list[str]:
        due = self.store.all(
            """SELECT agreement_id FROM renewal_agreements
               WHERE status = 'confirmed_awaiting_effective_date' AND effective_date <= ? ORDER BY agreement_id""",
            (as_of.isoformat(),),
        )
        applied = []
        for row in due:
            with workflow_step("renewal.apply", input={"agreement_id": row["agreement_id"], "as_of": as_of.isoformat()}) as step:
                with self.store.transaction() as connection:
                    agreement = connection.execute(
                        "SELECT * FROM renewal_agreements WHERE agreement_id = ? AND status = 'confirmed_awaiting_effective_date'",
                        (row["agreement_id"],),
                    ).fetchone()
                    if agreement is None:
                        continue
                    outcome = self._apply_agreement(connection, agreement, as_of)
                step.update(output=outcome)
            applied.append(row["agreement_id"])
        return applied

    def _apply_agreement(self, connection: sqlite3.Connection, agreement: sqlite3.Row, as_of: date) -> dict[str, Any]:
        run = connection.execute("SELECT * FROM renewal_runs WHERE run_id = ?", (agreement["run_id"],)).fetchone()
        before = dict(connection.execute(
            "SELECT seat_limit, active_seats, annual_cost, renewal_date FROM software_subscriptions WHERE subscription_id = ?",
            (agreement["subscription_id"],),
        ).fetchone())
        connection.execute(
            """UPDATE software_subscriptions SET seat_limit = ?, annual_cost = ?, renewal_date = ?
               WHERE subscription_id = ?""",
            (agreement["seat_limit"], agreement["annual_cost_usd"], agreement["term_end_date"], agreement["subscription_id"]),
        )
        connection.execute(
            "UPDATE contracts SET start_date = ?, end_date = ?, annual_cost = ? WHERE contract_id = ?",
            (agreement["term_start_date"], agreement["term_end_date"], agreement["annual_cost_usd"], agreement["contract_id"]),
        )
        audit_id = f"AUD-{agreement['run_id']}-APPLY"
        connection.execute(
            "INSERT INTO audit_log VALUES (?, 'subscription', ?, 'renewal_applied', ?, ?, ?)",
            (
                audit_id,
                agreement["subscription_id"],
                run["approver_id"],
                f"{as_of.isoformat()}T00:00:00Z",
                f"{agreement['contract_id']} renewed: seat_limit {before['seat_limit']}->{agreement['seat_limit']}, "
                f"term {agreement['term_start_date']}..{agreement['term_end_date']}; approval {agreement['approval_event_id']}, "
                f"supplier confirmation {agreement['confirmation_id']} ({agreement['source_message_id']}). "
                f"active_seats unchanged at {before['active_seats']}.",
            ),
        )
        connection.execute(
            "UPDATE renewal_agreements SET status = 'applied', applied_on = ?, audit_event_id = ? WHERE agreement_id = ?",
            (as_of.isoformat(), audit_id, agreement["agreement_id"]),
        )
        RenewalStore.move_run(
            connection, run["run_id"], to_stage="applied",
            reason=f"applied on {as_of.isoformat()} (effective {agreement['effective_date']}); audit {audit_id}",
            next_owner=None, business_date=as_of.isoformat(), cause=audit_id,
        )
        notified = self._enqueue_capacity_notices(connection, run, agreement, as_of)
        return {"audit_event_id": audit_id, "before": before, "seat_limit_after": agreement["seat_limit"], "notified": notified}

    def _enqueue_capacity_notices(self, connection: sqlite3.Connection, run: sqlite3.Row, agreement: sqlite3.Row,
                                  as_of: date) -> list[str]:
        system = connection.execute(
            """SELECT s.system_id, s.system_name FROM contracts c JOIN systems s ON s.system_id = c.system_id
               WHERE c.contract_id = ?""",
            (agreement["contract_id"],),
        ).fetchone()
        blocked = connection.execute(
            """SELECT a.request_id, a.employee_id, a.approver_id, e.full_name, e.email
               FROM access_requests a JOIN employees e ON e.employee_id = a.employee_id
               WHERE a.system_id = ? AND a.status = 'blocked' ORDER BY a.request_id""",
            (system["system_id"],),
        ).fetchall()
        recipients = []
        for request in blocked:
            approver = _employee(connection, request["approver_id"]) if request["approver_id"] else None
            body = (
                f"Hi {request['full_name'].split()[0]},\n\n"
                f"NovaOps' {system['system_name']} seat capacity increased on {as_of.isoformat()}. "
                f"Your access request {request['request_id']} was blocked because all seats were in use.\n\n"
                "This message does not grant access. Your request is still open and now goes back to "
                f"{approver['full_name'] if approver else 'its approver'} for a normal review; you do not need "
                "to submit a new request."
            )
            if RenewalStore.enqueue(
                connection,
                message_key=f"capacity-notice:{run['run_id']}:{request['request_id']}",
                run_id=run["run_id"],
                channel="internal",
                recipient=request["email"],
                subject=f"{system['system_name']} capacity update for request {request['request_id']}",
                body=body,
                created_on=as_of.isoformat(),
            ):
                recipients.append(request["employee_id"])
        return recipients

    # == delivery ==========================================================================

    def dispatch(self) -> dict[str, int]:
        """Deliver pending messages once. A message is marked `uncertain` before the attempt,
        so a crash mid-send can never be reported as delivered nor silently re-sent."""
        counts = {"delivered": 0, "uncertain": 0, "retry_later": 0}
        for row in self.store.all("SELECT * FROM renewal_outbox WHERE delivery_status = 'pending' ORDER BY created_on, message_key"):
            with self.store.transaction() as connection:
                claimed = connection.execute(
                    """UPDATE renewal_outbox SET delivery_status = 'uncertain', attempts = attempts + 1,
                       last_error = 'in flight' WHERE message_key = ? AND delivery_status = 'pending'""",
                    (row["message_key"],),
                ).rowcount
            if not claimed:
                continue
            message = OutboundMessage(
                message_key=row["message_key"], channel=row["channel"], recipient=row["recipient"],
                subject=row["subject"], body=row["body"], headers=loads(row["headers_json"]),
            )
            adapter_send = self.provider.send_request if row["channel"] == "provider_email" else self.notifications.send
            with workflow_step(
                "renewal.deliver", as_type="tool",
                input={"message_key": message.message_key, "channel": message.channel, "recipient": message.recipient},
            ) as step:
                try:
                    adapter_send(message)
                    status, error = "delivered", None
                except DeliveryUncertain as exc:
                    status, error = "uncertain", str(exc)
                except DeliveryFailed as exc:
                    status, error = "pending", str(exc)
                step.update(output={"delivery_status": status, "error": error})
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE renewal_outbox SET delivery_status = ?, last_error = ?, delivered_at = ? WHERE message_key = ?",
                    (status, error, utc_now() if status == "delivered" else None, row["message_key"]),
                )
            counts["delivered" if status == "delivered" else "uncertain" if status == "uncertain" else "retry_later"] += 1
        return counts

    # == review ============================================================================

    def status(self) -> dict[str, Any]:
        latest = self.store.one("SELECT MAX(as_of) AS as_of FROM renewal_cases")
        cases = self.store.all(
            "SELECT contract_id, urgent, owner, reason, next_action, deadline, recommendation_json, facts_json "
            "FROM renewal_cases WHERE as_of = ? ORDER BY urgent DESC, deadline",
            (latest["as_of"] if latest else "",),
        )
        runs = []
        for run in self.store.all("SELECT * FROM renewal_runs ORDER BY run_id"):
            run_id = run["run_id"]
            runs.append(
                {
                    "run_id": run_id,
                    "contract_id": run["contract_id"],
                    "stage": run["stage"],
                    "status_reason": run["status_reason"],
                    "next_owner": run["next_owner"],
                    "recommendation": loads(run["recommendation_json"]),
                    "requested_scope": loads(run["requested_scope_json"]),
                    "approved_scope": loads(run["approved_scope_json"]),
                    "required_clearances": loads(run["required_clearances_json"]),
                    "decisions": self.store.all(
                        "SELECT event_id, kind, actor_employee_id, decision, approval_id, accepted, note, decided_at "
                        "FROM renewal_decisions WHERE run_id = ? ORDER BY decided_at", (run_id,)),
                    "supplier_replies": self.store.all(
                        "SELECT message_id, fixture_id, received_on, verdict, reasons_json FROM renewal_provider_replies "
                        "WHERE run_id = ? ORDER BY received_on, message_id", (run_id,)),
                    "agreement": self.store.one("SELECT * FROM renewal_agreements WHERE run_id = ?", (run_id,)),
                    "terms_in_effect": self.store.one(
                        "SELECT subscription_id, seat_limit, active_seats, annual_cost, renewal_date "
                        "FROM software_subscriptions WHERE subscription_id = ?", (run["subscription_id"],)),
                    "communications": self.store.all(
                        "SELECT message_key, channel, recipient, subject, delivery_status, created_on "
                        "FROM renewal_outbox WHERE run_id = ? ORDER BY created_on, message_key", (run_id,)),
                    "transitions": self.store.all(
                        "SELECT business_date, from_stage, to_stage, cause FROM renewal_run_transitions "
                        "WHERE run_id = ? ORDER BY id", (run_id,)),
                }
            )
        return {
            "as_of": latest["as_of"] if latest else None,
            "cases": [
                {
                    "contract_id": case["contract_id"],
                    "vendor": loads(case["facts_json"])["vendor"],
                    "urgent": bool(case["urgent"]),
                    "deadline": case["deadline"],
                    "owner": case["owner"],
                    "reason": case["reason"],
                    "next_action": case["next_action"],
                    "recommendation": loads(case["recommendation_json"]),
                }
                for case in cases
            ],
            "runs": runs,
            "uncertain_deliveries": self.store.all(
                "SELECT message_key, recipient, last_error FROM renewal_outbox WHERE delivery_status = 'uncertain'"
            ),
        }


# == helpers ===============================================================================


def _vendor_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _employee(connection: sqlite3.Connection, employee_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT employee_id, full_name, email, department_id, role FROM employees WHERE employee_id = ?",
        (employee_id,),
    ).fetchone()
    if row is None:
        return {"employee_id": employee_id, "full_name": employee_id, "email": f"{employee_id}@unknown", "department_id": None}
    return dict(row)


def _bounded_recommendation(case: Mapping[str, Any], proposal: Mapping[str, Any] | None, source: str) -> dict[str, Any]:
    allowed = case["allowed_actions"]
    proposed = (proposal or {}).get("action")
    guardrail = None
    if proposal is None:
        proposal = RulesRecommender().recommend([dict(case)])[case["contract_id"]]
        guardrail = "recommender returned nothing for this case; rule-based fallback"
        proposed = None
    action = proposed if proposed in allowed else allowed[0]
    if proposed is not None and proposed not in allowed:
        guardrail = f"model proposed '{proposed}', outside facts-allowed {allowed}; replaced with '{action}'"
    labels = {
        "expand": "expansion review",
        "renew_as_is": "renew as-is",
        "reduce": "reduction review",
        "cancel": "cancellation review",
        "escalate": "escalate",
    }
    return {
        "action": action,
        "label": f"{labels[action]} — recommendation, not authorization",
        "rationale": str(proposal.get("rationale", "")),
        "expected_impact": str(proposal.get("expected_impact", "")),
        "uncertainty": str(proposal.get("uncertainty", "")),
        "model_action": proposed,
        "allowed_actions": list(allowed),
        "guardrail": guardrail,
        "source": source,
        "is_authorization": False,
    }


def _case_next_action(case: Mapping[str, Any], recommendation: Mapping[str, Any]) -> tuple[str, str]:
    approver = case["owner"]["approver_id"]
    action = recommendation["action"]
    if action == "escalate":
        if case["owner"]["conflict"]:
            return ("unresolved (DB vs contract document)",
                    "escalate: owning departments confirm the accountable owner before any renewal decision")
        if any("Finance must rule" in issue for issue in case["open_issues"]):
            return ("Finance", "escalate: Finance rules on the ambiguous renewal terms before the renewal proceeds")
        return (approver or "unassigned", "escalate: human review of open issues")
    if action == "expand":
        if not case["reference_quote"]:
            return (approver, "obtain a supplier quote, then request internal approval")
        return (approver, f"internal approval requested from {approver} for the scoped expansion")
    if case["timing"]["notice_overdue"]:
        return (approver, f"notice deadline passed; owner confirms '{action}' before the term ends {case['current_terms']['end_date']}")
    return (approver, f"owner confirms '{action}' before notice deadline {case['timing']['notice_deadline']}")


def _requested_scope(case: Mapping[str, Any]) -> dict[str, Any]:
    quote = case["reference_quote"]
    start, end = rules.next_term(date.fromisoformat(case["current_terms"]["end_date"]))
    demand = (case["capacity"]["active_seats"] or 0) + len(case["capacity"]["blocked_requests"])
    return {
        "action": "expand",
        "seat_limit": int(quote["seat_limit"]),
        "max_annual_cost_usd": int(quote["annual_cost_usd"]),
        "term_start_date": start.isoformat(),
        "term_end_date": end.isoformat(),
        "effective_date": start.isoformat(),
        "current_annual_cost_usd": case["current_terms"]["annual_cost_usd"],
        "annual_increment_usd": int(quote["annual_cost_usd"]) - int(case["current_terms"]["annual_cost_usd"]),
        "seat_demand": demand,
        "quote_source": quote["source"],
    }


def _required_clearances(case: Mapping[str, Any], scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    clearances = []
    finance_approvals = [a["approval_id"] for a in case["open_approvals"] if a["request_type"] == "subscription_expansion"]
    over_threshold = rules.finance_required_by_threshold(scope["current_annual_cost_usd"], scope["max_annual_cost_usd"])
    if finance_approvals or over_threshold:
        clearances.append(
            {
                "kind": "finance_clearance",
                "approval_ids": finance_approvals,
                "owner": "Finance",
                "why": (
                    f"open Finance approval {', '.join(finance_approvals)} must be resolved "
                    f"(increment USD {scope['annual_increment_usd']} vs Q3 threshold USD "
                    f"{rules.FINANCE_INCREMENT_THRESHOLD_USD}; the threshold is not an exemption)"
                    if finance_approvals
                    else f"increment USD {scope['annual_increment_usd']} exceeds Q3 threshold"
                ),
            }
        )
    if case["security_review_required"]:
        clearances.append({"kind": "security_review", "approval_ids": [], "owner": "IT Security",
                           "why": "contract requires a security review before renewal"})
    return clearances


def _authorize(kind: str, event: Mapping[str, Any], run: sqlite3.Row, actor: Mapping[str, Any]) -> tuple[bool, str | None]:
    if kind == "internal_approval":
        if actor["employee_id"] != run["approver_id"]:
            return False, f"{actor['employee_id']} is not the run's approver {run['approver_id']}"
        if event.get("decision") not in ("approved", "rejected"):
            return False, f"unknown decision {event.get('decision')!r}"
    elif kind == "finance_clearance":
        if actor.get("department_id") != "D_FINANCE" or event.get("decision") != "cleared":
            return False, "finance clearance must come from Finance with decision 'cleared'"
        required = next((c for c in loads(run["required_clearances_json"]) if c["kind"] == kind), None)
        if required and required["approval_ids"] and event.get("approval_id") not in required["approval_ids"]:
            return False, f"clears {event.get('approval_id')}, run needs {required['approval_ids']}"
    elif kind == "security_review":
        if actor.get("department_id") != "D_IT" or event.get("decision") != "complete":
            return False, "security review must come from IT with decision 'complete'"
    return True, None


def _missing_clearances(connection: sqlite3.Connection, run: sqlite3.Row) -> list[dict[str, Any]]:
    done = {
        row["kind"]
        for row in connection.execute(
            "SELECT kind FROM renewal_decisions WHERE run_id = ? AND accepted = 1", (run["run_id"],)
        )
    }
    return [c for c in loads(run["required_clearances_json"]) if c["kind"] not in done]


def _waiting_reason(missing: list[dict[str, Any]]) -> str:
    return "approved; waiting for: " + ", ".join(
        f"{c['kind']}{' ' + '/'.join(c['approval_ids']) if c['approval_ids'] else ''}" for c in missing
    )


def _scope_problems(scope: Mapping[str, Any], requested: Mapping[str, Any]) -> list[str]:
    problems = []
    for key in ("action", "seat_limit", "max_annual_cost_usd", "term_start_date", "term_end_date"):
        if scope.get(key) in (None, ""):
            problems.append(f"approved scope lacks {key}")
    if scope.get("action") and scope.get("action") != requested.get("action"):
        problems.append(f"approved action {scope.get('action')} != requested {requested.get('action')}")
    return problems


def _approval_request_body(case, recommendation, scope, clearances, approver) -> str:
    return (
        f"Hi {approver['full_name'].split()[0]},\n\n"
        f"Renewal review for {case['vendor']} ({case['contract_id']}), as of {case['as_of']}:\n"
        f"- {'; '.join(case['signals'])}\n"
        f"- Recommendation: {recommendation['label']}. {recommendation['rationale']}\n"
        f"- Requested scope: {scope['action']} to {scope['seat_limit']} seats, up to USD {scope['max_annual_cost_usd']:,}/yr "
        f"(currently USD {scope['current_annual_cost_usd']:,}), term {scope['term_start_date']} to {scope['term_end_date']}.\n"
        f"- Clearances required before any supplier request: "
        f"{', '.join(c['kind'] for c in clearances) or 'none'}.\n\n"
        "Nothing has been sent to the supplier. Reply with an approve or reject decision."
    )


def _supplier_request_body(contract: sqlite3.Row, subscription: sqlite3.Row, scope: Mapping[str, Any]) -> str:
    return (
        f"Hello {contract['vendor_name']} team,\n\n"
        f"NovaOps requests the renewal of contract {contract['contract_id']} ({contract['contract_name']}). "
        f"The current term runs {contract['start_date']} to {contract['end_date']} with "
        f"{subscription['seat_limit']} contracted seats.\n\n"
        f"Requested: renew and {scope['action']} to {scope['seat_limit']} seats for the term "
        f"{scope['term_start_date']} to {scope['term_end_date']}, at the quoted annual price of "
        f"USD {scope['max_annual_cost_usd']:,}.\n\n"
        "Please reply to this email with a final confirmation number, or with any conditions.\n\n"
        "NovaOps Procurement"
    )
