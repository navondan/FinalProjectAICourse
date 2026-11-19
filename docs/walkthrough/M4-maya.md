# M4 · Maya: cited onboarding, no writes

## What changed

Maya's `onboarding_status` path now bypasses free-form tool selection after classification
and gathers the authorized employee record, onboarding rows, asset inventory, Webex seat
counts, and onboarding policy through MCP. The model returns an `OnboardingChecklist` JSON
object. The application validates its schema, requires every task to match the database
exactly, checks that every evidence quote is present in its cited source, preserves each
blocked task, and rejects an unsupported Webex explanation before rendering the answer.

The final checklist cites each database task row. The Webex item remains blocked when its
42 active seats exceed the 40-seat limit; the output cites both the onboarding row and the
subscription row. Maya's prompt cannot mark a task complete, create an access request, or
create a ticket. The Maya loadouts are read-only and the checklist path invokes only read
tools.

## Webex handoff

When a blocked Webex onboarding task is present, `workflows/maya/handoff.py` prepares a
checkpointed handoff payload bound to the caller's employee id and conversation thread. Its
SHA-256 idempotency key includes the task, reason, and evidence sources, so replaying the
same handoff is stable while a different thread produces a different key. This is diagnostic
state for the shared graph, not a submitted access request; no external write is performed.

## Conversation behavior

The final-answer prompt now includes persisted conversation history. Maya answers are
checked sentence by sentence: each sentence must cite an allowed retrieved document,
database tool result, or the specific caller-provided conversation turn. Unknown citation
sources and uncited factual sentences fail explicitly.

`tests/fixtures/maya_s9.json` adds the five-turn `S9-maya-own-account` scenario: two attempts
to access manager-only material, a forwarded three-person email whose answer is in the
message, an unmarked Q3 SaaS-policy pivot, and Maya's own onboarding checklist. The email
thread is explicitly synthetic test input; it is not treated as NovaOps operational data.

## Verify

```powershell
.\.venv\Scripts\python -m pytest tests/test_maya.py tests/test_agent_graph.py
.\.venv\Scripts\python -m pytest
```

The focused tests verify schema/evidence validation, 42-of-40 Webex blocking, stable
idempotency, persistent thread history, citation enforcement, and that no Maya loadout or
S9 turn exposes or executes a write tool.

The lesson-source S2 transcript and original Maya handoff implementation described in
`BUILD-PLAN.md` are not present in this workspace, so this milestone adapts the supplied
NovaOps dataset and adds S9 rather than claiming to have replayed those unavailable lesson
artifacts. The tests use deterministic models and MCP fakes; a live Bedrock chat was not run.
