# M5 - Webex diagnosis and approval

## Outcome

Webex access questions now follow a deterministic, evidence-backed diagnosis before any
write is considered. The shared Maya/Webex graph reads the caller's employee record,
subscription, access requests, tickets, applicable policy, and permitted knowledge through
the MCP boundary. The response distinguishes blocked, already-pending, and approval-needed
cases; it does not claim that a request or an approval has granted system access.

The supplied dataset's Webex subscription is at 42 active seats for 40 seats. Its seeded
blocked request is returned as blocked, with no approval pause and no write. Existing
requests are reused rather than duplicated. A separate test fixture reduces the seat count
to exercise the valid approval path.

## Persisted approval and restart

When diagnosis permits a new request, LangGraph interrupts and persists its checkpoint.
The local CLI records a decision only after validating the claimed approver against the
employee-manager relationship or the IT Manager role records:

```powershell
.\.venv\Scripts\python -m novaops.cli chat --as E001 --thread-id webex-1 "I need standard Webex access for my role."
.\.venv\Scripts\python -m novaops.cli approve --thread-id webex-1 --as E018 --decision approved --reason "Business need confirmed."
```

The approval record is exact to the thread, workflow scope, employee, system, access level,
requested tool, and justification. The approval command resumes the persisted thread; the
write boundary independently checks that record before sending the request through MCP.
An approved request is recorded as `pending_approval`, which means it has been submitted for
operational approval—not that Webex access has been provisioned.

The CLI's `--as` argument is a claimed actor identity for the local demo. Dataset-based
authorization prevents unauthorized employee IDs from releasing writes, but it does not
authenticate the person at the keyboard. A deployed approval interface must bind approvals
to an authenticated identity.

## Idempotency and recovery

Access-request and ticket writes use stable idempotency keys and an atomic database
transaction. Replaying the same operation returns the existing record. Reusing a key with
a different payload is rejected. This covers retries after a process interruption without
creating duplicate operational records.

The original lesson's Webex session transcript is not present in this workspace, so the
approval/restart scenario is covered with focused tests rather than represented as a replay
of that transcript. Tests use a fake model and do not require live Bedrock credentials.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_webex.py -k idempotent
.\.venv\Scripts\python.exe -m pytest tests\test_webex.py -k restart
.\.venv\Scripts\python.exe -m pytest tests\test_database.py tests\test_mcp_tools.py tests\test_agent_graph.py
.\.venv\Scripts\python.exe -m novaops.cli approve --help
.\.venv\Scripts\python.exe -m pytest
```
