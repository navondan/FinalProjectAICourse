# NovaOps Final Project

The capstone. You build NovaOps' **operational company brain** — an internal assistant over a
messy, permission-sensitive enterprise world — then instrument it, run a fixed evaluation set
against it, and hand in the traces.

## Clone

```bash
git clone "https://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@github.com/Experts-ProciGen-AI/project.git"
cd project
```

## What is here

| File | Read it |
| ---------------------------- | ------------------------------------------------------- |
| **[PROJECT-DESCRIPTION.md](PROJECT-DESCRIPTION.md)** | **Start here.** What to build, the four workflows, the milestones, and how you are assessed |
| **[GOLDEN-DATASETS.json](GOLDEN-DATASETS.json)** | Annotated class material — every expectation readable. Develop and self-check against this |
| **[EVALUATION-INPUTS.yaml](EVALUATION-INPUTS.yaml)** | The measured set: questions only, no answers. Run it once, at the end |
| **[SUBMISSION.md](SUBMISSION.md)** | Copy into your own repository and fill in — repo link, Langfuse project, trace index |
| **[novaops-enterprise-agent-dataset/](novaops-enterprise-agent-dataset/)** | The NovaOps world: the document corpus, the operational database, and per-workflow material |

## The shape of it

Four workflows. **Maya** (HR and onboarding) and **Webex** (IT operations) are two scopes on
one conversational agent and are **required**. **Vendor** (one-shot CRM extraction) and
**Renewal** (an offline, event-driven process) are standalone and **optional** — skipping them
costs no marks.

## Two things to know before you start

**You are assessed by reading, not by running.** Nobody executes your code. Your repository
and your Langfuse traces are the whole submission, so instrument early — Lesson 11's work is
required here precisely because it is the channel through which everything is seen.

**This specification is deliberately incomplete.** It describes the behavior you must produce
and the evidence that proves it. It does not enumerate every artefact you will need to get
there, and at least one capability it requires has nothing behind it in any lesson. That is
not an erratum — noticing what nobody handed you is part of the work.

## Implementation

The implementation uses Python, Amazon Bedrock for model calls, LangGraph for the shared
Maya/Webex agent, FastMCP for the tool boundary, local FAISS for retrieval, SQLite for durable
state, and Langfuse for tracing. The repository contains the complete NovaOps dataset in
`novaops-enterprise-agent-dataset/`.

Maya and Webex share one agent graph with separate scopes. Retrieval permissions are enforced
before evidence reaches the model; a write is released only by application code after it finds
a persisted approval. Employees are managers when another employee's `manager_id` points to
them, regardless of their user group. Documents without an `audience` restriction are available
to all callers; restricted manager-playbook material is only available to managers.
The Webex subscription contains only seat counts, not seat assignments; the team seat-gap tool
reports role eligibility, blocked onboarding/access records, and that assignment limitation
instead of guessing who holds a seat.

### Setup

Use Python 3.11 or 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
pytest
```

Set AWS credentials using your normal AWS profile or environment configuration. Put Langfuse
credentials only in `.env`; do not commit that file. `NOVAOPS_AS_OF_DATE` pins date-sensitive
decisions for reproducible evaluation runs.

### Implementation map

| Claim | Implementation |
| --- | --- |
| Shared agent and workflow scopes | `novaops/agent/`, `novaops/workflows/` |
| Permission-aware retrieval | `novaops/retrieval/` |
| Tool server boundary | `novaops/mcp_server/` |
| Durable configuration and state | `novaops/core/`, `novaops-enterprise-agent-dataset/database/` |
| Checks and evaluation datasets | `novaops/evals/`, `tests/` |
| Vendor CRM extraction (optional) | `novaops/workflows/vendor/`, `tests/test_vendor.py` |
| Contract renewal workflow (optional) | `novaops/workflows/renewal/`, `tests/test_renewal.py` |
| Evaluation run harness (agent + workflow items) | `novaops/evals/harness.py`, `novaops/cli.py run-set` |
| Eval loop: gate, journal, human promotion (optional) | `novaops/evals/targets.py`, `novaops/evals/loop.py`, `tests/test_eval_loop.py`, `eval-runs/loop-report.md` |
| HTTP API, worker, containers (optional) | `novaops/api/app.py`, `novaops/workflows/renewal/worker.py`, `Dockerfile`, `compose.yaml`, `scripts/build-images.*`, `tests/test_packaging.py` |
| Milestone walkthroughs | `docs/walkthrough/` |

See `docs/walkthrough/M0-repo-skeleton.md` for the first milestone's design decisions and
verification command.

### Local retrieval and tools

After configuring AWS access to Bedrock, build the local document index and start the tool
server:

```powershell
.\.venv\Scripts\python -m novaops.cli ingest
.\.venv\Scripts\python -m novaops.cli ask "What's the laptop policy for remote employees?"
.\.venv\Scripts\python -m novaops.cli serve
```

The MCP server binds to `127.0.0.1:9876` by default. The SQLite database is initialized from
the supplied schema and seed only when the configured database file has no tables; an existing
partial or populated database is never reset automatically.
The MCP server is a separate process and must itself have access to the AWS profile or
credential chain used for Bedrock embeddings. If credentials are configured after the server
starts, restart it from the configured shell.

With the MCP server running, send one turn through the shared Maya/Webex graph. Reuse a
`--thread-id` to resume a persisted conversation:

```powershell
.\.venv\Scripts\python -m novaops.cli chat --as E004 --thread-id onboarding-1 "Pull up Maya Cohen's employee record."
.\.venv\Scripts\python -m novaops.cli chat --as E004 --thread-id onboarding-1 "What does her checklist say?"
```

The graph resolves the caller's user group from the database, persists conversation state in
SQLite, and calls tools only through the MCP server. A write tool is visible and callable only
when the exact thread, scope, employee, system, access level, and justification match a
persisted approval decision.

For a Webex access request that is not blocked by capacity or an existing request, the graph
pauses for a persisted human decision. In the local demo, an authorized manager can resume it:

```powershell
.\.venv\Scripts\python -m novaops.cli approve --thread-id webex-1 --as E018 --decision approved --reason "Business need confirmed."
```

The CLI checks the approver against the employee and role records, records the decision, and
resumes the SQLite checkpoint. This is a local-demo identity input, not an authentication
mechanism; a deployed approval interface must bind the actor to an authenticated identity.
Submitting a request still leaves its operational status `pending_approval`; it does not grant
system access.

### Observability and recorded-run checks

When both Langfuse keys are configured, each shared-agent invocation records a root
`novaops.agent` trace with the caller as `user_id`, the thread as `session_id`, and child spans
for graph nodes such as `classify`, `scope`, `tools`, and `answer`. The root metadata carries
the terminal status and a tool/argument/result/decision digest capped at 1,500 characters.
Without Langfuse keys, local tracing is disabled. Pass `--request-id` to `chat` or `approve`
to honor an inbound `X-Request-ID`; the ID is sent on MCP HTTP calls and the workflow ID is
preserved when an approval resumes a checkpoint.

The evaluator scores recorded results without calling the agent or model. The JSON run file
maps case IDs such as `S1-quick-policy:1` to result objects containing `answer`, `status`,
and `tool_history`:

```powershell
.\.venv\Scripts\python -m novaops.cli eval --runs .\runs.json --output .\eval-report.json
.\.venv\Scripts\python -m novaops.cli eval --runs .\runs.json --baseline .\baseline.json
```

Four Langfuse judge rubrics can be previewed with
`python -m novaops.evals.register_judges --dry-run`; `--enable` registers them. The current
Langfuse SDK exposes evaluator creation but not LLM-connection creation, so configure the
Bedrock provider in the Langfuse project before enabling judges. NovaOps does not upload AWS
credentials. The supplied workspace has no recorded baseline bundle; the comparer accepts a
saved report but no baseline results are fabricated.

Replay a whole golden or evaluation set (each independent case gets a freshly seeded database;
session turns share one) and record a trace index:

```powershell
.\.venv\Scripts\python -m novaops.cli run-set --set EVALUATION-INPUTS.yaml --output eval-runs\runs.json --trace-index eval-runs\trace-index.json
.\.venv\Scripts\python -m novaops.cli run-set --set EVALUATION-INPUTS.yaml --only V-I-01 R-I-01 --output eval-runs\runs-optional.json --trace-index eval-runs\trace-index-optional.json
```

Vendor (`V-*`) and Renewal (`R-*`) items are run by their workflow services, not the chat agent.

### Vendor and Renewal workflows (optional)

Vendor extracts a CRM vendor record from a document and reports missing fields; it never
invents a value:

```powershell
.\.venv\Scripts\python -m novaops.workflows.vendor path\to\vendor-intake.md
```

Renewal is an explicit SQLite state machine. Every event and reply is recorded once by ID, so
replaying anything leaves one seat update and one notification per recipient. Seats change
only on the effective date, and only for a clear approval that matches the recommendation;
conditional, ambiguous, rejected, or over-ceiling replies go to a human.

```powershell
.\.venv\Scripts\python -m novaops.workflows.renewal --db data\renewal.sqlite3 schedule --as-of 2026-07-01
.\.venv\Scripts\python -m novaops.workflows.renewal --db data\renewal.sqlite3 reply --fixture-id <id>
.\.venv\Scripts\python -m novaops.workflows.renewal --db data\renewal.sqlite3 status
.\.venv\Scripts\python -m novaops.workflows.renewal.worker --once
```

Add `--offline` for the rule-based path with no model calls. The worker runs the
schedule once per business day and consumes `inbox/events/*.json` and `inbox/replies/*.eml`.

### Eval loop (optional)

The loop is evidence → diagnose → change → re-evaluate, fed by local `run-set` files. The gate
(`novaops/evals/targets.py`) reports a *distance* to the declared targets, never a "pass".
Safety floors (`forbidden_tools`, `forbidden_sources`, `forbidden_facts` = 1.0) cannot be
traded for quality, and incomplete runs are INVALID rather than "worse". A named human
promotes each round; budgets stop the loop.

```powershell
.\.venv\Scripts\python -m novaops.evals.loop baseline --runs eval-runs\runs-golden.json
.\.venv\Scripts\python -m novaops.evals.loop diagnose
.\.venv\Scripts\python -m novaops.evals.loop propose --runs eval-runs\runs-golden-loop-r1.json --change "..."
.\.venv\Scripts\python -m novaops.evals.loop review --round 1 --reviewer "Your Name" --decision promote
.\.venv\Scripts\python -m novaops.evals.loop report --output eval-runs\loop-report.md
```

### HTTP API and containers (optional)

```powershell
.\.venv\Scripts\python -m novaops.cli serve
.\.venv\Scripts\python -m uvicorn novaops.api.app:create_app --factory --port 8000
```

`GET /health` checks the process; `GET /ready` checks the database, the index, and the MCP
server's `/health`. `POST /chat` and `POST /approvals` wrap the same agent and approval paths
as the CLI. The `Dockerfile` builds three targets (`mcp`, `api`, `renewal-worker`) as a
non-root user; `compose.yaml` starts them with health-gated ordering. Build images tagged with
the commit SHA (a dirty tree is refused):

```powershell
.\scripts\build-images.ps1
$env:GIT_SHA = (git rev-parse --short HEAD); docker compose up
```

Docker was not available on the development machine, so the images are untested; the API
and MCP health and readiness endpoints were checked live outside containers.
