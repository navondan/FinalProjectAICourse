# M6 - Observability and evaluation

## Trace binding

`novaops/core/tracing.py` is the single Langfuse binding for the shared Maya/Webex graph.
When both Langfuse keys are present, it attaches the Langfuse LangChain callback, propagates
caller identity, thread session, and tags, and creates a root `novaops.agent` chain span.
Graph node spans include `classify`, `scope`, `tools`, and `answer`. Conditional edges use
named routers (`route_after_retrieve`, `route_after_answer`, and so on) so routing decisions
appear as readable spans rather than `<unknown>` lambdas. Each MCP call is wrapped in a
`tool.<name>` observation (`tracing.tool_observation`) carrying the tool arguments and result;
FastMCP's own nested `<name>` span records only the protocol call. The root span records one
terminal `status`, the final answer, selected scope, current request ID, workflow request ID,
and a compact trajectory digest of tools, arguments, results, and access decision. The digest
is valid JSON and capped at 1,500 characters. With no keys, tracing remains local and
disabled; a partial key pair is rejected.

`new_request_id()` preserves a valid inbound `X-Request-ID` and generates a UUID otherwise.
The CLI accepts `--request-id`; `MCPToolExecutor` forwards it as an HTTP header. Request IDs
are checkpointed, and approval resume updates the current request ID while retaining the
original workflow request ID. This links a resumed action to its initiating turn without
reusing the incoming approval request's ID as if it were the same HTTP request.

## Deterministic evaluation

`novaops/evals/checks.py` implements eight checks over recorded results: required facts,
forbidden facts, forbidden tools, architecture-aware expected tools, expected sources,
forbidden sources, whether tools should run, and terminal-status/answer consistency.
Binding facts, sources, forbidden tools, and tool-use expectations remain strict. Expected
tool names are illustrative; `ALWAYS_REASONABLE` permits only known read-only equivalents.
`session_checks.py` adds focused constraint-carry, closed-branch, citation, replay-idempotency,
and restart-lineage checks. `judges.py` defines four judge rubrics, including the Webex
overstatement rubric. Its deterministic companion scores 0.0 when a pending, refused,
blocked, or human-gated answer positively claims that access was granted.

The runner consumes recorded output rather than issuing model or agent calls. Each run file
is a JSON object keyed by a session turn (`S1-quick-policy:1`) or single-turn case ID:

```json
{
  "S1-quick-policy:1": {
    "answer": "The answer with its evidence citation.",
    "status": "completed",
    "tool_history": [
      {"name": "get_policy", "arguments": {}, "result": {"source": "documents/policies/example.md"}}
    ]
  }
}
```

Run and compare reports with:

```powershell
.\.venv\Scripts\python -m novaops.cli eval --runs .\runs.json --output .\eval-report.json
.\.venv\Scripts\python -m novaops.cli eval --runs .\runs.json --baseline .\baseline.json
```

Classifier and tool-planner responses accept either a plain JSON object or one complete
Markdown-fenced JSON object. Prose around the object and non-object JSON remain errors.

Maya policy questions call permission-filtered knowledge-base search in the application
retrieval node, rather than asking the model to construct a tool call or looking up a guessed
policy filename. Each policy turn searches the current request with the trusted caller ID. A
request specifically about the manager playbook or guide is restricted to matching
`documents/manager_playbook/` results after permission-filtered retrieval. A regular employee
therefore receives no manager-playbook content even if unrelated public results match; when
there is no permitted playbook result, the graph returns a cited refusal without another model
call. Regression tests cover the S9 employee refusal, manager access, and current-request
retrieval on follow-up turns.

## Langfuse judge registration

`novaops/evals/register_judges.py` supports a no-network dry run and explicit `--enable`
registration. It references the Bedrock model by provider and model ID and never sends AWS
credentials. Langfuse 5.2 exposes evaluator creation but does not expose LLM-connection
creation, so the Bedrock provider must first be configured in the Langfuse project. This
adapts the plan's connection setup rather than inventing an undocumented endpoint or
uploading credentials.

## Verification and limitations

```powershell
.\.venv\Scripts\python -m pytest tests\test_tracing.py tests\test_evals.py tests\test_mcp_client.py
.\.venv\Scripts\python -m pytest tests\test_agent_graph.py tests\test_webex.py
.\.venv\Scripts\python -m novaops.cli eval --help
.\.venv\Scripts\python -m novaops.evals.register_judges --help
.\.venv\Scripts\python -m pytest
```

The earlier lesson evaluator source files and its approximately 2 MB recorded baseline bundle
are not in this workspace. The checks, rubrics, runner, and comparer are reimplemented against
the supplied golden-data contract; no missing lesson code or baseline results are represented
as ported. A live Langfuse trace was not sent during local verification. Before submission,
run a turn from the S9 fixture with Langfuse configured:

```powershell
.\.venv\Scripts\python -m novaops.cli chat --as E001 --thread-id m6-s9 --request-id m6-s9-turn-1 "What does the manager playbook say about giving feedback?"
```

In Langfuse, personally confirm the trace answers who asked, the chosen scope, tools run,
terminal status, and the evidence/decision behind it.

Langfuse Cloud removed `GET /api/public/traces` on 2026-11-16 (it now returns HTTP 410).
Read recorded traces through `GET /api/public/v2/observations` and group rows by `traceId`;
the root observation is named `novaops.agent` and carries `userId`, `sessionId`, `status`,
`scope`, and the `trajectory_digest.*` metadata. NovaOps itself only writes traces through
the SDK's OpenTelemetry exporter, so ingestion is unaffected.
