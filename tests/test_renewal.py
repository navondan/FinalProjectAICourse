"""M8 renewal workflow: durable state, gates, idempotency, effective date. No network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from novaops.core.config import PROJECT_ROOT, Settings
from novaops.workflows.renewal.matching import evaluate_reply
from novaops.workflows.renewal.model import ModelOutputError, RulesRecommender
from novaops.workflows.renewal.ports import MockNotificationAdapter, MockProviderEmailAdapter, parse_reply
from novaops.workflows.renewal.service import EVENTS_FILE, build_workflow, run_renewal_evaluation

SCOPE = {
    "action": "expand",
    "seat_limit": 50,
    "max_annual_cost_usd": 22000,
    "term_start_date": "2026-07-21",
    "term_end_date": "2027-07-20",
}
BASE = {
    "contract_id": "C001",
    "confirmation_id": None,
    "new_seat_limit": 50,
    "annual_cost_usd": 22000,
    "term_start_date": "2026-07-21",
    "term_end_date": "2027-07-20",
    "conditions": [],
    "missing_fields": [],
}
EXTRACTIONS = {
    "webex_renewal_approved": {**BASE, "decision": "approved", "confirmation_id": "RX-WEBEX-2026-8841"},
    "webex_renewal_conditional": {
        **BASE,
        "decision": "conditional",
        "conditions": ["signed order form", "written Finance acceptance by 2026-07-10"],
        "missing_fields": ["confirmation_id"],
    },
    "webex_renewal_ambiguous": {
        **BASE, "decision": "ambiguous", "contract_id": None, "annual_cost_usd": None,
        "term_start_date": None, "term_end_date": None,
        "missing_fields": ["contract_id", "confirmation_id", "annual_cost_usd", "term_start_date", "term_end_date"],
    },
    "webex_renewal_rejected": {**BASE, "decision": "rejected", "new_seat_limit": None, "annual_cost_usd": None,
                               "term_start_date": None, "term_end_date": None},
    "webex_renewal_over_ceiling": {**BASE, "decision": "approved", "confirmation_id": "RX-WEBEX-2026-9902",
                                   "annual_cost_usd": 24000},
}


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, reply):
        self.calls += 1
        return dict(EXTRACTIONS[reply.fixture_id])


class CountingRecommender(RulesRecommender):
    def __init__(self) -> None:
        self.calls = 0

    def recommend(self, cases):
        self.calls += 1
        return super().recommend(cases)


@pytest.fixture()
def settings() -> Settings:
    os.environ["LANGFUSE_PUBLIC_KEY"] = ""
    os.environ["LANGFUSE_SECRET_KEY"] = ""
    return Settings.from_env()


def all_events(settings: Settings) -> dict[str, dict]:
    lines = (settings.data_dir / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    return {event["event_id"]: event for event in map(json.loads, filter(str.strip, lines))}


def reply_text(settings: Settings, fixture_id: str) -> str:
    return MockProviderEmailAdapter(settings.data_dir, fixture_id=fixture_id).receive_reply()


def make(settings: Settings, db: Path, **kwargs):
    kwargs.setdefault("recommender", RulesRecommender())
    kwargs.setdefault("extractor", FakeExtractor())
    kwargs.setdefault("notifications", MockNotificationAdapter())
    kwargs.setdefault("provider", MockProviderEmailAdapter(settings.data_dir))
    return build_workflow(settings, database_path=db, **kwargs)


def happy_events(settings: Settings) -> list[dict]:
    events = all_events(settings)
    return [events[key] for key in ("IA-WEBEX-APPROVE-001", "FC-WEBEX-CLEAR-001", "SR-WEBEX-REVIEW-001")]


def seats(workflow) -> dict:
    return workflow.store.one(
        "SELECT seat_limit, active_seats, annual_cost, renewal_date FROM software_subscriptions WHERE subscription_id = 'SUB001'"
    )


def run_of(workflow) -> dict:
    return next(run for run in workflow.status()["runs"] if run["run_id"] == "RR-WEBEX-2026")


# -- scan ------------------------------------------------------------------------------


def test_scan_marks_webex_urgent_and_notice_overdue_and_opens_one_run(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    result = workflow.run_schedule(date(2026, 7, 1))
    by_id = {case["contract_id"]: case for case in result["cases"]}
    assert {"C001", "C002", "C003", "C007", "C017", "C019", "C020"} <= set(by_id)
    assert by_id["C001"] | {} == {**by_id["C001"], "urgent": True, "notice_overdue": True, "expired": False}
    assert by_id["C001"]["recommendation"] == "expand"
    assert by_id["C017"]["recommendation"] == "escalate"
    assert by_id["C020"]["recommendation"] == "escalate"
    assert result["runs_opened"] == ["RR-WEBEX-2026"]
    assert run_of(workflow)["stage"] == "awaiting_internal_decision"
    # Nothing goes to the supplier before an internal decision.
    assert not workflow.provider.sent


def test_recommendations_are_cached_per_term(settings, tmp_path):
    recommender = CountingRecommender()
    workflow = make(settings, tmp_path / "r.sqlite3", recommender=recommender)
    workflow.run_schedule(date(2026, 7, 1))
    workflow.run_schedule(date(2026, 7, 1))
    assert recommender.calls == 1


def test_model_failure_falls_back_to_rules(settings, tmp_path):
    class Broken:
        def recommend(self, cases):
            raise ModelOutputError("bad tool output")

    workflow = make(settings, tmp_path / "r.sqlite3", recommender=Broken())
    result = workflow.run_schedule(date(2026, 7, 1))
    assert result["runs_opened"] == ["RR-WEBEX-2026"]


# -- restart / durability ------------------------------------------------------------


def test_restart_between_processes_resumes_from_sqlite(settings, tmp_path):
    db = tmp_path / "restart.sqlite3"
    env = {**os.environ, "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}
    completed = subprocess.run(
        [sys.executable, "-m", "novaops.workflows.renewal", "--db", str(db), "--offline", "schedule", "--as-of", "2026-07-01"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["runs_opened"] == ["RR-WEBEX-2026"]

    # A brand-new process: nothing in memory, the run is found in SQLite.
    workflow = make(settings, db)
    for event in happy_events(settings):
        workflow.handle_event(event)
    assert run_of(workflow)["stage"] == "awaiting_supplier"


# -- internal events -----------------------------------------------------------------


def test_missing_clearance_holds_supplier_request(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    workflow.run_schedule(date(2026, 7, 1))
    approve, finance, _security = happy_events(settings)
    workflow.handle_event(approve)
    workflow.handle_event(finance)
    run = run_of(workflow)
    assert run["stage"] == "awaiting_clearances"
    assert "security" in run["status_reason"].lower()
    assert not workflow.provider.sent


def test_finance_clearance_closes_ap002_with_audit(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    workflow.run_schedule(date(2026, 7, 1))
    for event in happy_events(settings):
        workflow.handle_event(event)
    assert workflow.store.one("SELECT status FROM approvals WHERE approval_id = 'AP002'")["status"] == "approved"
    assert workflow.store.one("SELECT 1 FROM audit_log WHERE event_id = 'AUD-FC-WEBEX-CLEAR-001'")


def test_unauthorized_actor_is_recorded_but_has_no_effect(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    workflow.run_schedule(date(2026, 7, 1))
    forged = {**happy_events(settings)[0], "event_id": "IA-FORGED-1", "actor_employee_id": "E010"}
    assert workflow.handle_event(forged)["outcome"] == "rejected_unauthorized"
    assert run_of(workflow)["stage"] == "awaiting_internal_decision"


def test_internal_rejection_stops_the_run(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    workflow.run_schedule(date(2026, 7, 1))
    reject = all_events(settings)["IA-WEBEX-REJECT-001"]
    assert workflow.handle_event(reject)["outcome"] == "run_rejected_internally"
    assert run_of(workflow)["stage"] == "rejected_internally"
    assert not workflow.provider.sent
    assert workflow.store.one("SELECT status FROM contracts WHERE contract_id = 'C001'")["status"] != "cancelled"


def test_supplier_request_carries_message_id_for_reply_matching(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    workflow.run_schedule(date(2026, 7, 1))
    for event in happy_events(settings):
        workflow.handle_event(event)
    [request] = workflow.provider.sent
    assert request.headers["Message-ID"] == "<RR-WEBEX-2026@novaops.example>"
    assert "50" in request.body and "22,000" in request.body.replace("22000", "22,000")


# -- supplier replies ----------------------------------------------------------------


def _to_supplier(settings, db):
    workflow = make(settings, db)
    workflow.run_schedule(date(2026, 7, 1))
    for event in happy_events(settings):
        workflow.handle_event(event)
    return workflow


@pytest.mark.parametrize(
    ("fixture_id", "category"),
    [
        ("webex_renewal_conditional", "conditional"),
        ("webex_renewal_ambiguous", "ambiguous"),
        ("webex_renewal_rejected", "rejected"),
        ("webex_renewal_over_ceiling", "mismatch"),
    ],
)
def test_non_final_replies_escalate_without_any_write(settings, tmp_path, fixture_id, category):
    workflow = _to_supplier(settings, tmp_path / "r.sqlite3")
    result = workflow.handle_reply(reply_text(settings, fixture_id))
    assert result["verdict"] == "escalate" and result["category"] == category
    run = run_of(workflow)
    assert run["stage"] == "needs_human" and run["agreement"] is None
    workflow.run_schedule(date(2026, 7, 21))
    assert seats(workflow)["seat_limit"] == 40
    assert not [m for m in workflow.notifications.sent if m.message_key.startswith("capacity-notice:")]
    assert workflow.store.one("SELECT status FROM contracts WHERE contract_id = 'C001'")["status"] != "cancelled"


def test_reply_for_unknown_run_has_no_effect(settings, tmp_path):
    workflow = make(settings, tmp_path / "r.sqlite3")
    workflow.run_schedule(date(2026, 7, 1))
    result = workflow.handle_reply(reply_text(settings, "webex_renewal_approved"))
    assert result["outcome"] == "unexpected_no_effect"
    assert workflow.extractor.calls == 0


def test_matching_requires_grounded_values():
    text = "Final confirmation RX-1 for C001: 50 seats, USD 22,000, 2026-07-21 to 2027-07-20."
    good = {**BASE, "decision": "approved", "confirmation_id": "RX-1"}
    assert evaluate_reply(good, reply_text=text, contract_id="C001", approved_scope=SCOPE)["verdict"] == "record"
    invented = {**good, "confirmation_id": "RX-INVENTED"}
    assert evaluate_reply(invented, reply_text=text, contract_id="C001", approved_scope=SCOPE)["category"] == "mismatch"


# -- effective date, replay, notices -------------------------------------------------


def test_effective_date_and_replay(settings, tmp_path):
    db = tmp_path / "r.sqlite3"
    workflow = _to_supplier(settings, db)
    raw = reply_text(settings, "webex_renewal_approved")
    assert workflow.handle_reply(raw)["outcome"] == "recorded"
    assert workflow.handle_reply(raw)["outcome"] == "duplicate_ignored"
    assert workflow.extractor.calls == 1
    for event in happy_events(settings):
        assert workflow.handle_event(event)["outcome"] == "duplicate_ignored"

    workflow.run_schedule(date(2026, 7, 3))
    assert seats(workflow)["seat_limit"] == 40
    assert run_of(workflow)["stage"] == "confirmed_awaiting_effective_date"

    workflow.run_schedule(date(2026, 7, 21))
    workflow.run_schedule(date(2026, 7, 21))
    make(settings, db, notifications=workflow.notifications).run_schedule(date(2026, 7, 21))
    assert seats(workflow) == {"seat_limit": 50, "active_seats": 42, "annual_cost": 22000, "renewal_date": "2027-07-20"}
    assert run_of(workflow)["stage"] == "applied"
    applies = workflow.store.all("SELECT 1 FROM audit_log WHERE event_id = 'AUD-RR-WEBEX-2026-APPLY'")
    assert len(applies) == 1
    notices = [m for m in workflow.notifications.sent if m.message_key.startswith("capacity-notice:")]
    assert [m.recipient for m in notices] == ["rachel.stein@novaops.example"]
    body = notices[0].body
    assert "22,000" not in body and "22000" not in body and "USD" not in body
    assert "does not grant access" in body
    assert workflow.store.one("SELECT status FROM access_requests WHERE request_id = 'AR001'")["status"] == "blocked"


def test_uncertain_delivery_is_not_reported_as_sent_and_retries_once(settings, tmp_path):
    provider = MockProviderEmailAdapter(settings.data_dir, uncertain_keys={"supplier-request:RR-WEBEX-2026"})
    workflow = make(settings, tmp_path / "r.sqlite3", provider=provider)
    workflow.run_schedule(date(2026, 7, 1))
    for event in happy_events(settings):
        workflow.handle_event(event)
    assert not provider.sent
    status = workflow.status()
    assert "supplier-request:RR-WEBEX-2026" in [d["message_key"] for d in status["uncertain_deliveries"]]
    # An uncertain send is never re-sent automatically: a duplicate supplier request is worse.
    provider.uncertain_keys.clear()
    workflow.dispatch()
    assert not provider.sent


def test_failed_delivery_stays_pending_and_is_retried(settings, tmp_path):
    notifications = MockNotificationAdapter(failing_keys={"approval-request:RR-WEBEX-2026"})
    workflow = make(settings, tmp_path / "r.sqlite3", notifications=notifications)
    workflow.run_schedule(date(2026, 7, 1))
    assert not notifications.sent
    notifications.failing_keys.clear()
    workflow.dispatch()
    workflow.dispatch()
    assert [m.message_key for m in notifications.sent] == ["approval-request:RR-WEBEX-2026"]


def test_parse_reply_reads_headers(settings):
    reply = parse_reply(reply_text(settings, "webex_renewal_approved"))
    assert reply.in_reply_to == "<RR-WEBEX-2026@novaops.example>"
    assert reply.received_on == "2026-07-03"
    assert reply.fixture_id == "webex_renewal_approved"


def test_seeded_reply_choice_is_deterministic(settings):
    first = MockProviderEmailAdapter(settings.data_dir, seed=7).receive_reply()
    assert first == MockProviderEmailAdapter(settings.data_dir, seed=7).receive_reply()


def test_evaluation_sequence_end_to_end(settings, tmp_path):
    result = run_renewal_evaluation(
        settings,
        item_id="T",
        provider_reply=reply_text(settings, "webex_renewal_approved"),
        internal_events=happy_events(settings),
        database_path=tmp_path / "eval.sqlite3",
        recommender=RulesRecommender(),
        extractor=FakeExtractor(),
    )["summary"]
    assert result["run_stage"] == "applied"
    assert result["terms_in_effect_2026_07_03"]["seat_limit"] == 40
    assert result["terms_in_effect_2026_07_21"]["seat_limit"] == 50
    assert len(result["employee_notifications"]) == 1
    assert set(result["replay_outcomes"]) == {"duplicate_ignored"}
