# Workflow 4 — event-driven contract renewal

Nobody chats with this one. A schedule starts it, it stops to wait for a human, inbound
events restart it, the schedule wakes it again on the effective date, and it must survive
the process dying in between. Standalone: it shares `documents/` and `database/` with the
agent and nothing else.

## What is here

These are **mocks behind two replaceable ports**, not business data. The business facts live
in `database/` (contract `C001`, subscription `SUB001`, the blocked access request) and in
`documents/contracts/webex_vendor_agreement.md`.

- `internal_approval_events.jsonl` — four internal events, each with an `event_id` that is
  its idempotency key and an `event_type`. Consumed through a `NotificationAdapter` port.
  - `internal_approval`: an approve and a reject from the IT Manager. The approval carries
    `approved_scope` (action, seats, price ceiling, term), which is what a supplier reply
    must match before anything is recorded.
  - `finance_clearance`: Finance resolves the open approval `AP002` in the database.
  - `security_review`: the security review that `C001` requires is complete.

  The happy path needs all three kinds. With the approval but a clearance missing, the run
  stays pending for a human, and nothing goes to the supplier.
- `provider_replies/*.eml` — five vendor replies: **approved**, **conditional**, **rejected**,
  **ambiguous**, and **over ceiling**: a final confirmation at USD 24,000, above the
  approved USD 22,000. Consumed through a `ProviderEmailAdapter` port.
- `provider_reply_manifest.json` — stable fixture ids and demo selection weights. Selection
  may be random for a demo, but must accept an explicit id or seed so tests are deterministic.
- `provider_renewal_decision.schema.json` — the extraction contract for a reply.

## The one that catches people

`webex_renewal_conditional.eml` carries a price, a seat count and dates — everything an eager
extractor wants — and a sentence saying it is **not final approval**. Applying it is the
failure this workflow is built to expose.

`webex_renewal_over_ceiling.eml` fails the other way: it **is** final and complete, and the
extraction is correctly `approved`. It still must not be applied, because its price is above
what the internal approval authorized. The supplier's word decides whether a reply is final;
the approved scope decides whether it can be recorded.

The provider email is untrusted external text whose extracted contents gate a database write.
Extract it as a *proposal*, then let deterministic code decide: only a complete, approved
reply that matches the pending run may mutate anything. Conditional, rejected, ambiguous and
mismatched replies all return to a human with no write.

## A confirmation is not an activation

`webex_renewal_approved.eml` arrives on 2026-07-03 and confirms a term that **starts on
2026-07-21**. Record it as a future agreement ("confirmed, awaiting effective date") and
change nothing else: the seat limit stays 40 and nobody is told capacity is available. The
change is applied once, by the scheduled run on or after 2026-07-21, and the blocked
requesters are notified then.
