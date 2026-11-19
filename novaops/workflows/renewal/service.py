"""Wiring: build the renewal workflow from settings, and the traced evaluation sequence."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from novaops.core.config import Settings
from novaops.core.tracing import trace_workflow_run, workflow_step
from novaops.workflows.renewal.model import (
    BedrockRecommender,
    BedrockReplyExtractor,
    Recommender,
    ReplyExtractor,
    RulesRecommender,
    load_decision_schema,
)
from novaops.workflows.renewal.ports import (
    MockNotificationAdapter,
    MockProviderEmailAdapter,
    NotificationAdapter,
    ProviderEmailAdapter,
)
from novaops.workflows.renewal.store import RenewalStore
from novaops.workflows.renewal.workflow import RenewalWorkflow

EVENTS_FILE = Path("workflows/renewal/internal_approval_events.jsonl")
SCHEDULE_DATE = date(2026, 7, 1)
REPLY_DATE = date(2026, 7, 3)
EFFECTIVE_DATE = date(2026, 7, 21)


def build_workflow(
    settings: Settings,
    *,
    database_path: Path | None = None,
    notifications: NotificationAdapter | None = None,
    provider: ProviderEmailAdapter | None = None,
    recommender: Recommender | None = None,
    extractor: ReplyExtractor | None = None,
    client: Any | None = None,
    offline: bool = False,
) -> RenewalWorkflow:
    schema = load_decision_schema(settings.data_dir)
    if not offline and (recommender is None or extractor is None) and client is None:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=settings.aws_region)
    if recommender is None:
        recommender = RulesRecommender() if offline else BedrockRecommender(client, settings.bedrock_model_id)
    if extractor is None and not offline:
        extractor = BedrockReplyExtractor(client, settings.bedrock_model_id, schema)
    return RenewalWorkflow(
        RenewalStore(database_path or settings.database_path, settings.data_dir),
        notifications=notifications or MockNotificationAdapter(),
        provider=provider or MockProviderEmailAdapter(settings.data_dir),
        recommender=recommender,
        extractor=extractor,
        decision_schema=schema,
    )


def run_renewal_evaluation(
    settings: Settings,
    *,
    item_id: str,
    provider_reply: str,
    internal_events: Sequence[Mapping[str, Any]],
    database_path: Path,
    client: Any | None = None,
    recommender: Recommender | None = None,
    extractor: ReplyExtractor | None = None,
) -> dict[str, Any]:
    """One evaluation input = one trace: schedule, restart, events, reply, replays, 07-03, 07-21."""
    if database_path.exists():
        database_path.unlink()
    notifications = MockNotificationAdapter()
    provider = MockProviderEmailAdapter(settings.data_dir)

    def fresh_process() -> RenewalWorkflow:
        # A new workflow object over the same database file: nothing carried in memory.
        return build_workflow(
            settings, database_path=database_path, notifications=notifications, provider=provider,
            recommender=recommender, extractor=extractor, client=client,
        )

    with trace_workflow_run(
        settings,
        name="novaops.renewal",
        session_id=f"renewal-{item_id}",
        request_id=item_id,
        tags=["renewal", "workflow-4"],
        input={"item_id": item_id, "events": [event["event_id"] for event in internal_events]},
    ) as root:
        timeline: list[dict[str, Any]] = []
        schedule_1 = fresh_process().run_schedule(SCHEDULE_DATE)
        timeline.append({"step": "schedule 2026-07-01", "result": schedule_1})

        with workflow_step("renewal.process_restart", input={"database": database_path.name}):
            workflow = fresh_process()
        for event in internal_events:
            timeline.append({"step": f"event {event['event_id']}", "result": workflow.handle_event(event)})
        timeline.append({"step": "provider reply", "result": workflow.handle_reply(provider_reply)})

        with workflow_step("renewal.replay_all", input={"note": "every event and the reply delivered a second time"}) as replay:
            replays = [workflow.handle_event(event)["outcome"] for event in internal_events]
            replays.append(workflow.handle_reply(provider_reply)["outcome"])
            replay.update(output=replays)
        timeline.append({"step": "replay", "result": replays})

        timeline.append({"step": "schedule 2026-07-03", "result": workflow.run_schedule(REPLY_DATE)})
        seats_jul_03 = _terms(workflow)
        timeline.append({"step": "schedule 2026-07-21", "result": workflow.run_schedule(EFFECTIVE_DATE)})
        seats_jul_21 = _terms(workflow)
        timeline.append({"step": "schedule 2026-07-21 (replayed)", "result": fresh_process().run_schedule(EFFECTIVE_DATE)})

        status = workflow.status()
        run = next((r for r in status["runs"] if r["contract_id"] == "C001"), None)
        summary = {
            "item_id": item_id,
            "c001_on_2026_07_01": next(
                (case for case in schedule_1["cases"] if case["contract_id"] == "C001"), None
            ),
            "run_stage": run["stage"] if run else None,
            "status_reason": run["status_reason"] if run else None,
            "next_owner": run["next_owner"] if run else None,
            "stage_history": [f"{t['business_date']} {t['to_stage']} ({t['cause']})" for t in run["transitions"]] if run else [],
            "agreement": run["agreement"] if run else None,
            "terms_in_effect_2026_07_03": seats_jul_03,
            "terms_in_effect_2026_07_21": seats_jul_21,
            "communications": run["communications"] if run else [],
            "employee_notifications": [
                {"recipient": m.recipient, "subject": m.subject, "body": m.body}
                for m in notifications.sent
                if m.message_key.startswith("capacity-notice:")
            ],
            "supplier_requests_sent": len([m for m in provider.sent if m.message_key.startswith("supplier-request:")]),
            "replay_outcomes": replays,
            "audit_rows": workflow.store.all(
                "SELECT event_id, event_type, notes FROM audit_log WHERE event_id LIKE 'AUD-%' ORDER BY event_id"
            ),
            "AP002": workflow.store.one("SELECT approval_id, status FROM approvals WHERE approval_id = 'AP002'"),
            "AR001": workflow.store.one("SELECT request_id, status FROM access_requests WHERE request_id = 'AR001'"),
        }
        root.update(
            output=summary,
            metadata={"workflow": "renewal", "run_stage": summary["run_stage"], "status": "completed"},
        )
    return {"summary": summary, "timeline": timeline}


def _terms(workflow: RenewalWorkflow) -> dict[str, Any] | None:
    return workflow.store.one(
        """SELECT s.seat_limit, s.active_seats, s.annual_cost, s.renewal_date, c.start_date, c.end_date
           FROM software_subscriptions s JOIN contracts c ON c.contract_id = s.contract_id
           WHERE s.subscription_id = 'SUB001'"""
    )
