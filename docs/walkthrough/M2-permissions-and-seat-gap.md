# M2 · Permissions and the Webex seat-assignment gap

## What changed

Document audience filtering treats an absent `audience` as unrestricted, allows `all` to
everyone, allows the singular value `manager` only to managers, and denies any other explicit
audience value. This filter runs before FAISS/BM25 results are returned, so unauthorized text
does not reach a caller or model.

Manager status comes from the employee table: a caller is a manager when another employee has
their id in `manager_id`. This deliberately does not rely on job title or user group; for
example, Yael Romano (`E018`) is a manager despite belonging to `UG_REGULAR`.

The database has Webex seat counts, role entitlements, blocked access/onboarding records, and
tickets, but no employee-to-seat assignment table. The new
`report_team_webex_seat_assignment_gap` tool requires a known manager and returns those source
records while marking every individual seat assignment as unrecorded. It never infers a seat
holder from role eligibility.

For Yael's direct reports in the supplied database, the report shows:

| Employee | Webex by role | Recorded Webex evidence | Seat assignment |
| --- | --- | --- | --- |
| Maya Cohen | Eligible | Onboarding task `OT005` is blocked | Not recorded |
| Noam Sharon | Not eligible | No Webex request/task is recorded | Not recorded |
| Rachel Stein | Eligible | Request `AR001` is blocked; ticket `T001` is open | Not recorded |

The subscription count is 42 active seats against a limit of 40. These counts do not identify
which employees have seats.

## Data flow

```text
trusted caller employee id
          |
          +--> SELECT employees WHERE manager_id = caller id
          |          |
          |          +--> role entitlement from systems_allowed_by_role
          |          +--> Webex access requests, onboarding tasks, tickets
          |
          +--> require caller has direct reports
          |
          +--> Webex seat counts
                     |
                     v
       report evidence + assignment gap
       (never infer seat holder)
```

Separately, retrieval applies `audience_is_allowed()` to document metadata before selecting
results:

```text
no audience / audience=all -> all callers
audience=manager            -> caller has a direct report
other explicit value        -> denied
```

## Files to know

| File | Why it matters |
| --- | --- |
| `novaops/retrieval/filters.py` | Fail-closed audience policy |
| `novaops/core/database.py` | `is_manager()` and the evidence-based seat-gap report |
| `novaops/mcp_server/tools/functions.py` | Caller-aware retrieval and tool contract |
| `novaops/mcp_server/server.py` | Publishes the gap report through MCP |
| `tests/test_database.py` | Asserts known fixture evidence and manager authorization |

## Verify

The focused checks use a synthetic embedder and make no AWS calls:

```powershell
.\.venv\Scripts\python -m pytest tests/test_database.py tests/test_retrieval.py tests/test_mcp_tools.py
```

With Bedrock credentials configured, also verify the full cited retrieval boundary:

```powershell
.\.venv\Scripts\python -m novaops.cli ask --as E001 "What does the manager's guide say about feedback?"
.\.venv\Scripts\python -m novaops.cli ask --as E006 "What does the manager's guide say about feedback?"
.\.venv\Scripts\python -m novaops.cli ask --as E001 "Where do I find the VPN access steps?"
```

Maya (`E001`) must not receive a citation or content from `documents/manager_playbook/`.
Amir (`E006`) must be able to retrieve it. Maya must still be able to retrieve the unrestricted
IT KB article.

## What I'd ask in a viva

1. **Why isn't `systems_allowed_by_role` enough to identify seat holders?** It describes
   entitlement, not an actual seat assignment. The tool keeps those facts distinct.
2. **Why use `manager_id` instead of `user_group`?** The dataset deliberately includes managers
   in the regular user group, so authorization must follow the employee relationship.
3. **Why allow documents without `audience`?** Most of the corpus has no audience field; treating
   absence as restricted would incorrectly hide ordinary policy and IT knowledge.
