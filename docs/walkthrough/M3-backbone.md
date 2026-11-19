# M3 · Shared agent backbone

## What changed

Maya and Webex now run through one LangGraph builder. Each turn has one model classification
step that chooses the workflow scope and intent, then the graph selects a scope-specific tool
loadout, retrieves evidence through MCP, asks the model for allowed tool calls or an answer,
executes requested tools through MCP, and rearms once for a final evidence-grounded answer.
Conversation state and both user/assistant turns persist under the MCP-supplied conversation
thread id.

The CLI resolves the caller's group from the employee database; the model cannot choose the
caller or permissions. The shared graph also overwrites any model-proposed caller id with that
trusted application identity before it sends a call across MCP.

## The write gate

`agent/write_gate.py` is the authorization boundary:

```text
model action_requested ----------------------------+
                                                  v
scope loadout -> persisted approval lookup -> write visible?
                    thread + scope +
                    employee + tool +
                    system + access level +
                    exact justification
```

`can_release_write()` is the only authorization check that releases a write tool. Model
classification or chat text alone never creates an approval. The same check is applied again
inside the MCP tool implementation, so calling the tool endpoint directly does not bypass the
graph gate. An approved graph call's employee, system, access level, and justification are
replaced with the values bound to the persisted approval, rather than trusting tool arguments
proposed by the model.

The SQLite checkpointer is separate from application approval records:

```text
agent turn -> AsyncSqliteSaver -> checkpoint database
trusted approval event -> ApprovalStore -> same durable operational SQLite database
```

## Shared graph

```text
START
  |
classify (scope + intent)
  |
scope -> select tools -> retrieve evidence via MCP
                            |
                           model
                         /      \
                    tool calls   answer
                      |            |
                    tools       finalize
                      |
                    rearm
                      |
                    model
```

The graph is the same for both scopes; only `LOADOUTS` differs:

| Intent | Maya examples | Webex examples |
| --- | --- | --- |
| `policy_question` | handbook/policy search | IT guidance and policy search |
| `employee_lookup` | employee record | employee record |
| `onboarding_status` | checklist and asset inventory | checklist and asset inventory |
| `subscription_review` | subscription and knowledge search | subscription, seat-gap report, and search |
| `access_request` | read-only request diagnosis | diagnosis and persisted-approval-gated write |
| `ticket_status` | ticket lookup | ticket lookup |

## Files to know

| File | Why it matters |
| --- | --- |
| `novaops/agent/graph.py` | Shared graph, trusted caller binding, tool cycle, and async SQLite checkpointer |
| `novaops/agent/policy.py` | Scope loadouts and write-tool visibility |
| `novaops/agent/write_gate.py` | Durable, exact-action approval lookup |
| `novaops/agent/mcp_client.py` | Tool call boundary to the running MCP server |
| `novaops/cli.py` | `chat` command backed by the same graph used by tests |

## Verify

```powershell
.\.venv\Scripts\python -m pytest
```

The targeted tests cover distinct Maya/Webex loadouts, two-turn state restoration after closing
and reopening the SQLite saver, caller-id override, rejection of a model-proposed hidden write,
exact approval-scope matching, direct MCP-protocol calls, and MCP-tool enforcement. An
approval for a Webex access request exposes only that exact write tool, never an unrelated
ticket-creation tool.

For a live session, start `python -m novaops.cli serve`, then use two `chat` calls with the same
`--thread-id`. The graph requires the configured document index and Bedrock access for its
classification and response calls.

## What I'd ask in a viva

1. **Why is there a `scope` node after classification?** Classification is the only router; the
   scope node validates the selected workflow and keeps graph flow explicit without
   reclassifying the message.
2. **Why check approvals both during tool selection and again in the MCP tool?** Tool selection
   keeps writes out of model context until authorized, while the tool-side check protects the
   server boundary from direct or stale calls.
3. **What survives restarting the process?** LangGraph turns, including user and assistant
   history, are stored in the SQLite checkpointer under the conversation thread id; approval
   decisions are separately stored in SQLite and matched to exact write scope.
