# NovaOps Final Project — Build Plan

**Target: 120/100** (Maya + Webex + observability + Vendor + Renewal + packaging).

This plan is written around **what you already have**, not a greenfield start. Each milestone
says what gets built, **where you have to step in**, and **what artifact will let you understand
it** without reading every line of code.

---

## Status — read this first, update it last

**Living section.** Read it at the start of every working session; update it before ending one.
Everything below it is the original plan, kept for its reasoning, with *As built* notes where
reality diverged.

**Last updated:** 2026-11-19 · **Tests:** 88 passing (`.\.venv\Scripts\python -m pytest`) ·
**Repository:** https://github.com/navondan/FinalProjectAICourse (`main`, pushed 2026-11-19) · **Deadline:** none fixed — order by marks per
effort, required path first.

| # | Milestone | State | Evidence |
| --- | --- | --- | --- |
| M0 | Repo skeleton | ✅ done | `docs/walkthrough/M0-repo-skeleton.md`; git initialized 2026-11-19 |
| M1 | Foundation | ✅ done | `M1-foundation.md`; `tests/test_ingest.py`, `test_mcp_tools.py` |
| M2 | Permissions + seat gap | ✅ done | `M2-permissions-and-seat-gap.md`; `tests/test_retrieval.py` |
| M3 | Agent backbone | ✅ done | `M3-backbone.md`; `tests/test_agent_graph.py`, `test_write_gate.py` |
| M4 | Maya | ✅ done | `M4-maya.md`; `tests/test_maya.py` |
| M5 | Webex | ✅ done | `M5-webex.md`; `tests/test_webex.py`, `test_database.py` |
| M6 | Observability ⭐ | ✅ done | `M6-observability.md`; live S9 trace, session `m6-s9-20261119b` |
| M6.5 | Evaluation run harness ⭐ | ✅ done | `M6.5-harness.md`; `cli run-set`; golden 79 turns: baseline 0.323 (67 failed) → 0.686 (0 failed), `eval-runs/` |
| M7 | Vendor *(+5)* | ⬜ not started | `novaops/workflows/vendor/` is an empty package |
| M8 | Renewal *(+10)* | ⬜ not started | `novaops/workflows/renewal/` is an empty package |
| M9 | Eval loop | ⬜ not started | — |
| M10 | Packaging *(+5)* | ⬜ not started | — |
| **M11** | **Submission ⭐** | ⏭️ **next** | — |

**Next action:** M11 — run `EVALUATION-INPUTS.yaml` once with `cli run-set` (27 required
turns), then fill `SUBMISSION.md` from `trace-index-evaluation.json`.

**Known golden gaps (by design, not bugs):** S6/S7/S8 turn 4 expect the Webex request to be
filed, but the M5 decision blocks it on recorded evidence (42/40 seats, AR001 blocked).
Golden `expected_sources` that name DB tables (`contracts`, `onboarding_tasks`) can never
match a document path.

**Open items that need you (not code):**
- [x] Create your own remote repository and push `main` — done 2026-11-19. Push after each
      milestone (`git push`).
- [ ] Get the instructor's email and invite them to Langfuse with the **Member** role.
- [ ] Remove unused keys from your local `.env` (`OPENSEARCH_COLLECTION`, `GOOGLE_API_KEY`,
      `GEMINI_MODEL_ID` — leftovers from earlier lessons; no code reads them).

### Deviations from this plan (as built)

| Plan said | What was built | Why |
| --- | --- | --- |
| Port L7 `reranker.py` (M1) | No reranker; FAISS + BM25 + RRF fusion only (`retrieval/store.py`) | Hybrid fusion met the M1/M2 checks; a reranker adds a model call per query |
| `register_judges.py` auto-creates the Bedrock LLM connection (M6) | Dry-run/enable registration only; connection configured manually in Langfuse | Langfuse SDK 5.2 exposes no LLM-connection API; never upload AWS keys |
| Port ~2 MB of L10 recorded baselines (M6) | Checks/judges/runner/comparer reimplemented; no baselines | Baseline bundle not present in this workspace; none fabricated |
| Re-score history with `run_batched_evaluation` (M6) | Evaluator scores recorded run files offline (`cli eval`) | Offline scoring is reproducible and needs no Langfuse read access |
| Fix the 7 failing L10 homework test classes (M4) | Homework tests not ported; Maya covered by `tests/test_maya.py` | The homework code was replaced, not ported |
| Graph routes as anonymous lambdas | Named routers `route_after_*`; tool spans `tool.<name>` with input/output | Readable traces (M6 VERIFY, 2026-11-19) |
| Harness calls the running `serve` process (M6.5) | MCP server hosted in-process per case (same FastMCP server + `MCPToolExecutor`) | Per-case seeded DB without restarting a server; tool spans unchanged |
| Golden single-turn cases name a caller (M6.5) | They name only a group; replayed as E010 / E004 / E007 | Fixed, documented mapping (`GOLDEN_GROUP_CALLERS`) |
| Maya citation violation fails the turn (M4) | Citation contract + one retry + cited fallback answer | Nova Lite wrote uncited markdown; 100% of policy turns failed |
| Any tool / plan / checklist error fails the turn | Read-tool errors returned to the model with one re-plan; plan and checklist retried once; checklist DB fallback; unresolvable target → `blocked` | Baseline: 67/79 golden turns failed on recoverable errors |
| Scorer compares sources by equality (M6) | Path-suffix match | Golden sources are suffixes (`equipment_policy.md`) |

### Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Langfuse Cloud removed `GET /api/public/traces` on 2026-11-16 (HTTP 410) | L12 `langfuse_reader.py`, L11 `dashboard.py` and any v1 trace reads break (M9, M11 trace index) | Read via `GET /api/public/v2/observations`, grouped by `traceId`; record trace IDs locally at run time (M6.5) |
| Unordered runs share one database | Renewal cases suppress each other; Webex idempotency hides later writes | ✅ Mitigated: harness reseeds per independent case (M6.5) |
| Target 120/100 with optional M7–M10 | Time spent on upside before the required run exists | M6.5 and M11 dry run before any optional milestone |

---

## 0. Where you're actually starting from

> **Historical — the 2026-10-22 survey.** Every gap in the table below was closed in M0–M6
> (see Status). Kept for the reasoning behind the design, not as a to-do list.

**A full survey of all 14 lesson folders was done on 2026-10-22. The headline: you have far more
than expected — roughly 70% of the required system already exists in working form.** The project
is a **merge, a repoint, and three hardening fixes**, plus two genuinely new modules.

### Finding 0 — your work is not where it looks like it is

The standalone `*-sdd-*` top-level folders are mostly **empty shells** (data only). Your actual
spec-driven work lives in **`SDD\` subfolders inside each main lesson folder**. Look there
first.

### The assets that matter

**⭐ `lesson-07-rag-advanced-…\lesson-07-rag-advanced\SDD\` — the crown jewel**
*State: production-ish.* `ingest.py` with YAML front-matter parsing, `audience` metadata and
env-tunable chunking; `retrieval.py` with **permission filtering at the retrieval layer,
fail-closed**, hybrid BM25+vector, RRF fusion; `reranker.py`; `eval_harness.py` with Recall@k
and MRR plus 3 judges; 13 labeled eval questions. **Two measured chunk configs on disk**
(250/50 and 320/80) — the spec's "chunking chosen by measurement", already done.

**⭐ `lesson-09-…\code\03-human-in-the-loop\graph.py`**
*State: copy nearly verbatim.* Durable `interrupt()` + `AsyncSqliteSaver` + a `start`/`resume`
CLI that exits the process between the two. This is the capstone's single hardest requirement
(M5's kill-and-restart test), already built and working.

**`lesson-08-…\code\03-multi-tool-server\`**
*State: best starting point for M1.* FastMCP server with **9 of your 11 tools**,
streamable-HTTP on :9876, SQLite `db.py`, OpenSearch-backed `rag.py`, plus a tool-trajectory
eval harness. Homework 1/2/3 all completed.

**`lesson-03-…\SDD\03-validate-and-retry.py`**
*State: rename the tool, swap the schema.* One `converse` call, **forced tool call**, **local
`jsonschema` validation**, sensible tenacity retry policy. ~80% of Workflow 3 (Vendor).

**`lesson-11-build\`**
*State: the backbone for M3/M4.* Lesson 10 stage-3 graph
(`distil → plan → select → model → {tools | rearm}`), and **`run.py` — the best single asset**:
session replay, per-turn root spans, `propagate_attributes` identity, and `trajectory_digest()`
for server-side judges. Runs the **12-turn `S2`** today; `logs/full-run-S2.log` proves it.

**`lesson-10-…\homework\maya\`**
*State: schemas reusable, graph incomplete.* `OnboardingChecklist` / `ChecklistTask` /
`EvidenceCitation` / `WebexHandoff` (with a sha256 idempotency key); `policy.py` with
`FORBIDDEN_TOOLS` and a `select_tools` that never releases writes; `ports.py`; `retrieval.py`.
Milestones 1–3 done, 4–5 abandoned.

**`lesson-10-…\code\evals\`**
*State: reusable near-as-is.* 8 deterministic checks, 4 judge rubrics, a session runner, a
cross-stage comparer, a real token meter, and ~2 MB of recorded baselines.

**⚠️ `lesson-11-observability-…\…\homework\`**
*State: use **this** Webex, not the other one.* 7 nodes with a real LLM
(`plan → select → model → tools → rearm → approval_gate → finalize`), its own MCP server, and
14 deterministic checks.

**`lesson-11-homework-webex\evals\`**
*State: swap prompts + tag filter.* `register_judges.py` (Langfuse REST plumbing, auto-creates
the Bedrock LLM connection), `dashboard.py`, and the `webex-overstatement` judge that scores
0.0 when an answer implies access was granted.

**`lesson-12-…\code\03-eval-loop\`**
*State: copy — but note your own L12 homework isn't done.* Complete eval-loop harness: `Gate`
with targets/floors/noise floor, build snapshotting, Langfuse reader, journal — **plus recorded
baselines and 4 frozen builds**.

**`lesson-14-…\code\`**
*State: copy, then add SHA tagging.* `Dockerfile` (multi-stage, non-root uid 10001, per-stage
healthchecks, **worker vs API as two entry points**), `compose.yaml`, LiteLLM gateway config,
`boundary.py` with the **`X-Request-ID` pattern**, and `/health` (no model call) + `/ready`.

### What this changes

Three things I had wrong in the first draft of this plan, now corrected:

1. **The durable approval gate isn't a build — it's a copy.** M5's hardest requirement already
   exists in `lesson-09-…\03-human-in-the-loop\graph.py`.
2. **The retrieval + permission layer isn't a build either.** Lesson 7's SDD does filtering at
   the retrieval layer, fail-closed, with `audience` metadata preserved from front matter.
3. **`lesson-11-homework-webex` is the *weaker* of your two Webex builds.** It has no LLM at all
   — it's a pure-Python decision cascade over hardcoded dicts. Use the archived
   `lesson-11-observability-and-evals-…\homework\` variant instead.

### The real gap list (shorter than you'd think)

| # | Gap | Milestone |
| --- | --- | --- |
| 1 | Corpus is a subset — 29–39 docs; no handbook, manager_playbook or contracts | M1 |
| 2 | `_access_filter` semantics are wrong for NovaOps | M2 |
| 3 | Two tools missing: `search_hr_documents`, `list_onboarding_tasks` | M1 |
| 4 | The seat-gap tool for `W-I-02` | M2 |
| 5 | Write gate reads a model-inferred flag / a request-carried token | M3, M5 |
| 6 | `InMemorySaver` instead of a durable checkpointer | M3 |
| 7 | Tool DB is `sqlite3.connect(":memory:")` | M1 |
| 8 | Idempotency: nothing exists, anywhere | M5 |
| 9 | `S9-maya-own-account` doesn't exist | M4 |
| 10 | `OnboardingChecklist` is declared but never produced | M4 |
| 11 | Webex handoff node defined but never wired | M4 |
| 12 | Record ids don't match the project's `T001` / `AR001` | M5 |
| 13 | No `classify`/`scope` in Webex, no `tracing.py`, no SHA tags | M3, M6, M10 |
| 14 | Vendor and Renewal don't exist | M7, M8 |
| 15 | 7 failing test classes in the Maya homework | M4 |

The ones that need explaining:

- **#2 — the filter semantics.** L7 returns `{"term": {"audience": "all"}}` for employees. That
  would exclude the **70 of 102 documents with no `audience` field** and fail every case.
  NovaOps needs *absent = unrestricted*, the value is `manager` (not `managers`), and the role
  comes from `manager_id`.
- **#5 — the write gate.** L10 releases on `plan.action_confirmed`, a model-inferred boolean;
  the Webex homework releases on an `approval_token` carried in the request. The spec forbids
  both. It needs a persisted approval row.
- **#6 — durability.** `InMemorySaver` appears in `lesson-11-build\graph.py` and
  `homework\maya\graph.py`. L9's durable version just needs porting over.
- **#7 — the database.** It's rebuilt per process today, so every write vanishes on restart.
  Fine for a lab, fatal for idempotency.
- **#8 — idempotency.** `create_ticket` does `max(id)+1` and unconditionally INSERTs. Replaying
  makes a second ticket. Nothing in any lesson addresses this.
- **#11 — the handoff.** `RealWebexPort` raises `NotImplementedError` and the node hardcodes
  `"E001"`. Homework milestone 5 was never finished.
- **#12 — the ids.** The homework uses `TKT-8842`/`AR-2091`; the archived variant uses
  `T-2001`/`AR-5001`. Neither matches the project.
- **#15 — the tests.** All 7 classes in `test_maya_handoff.py` and `test_maya_permissions.py`
  were failing at last run; the homework was abandoned mid-milestone.

---

## 1. How we'll work together

Three kinds of checkpoint. I'll always say which one I'm asking for.

| Type | What it means | Your time |
| --- | --- | --- |
| 🔵 **DECIDE** | A real choice only you can make. I give options, then wait. | 5–15 min |
| 🟢 **REVIEW** | I built something; read it and say if it matches your intent. | 10–20 min |
| 🟠 **VERIFY** | Run a command or open a trace; confirm my claim is true. | 5–10 min |

**Rule: nothing moves to the next milestone until its VERIFY passes.** The spec is explicit that
one milestone you can defend beats three that half-run.

### How you'll understand the details

Every milestone produces **one explanatory artifact** aimed at you, not at the grader:

- `docs/walkthrough/MXX-<name>.md` — what was built, why, the 5 files that matter, and the
  exact command to prove it works.
- A **diagram** (ASCII, in the walkthrough) whenever the control flow changed.
- A **"what I'd ask in a viva"** section — 3 questions a reviewer might ask about that
  milestone, with the answers. The spec grades you on explaining your system; this rehearses it.

These are for you. They're cheap to write and they double as the raw material for your final
README, which is itself graded.

---

## 2. Milestone map

| # | Milestone | Builds | Effort | Checkpoints |
| --- | --- | --- | --- | --- |
| **M0** | Repo skeleton + decisions | layout, config, `.env`, test runner | S | 🔵🟢 |
| **M1** | Foundation | port L7 ingest + L8 MCP server; full corpus; file DB; 2 tools | M *(was L)* | 🔵🟠 |
| **M2** | Permissions + tool gap | fix filter semantics, `manager_id` rule, seat-gap tool | S *(was M)* | 🔵🟢🟠 |
| **M3** | Agent backbone | merge L11 graph + L9 checkpointer; scopes; write gate | M | 🟢🟠 |
| **M4** | Maya | produce the checklist, wire the handoff, author `S9`, fix 7 tests | L | 🟢🟠 |
| **M5** | Webex | port archived homework, re-key ids, persisted approval, idempotency | L | 🔵🟢🟠 |
| **M6** | Observability ⭐ | write `tracing.py`; port checks + judges; the unshipped checks | M | 🟠 |
| **M6.5** | Evaluation run harness ⭐ | replay golden/eval sets through the agent; reseed; trace index | M | 🟢🟠 |
| **M7** | Vendor *(+5)* | adapt `lesson-03 SDD\03-validate-and-retry.py` | S *(mostly done)* | 🟠 |
| **M8** | Renewal *(+10)* | persistence first, then the workflow. **Genuinely new.** | XL | 🔵🟢🟠 |
| **M9** | Eval loop | copy the L12 harness, point it at your gate | S *(was M)* | 🔵🟢 |
| **M10** | Packaging *(+5)* | copy L14 Dockerfile/compose/gateway; **add SHA tagging** | S *(was M)* | 🔵🟠 |
| **M11** | Submission | run the eval set, README, `SUBMISSION.md`, Langfuse access | M | 🟠🟠🟠 |

⭐ = required. Effort: S ≈ half a session, M ≈ 1 session, L ≈ 2 sessions, XL ≈ 3+.

**Net effect of the survey: roughly four sessions of work moved from "build" to "port."** The
weight has shifted onto M4, M5 and M8 — the three places where something genuinely isn't
finished.

**Required path:** M0–M6.5 + M11. Everything else is upside. If time gets tight, **cut from M8
backwards** (Renewal → Vendor → packaging → eval loop), never from M6 or M6.5.

---

## M0 · Repo skeleton and the decisions that are hard to reverse

**Goal:** one repository, laid out so a reader can navigate it, with the choices locked.

**I build:**
- Repo structure (proposed below), `pyproject.toml`/`requirements.txt`, `.env.example`,
  `.gitignore` that definitely excludes `.env` and `*.db`.
- A `CLAUDE.md` with project conventions so my behavior stays consistent across sessions.
- A stub `README.md` with the claim→file table skeleton that grows all project long.

```
novaops/
  core/            model.py (one model call), identity.py, permissions.py, status.py
  retrieval/       ingest.py, store.py, filters.py
  mcp_server/      server.py, tools/*.py
  agent/           graph.py, policy.py (LOADOUTS), schemas.py, write_gate.py
  workflows/
    maya/          scope.py, checklist.py
    webex/         scope.py, decision.py, approvals.py
    vendor/        extract.py, schemas/, sources/
    renewal/       persistence.py, workflow.py, ports/, fixtures/
  evals/           checks_*.py, judges.py, datasets.py
  tests/
  docs/walkthrough/
  data/            (the NovaOps dataset, or a submodule/copy)
```

🔵 **DECIDE — four locked-in choices.** The survey changes what's at stake in each:
1. **Retrieval store — now a real cost/benefit call.** Your Lesson 7 SDD work is built on **AWS
   OpenSearch Serverless**, which means the better code requires a live (billed) AOSS
   collection. Lesson 6 SDD has a working **FAISS `IndexFlatIP`** path, but with *no
   front-matter parsing and no metadata* — you'd port L7's `parse_frontmatter()` and reimplement
   `_access_filter` as a Python-side pre-filter. *My lean: FAISS + ported metadata, unless you
   already have AOSS running and are happy to keep paying for it. The grader reads your code,
   not your infrastructure bill — and a local store makes your repo clonable-and-runnable, which
   the submission checklist asks for.*
2. **Maya + Webex: one graph or two?** *Lean: one graph, two scopes — and this is now cheaper
   than it looked, because `lesson-11-build\graph.py` is already the backbone and the archived
   Webex homework already has `plan → select → model → tools → rearm` nodes that line up with
   it.*
3. **Where the dataset lives** — copy it into the repo. *Lean: yes; the grader shouldn't hunt.*
4. **Which Webex build to port** — I'd recommend the **archived
   `lesson-11-observability-and-evals-…\homework\`** variant (real LLM, MCP server, 14 checks)
   over `lesson-11-homework-webex` (no LLM, hardcoded dicts) — but keep the latter's
   `register_judges.py` and `dashboard.py`, which the archived one doesn't have.

🟢 **REVIEW:** the folder layout + `CLAUDE.md`. 10 minutes. If the layout doesn't match how you
think about the system, say so now — moving it later is painful.

**Done when:** `git clone` into an empty dir, install, and `pytest` runs (zero tests is fine).

---

## M1 · Foundation — the world is loaded and reachable

**Goal:** documents searchable, database queryable, tools served over MCP, one model function.

**This is now mostly porting.** Sources, in order of use:

**I build:**
- **Ingest** — port `lesson-07-…\SDD\ingest.py` (`parse_frontmatter()` + `chunk_text()` +
  `audience` metadata, already env-tunable via `CHUNK_WORDS`/`OVERLAP_WORDS`) and point it at
  all 102 documents. Your two measured configs (250/50, 320/80) carry over as the justification
  for whichever chunk size you keep — that's the spec's "chunking chosen by measurement",
  already paid for.
- **Retrieval** — port `lesson-07-…\SDD\retrieval.py` (hybrid BM25+vector, RRF fusion, filters
  injected into both branches) and `reranker.py`. ⚠️ One upgrade worth making: the reranker
  regex-extracts JSON from free text; switch it to a forced tool call like L3's.
  *As built: no reranker — FAISS + BM25 + RRF only (see Status → Deviations).*
- **Database** — port `lesson-08-…\code\03-multi-tool-server\server\db.py`, but **change
  `sqlite3.connect(":memory:")` to a file path.** Today the DB is rebuilt per process and every
  write vanishes on restart; idempotency is impossible against it.
- **MCP server** — port `…\03-multi-tool-server\server\server.py` (FastMCP, streamable-HTTP on
  :9876, 9 tools already there) and **add the two missing tools**: `search_hr_documents` and
  `list_onboarding_tasks`. Connect via the `MultiServerMCPClient` seam in
  `lesson-09-…\code\*\common.py`.
- **`core/model.py`** — one Bedrock function. Candidates to merge:
  `lesson-04-tool-use\homework\02-validate-json.py:call_model()` and L7's
  `client.py:embed_text()`.

🟠 **VERIFY:** I'll give you a one-liner. Ask a policy question, get back a cited answer naming
the right document. That's the spec's own done-when.

```powershell
python -m novaops.cli ask "What's the laptop policy for remote employees?"
# expect: an answer + a citation to a real file under documents/
```

🔵 **DECIDE** (small, but it bites later): **the clock.** The dataset's `as_of_date` is
2026-07-02 and the eval set must be pinned to **2026-07-01**. I'll make "now" an injectable
parameter rather than `datetime.now()`. You just need to know this exists, because every
"currently blocked" and "in renewal window" answer depends on it.

**Artifact:** `docs/walkthrough/M1-foundation.md` — the ingest pipeline diagram, where
`audience` lives in the index, and how to re-ingest from scratch.

---

## M2 · Permissions and the tool-inventory gap

This milestone is small in code and large in marks. It's two of the spec's deliberate traps —
and the survey found that **you have a filter already, with the wrong semantics.**

**I build:**
- **Fix `_access_filter`.** Lesson 7's version is structurally right (injected into both
  retrieval branches, fails closed for unknown roles) but wrong for this corpus:

```python
# lesson-07-...\SDD\retrieval.py — as it stands today
if user_role == "employee": return {"term": {"audience": "all"}}        # ← excludes 70 of 102 docs
if user_role == "manager":  return {"terms": {"audience": ["all", "managers"]}}  # ← NovaOps says "manager"
return {"term": {"audience": "___denied___"}}
```

  Three corrections: **absent `audience` must mean unrestricted** (only `handbook` and
  `manager_playbook` carry the field at all — a `term: all` filter would block the other 70
  documents and fail every case); the restricted value is **`manager`**, singular; and fail
  closed **within the restricted collection**, not across the corpus.
- **`is_manager(caller)` = "does any employee have `manager_id == caller.id`?"** — *not* derived
  from `user_group`. Yael Romano (`E018`) is a director on `UG_REGULAR`; a `user_group` check
  gets her wrong. Note L7's filter takes a `user_role` string, so this becomes the function that
  computes it.
- **The tool-inventory audit.** The spec says at least one required capability has no tool
  behind it anywhere in the course — and the survey confirms it. The clear one is `W-I-02`: the
  DB stores seat *counts*, not seat *assignments*, so "who on my team has a Webex seat?" has no
  precise answer. The fix is engineering, not modesty: **a tool that reports the gap**, so the
  honest answer is grounded in a tool result. I'll audit the full inventory against all 27
  required cases.

🔵 **DECIDE:** which missing tools we build, and their exact contracts. I'll bring a table of
"case → capability needed → existing tool? → proposed tool". ~15 minutes, and it's one of the
few places the spec openly says you're being tested on noticing something.

🟢 **REVIEW:** the manager rule and the filter semantics, stated in one paragraph. This paragraph
goes in your README verbatim — the spec requires you to state whichever rule you implemented.

🟠 **VERIFY:** run two queries yourself:
```powershell
# Maya Cohen, UG_REGULAR -> manager_playbook must be UNREACHABLE
python -m novaops.cli ask --as E001 "What does the manager's guide say about feedback?"

# a real manager -> the same question is reachable
python -m novaops.cli ask --as E006 "What does the manager's guide say about feedback?"
```
and confirm a plain IT KB article (no `audience` field) is still readable by everyone.

---

## M3 · The agent backbone — merge the two halves

**Goal:** one graph, two scopes, a write gate that releases nothing, state that survives a
restart.

**I build:**
- Port `lesson-11-build\graph.py` in as the backbone (`distil → plan → select → model → tools`
  plus the `rearm` fallback). **Extend, don't rewrite** — your project delta is the corpus and
  the sessions, not new orchestration.
- Add an explicit **`retrieve` (gather evidence) node** — it exists in `homework\maya\graph.py`
  but not in `lesson-11-build\graph.py`. Merge it in.
- `LOADOUTS` gains a **scope dimension**. Today it's a flat 8-intent table
  (`policy_question`, `employee_lookup`, `onboarding_status`, `equipment_request`,
  `subscription_review`, `access_request`, `ticket_status`, `other`) with
  `WRITE_TOOLS = {"create_access_request"}`.
- **Swap `InMemorySaver` → durable checkpointer by porting
  `lesson-09-…\code\03-human-in-the-loop\graph.py`**, which already does
  `AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB)` and has the `start`/`resume` CLI that exits
  the process in between. This is the single biggest "already built" win in the project.
- **Split visibility from authorization**, explicitly. Today `select_tools` does:

```python
# lesson-11-build\agent.py — the anti-pattern the spec names
if plan.current_intent == "access_request" and plan.action_confirmed:   # model-inferred bool!
    selected += sorted(WRITE_TOOLS)
```

  which becomes:

```python
selected = LOADOUTS.get(plan.current_intent)            # visibility: model-inferred is fine here
if plan.current_intent == "access_request" and approval_is_recorded(thread_id, scope):
    selected += sorted(WRITE_TOOLS)                      # authorization: a persisted row only
```

  (The other Webex build gates on `RachelRequest.approval_token` carried in the request —
  closer, but the spec explicitly rejects a token passed in. Both converge on the same fix.)
- The classify step **is** the router. No second router above it.

🟢 **REVIEW:** `agent/write_gate.py`. It should be short enough to read in two minutes. The spec
says a reviewer should be able to find *the one function that can release a write* — so that's
the test: can you find it, and is there only one?

🟠 **VERIFY:** ask two different questions and watch the visible tool set change. I'll print the
loadout per turn.

**Artifact:** `docs/walkthrough/M3-backbone.md` with the merged graph diagram and a
before/after of what changed from `lesson-11-build`.

---

## M4 · Maya — retrieves, cites, never writes

**Goal:** both sessions pass, every claim cited, zero writes.

**This is where your unfinished homework lives.** Lesson 10's homework got through milestones
1–3 and stopped. Specifically:

**I build:**
- **Produce the `OnboardingChecklist`.** The schema is finished and good
  (`homework\maya\schemas.py:108-143` — `ChecklistTask`, `BlockedItem`, `EvidenceCitation`, with
  `evidence: list[EvidenceCitation]` on every item). But it is **dead code**: there is no
  `with_structured_output(OnboardingChecklist)` call anywhere. That node is the work.
- **Wire the Webex handoff.** `handle_webex_handoff` is defined but **never added to the
  graph**, `RealWebexPort` raises `NotImplementedError` (`ports.py:115`), and it hardcodes
  `"E001"` with a canned business reason. `WebexHandoff` already has a sha256
  `compute_idempotency_key()` — good, keep that.
- **Author `S9-maya-own-account`.** `data/sessions.json` has S1–S8 (46 KB, richly annotated with
  `expected_intent` / `forbidden_tools` / `required_facts` / `arg_checks`). **There is no S9** —
  it's new in the project. Five turns: two manager-corpus attempts (turns 1 and 4), a forwarded
  three-person email chain (turn 2) where the answer is *in the thread, not in a search*, and an
  unmarked topic pivot (turn 3).
- **`S2` already runs.** `python run.py` defaults to `--session S2 --turns 12` and
  `logs/full-run-S2.log` proves a completed run. The work here is making it *correct* against
  the wider corpus, not making it run. The two hard turns: **turn 4** is a tangent about
  Rachel's Webex ticket that must not capture the conversation; **turn 9** needs the Q3
  spend-freeze constraint from **turn 1** — and `sessions.json` turn 1 is explicitly annotated
  "needed on turns 7, 9 and 12".
- **Fix the 7 failing test classes** in `homework\tests\test_maya_handoff.py` and
  `test_maya_permissions.py` (all listed in `.pytest_cache/lastfailed`).
  *As built: obsolete — the homework code was replaced, and `tests/test_maya.py` covers Maya.*
- Webex marked **blocked** with the 42-of-40 reason and a citation.
- **Bounded refinement**: at most *one* refined retrieval round, then answer with what you have
  and say what's missing. (The homework graph already does one round — keep that.)

**Already working, don't touch:** `ConversationMemory` distillation is genuinely good. Threshold
`DISTIL_AFTER_MESSAGES = 12` or `DISTIL_AFTER_TOKENS = 2500`; `memory_is_safe()` rejects any
result where `len(new.active_constraints) < len(old)` so constraints can never silently shrink;
messages are never deleted, only `distilled_upto` moves. That mechanism is exactly what makes
turn 1 bind on turn 9.

🟢 **REVIEW:** read one full `S2` transcript end to end. You're looking for three things: does
turn 9 honour turn 1, did turn 4 stay a tangent, and is every claim cited? I'll annotate it.

🟠 **VERIFY:** `pytest tests/test_maya.py -k "no_writes"` — asserts no write tool ever fires in
either session. This is the "Maya is read-only" property proven as a *check*, not a folder rule.

**Note on scoring yourself:** `S1-quick-policy` is your smoke test (4 turns, 1 tool call) — if
it fails, nothing downstream is worth debugging. And `S6` vs `S8` have **identical expectations
by design**, so the delta between them is a pure robustness measure. Best single number you have
while developing.

---

## M5 · Webex — diagnoses, reuses, writes only after a recorded approval

**Goal:** the truthful-report + durable-approval workflow, on the shared backbone.

**I build:**
- **Port the archived `lesson-11-observability-and-evals-…\homework\webex\`** (7 nodes, real
  LLM, its own MCP server on :9878, 14 deterministic checks) onto the shared backbone — **not**
  `lesson-11-homework-webex`, which has no LLM at all and is a pure-Python cascade over
  hardcoded module dicts.
- **Re-key the record ids.** The homework uses `TKT-8842`/`AR-2091`, the archived variant uses
  `T-2001`/`AR-5001`; the project needs **`T001`/`AR001`**. The reuse logic is already there
  (`if d.access_request_id: return {}`), it's just pointed at the wrong fixtures — and the data
  needs to come from the MCP tools, not module-level dicts.
- **Replace the write gate.** Current: `if d.status != "granted" or not d.approval_token: return
  {}` where `approval_token` arrives in the request. Required: a **persisted approval row** plus
  `interrupt()`. The durable half is a port from L9; the approval table is new.
- **Build idempotency from scratch — nothing exists anywhere in any lesson.** `create_ticket`
  currently does `next_num = int(last["ticket_id"][4:]) + 1` and unconditionally INSERTs.
  Replaying makes a second ticket. You need idempotency keys and duplicate-write guards.
  (`WebexHandoff`'s sha256 key from the Maya homework is a good pattern to extend.)
- **Extend `DecisionStatus`.** Today: `granted | pending_approval | refused`. The project needs
  `completed | blocked | pending | needs_human | failed`, and the status must land in trace
  metadata.
- The diagnosis chain: employee → subscription → existing tickets → KB + access policies →
  **42-of-40 blocker**. `AccessDecision` separating **observed facts / actions actually taken /
  recommended next steps**; never claims access was granted.
- The three remaining cases: `W-I-01` (ticket status, control case), `W-I-02` (the no-precise-
  answer case — uses the M2 gap tool), `W-S-01`/`S8` (4-turn write-gate session buried in
  noise).

🔵 **DECIDE:** how an approval actually arrives in your system — a CLI command, an API endpoint,
or a test-injected event. All three are fine; it affects how your demo and your tests read.

🟠 **VERIFY — the two that matter most, and you should run them yourself:**
```powershell
pytest tests/test_webex.py -k "idempotent"   # run the case twice -> row counts unchanged
pytest tests/test_webex.py -k "restart"      # kill between pause and approval, restart, resume
```
The spec's done-when is literally these two. An `InMemorySaver` fails the second one, which is
why M3 swapped it.

---

## M6 · Observability — **required, this is the submission channel**

**Goal:** a stranger opens a trace and follows what happened without reading your code.

**I build:**
- **`core/tracing.py` — this doesn't exist anywhere in any lesson.** Every lesson repeats the
  same one-liner `g.compile().with_config({"callbacks": [CallbackHandler()]})` and puts identity
  in the caller. You need one module wrapping three things: that binding, `propagate_attributes`
  (session/user/tags), and `trajectory_digest()`.
- **The request id** — lift `new_request_id(x_request_id)` from
  `lesson-14-…\code\01-agent-service\boundary.py`, which honours an inbound `X-Request-ID`.
  That's the piece that makes an id survive MCP calls and workflow resumes.
- **`trajectory_digest()`** from `lesson-11-build\run.py:67` — duplicates tools/args/results/
  decision onto the root span's metadata (truncated at 1500 chars) because a server-side
  Langfuse evaluation rule can only read the one observation it's attached to. This is the
  carrier for "tool sequence + terminal status".
- Spans named so `classify → scope → tools → answer` reads top to bottom (node names become span
  names automatically — the archived Webex homework already demonstrates this).
- Exactly one terminal `status` per run, and the answer text must agree with it.
- **`evals/`** — port the generic checks from
  `lesson-11-observability-and-evals\code\03-score-the-run\checks.py` (8 checks with an
  `ALWAYS_REASONABLE` allowlist) and `lesson-10-…\code\evals\` (checks + 4 judge rubrics +
  runner
  + comparer + ~2 MB of recorded baselines). Keep `lesson-11-homework-webex\evals\`'s
  `register_judges.py` (Langfuse REST plumbing, auto-creates the Bedrock LLM connection,
  `--dry-run`/`--enable`) and its **`webex-overstatement` judge** — scores **0.0 when an answer
  implies access was granted while status is `pending_approval`/`refused`**. That judge is
  purpose-built for the thing this project penalizes most.
- **The checks nothing ships** — per-turn constraint carry, idempotency on replay, restart
  survival, closed-branch tool-choice suppression. Explicitly graded, and nobody hands them to
  you.

**Useful detail from the survey:** two score-push styles exist. The homework uses direct
`langfuse.create_score(...)`; the course code uses `run_batched_evaluation`, which lets you
**re-score history** without re-running the agent. Prefer the latter.

*As built: no LLM-connection auto-creation, no recorded baselines, and offline scoring of run
files instead of `run_batched_evaluation` — see Status → Deviations.*

🟠 **VERIFY — do this one personally, it's the most important checkpoint in the project.** Open
Langfuse, pick an `S9` trace at random, and answer out loud: who asked, what scope was chosen,
which tools ran, what the terminal status was, and why. If you can't, the instrumentation isn't
done — and the instructor is going to do exactly this.

⚠️ **A warning about the golden dataset:** its expected *tools* and *routes* were recorded
against the course's reference implementation. The moment you pick your own retrieval store and
tool decomposition, those stop matching a system that's behaving perfectly. **Binding:**
required facts, forbidden facts, forbidden tools, forbidden sources. Chase the illustrative
fields and you'll spend your time fixing your scorer instead of your system.

---

## M6.5 · Evaluation run harness — **required, added 2026-11-19**

**Why this exists:** M11 asks for "the harness that runs `EVALUATION-INPUTS.yaml` correctly",
but it is the largest remaining piece of the required path and nothing here builds it yet.
The existing `cli eval` only *scores* a recorded run file; nothing *produces* one. Building it
now also gives M7–M10 a measurement to improve against.

**I build:**
- `novaops/evals/harness.py` + `cli run-set --set GOLDEN-DATASETS.json|EVALUATION-INPUTS.yaml
  --output runs.json --trace-index trace-index.json`.
- **Isolation:** each one-shot input and each session gets a fresh database copy
  (schema + seed into a temp path) and a fresh checkpoint file. Never reset the developer DB.
- **Continuity:** a session's turns run in order on **one** thread and one database.
- **Clock:** `NOVAOPS_AS_OF_DATE=2026-07-01` for every case (Renewal's effective-date run is the
  documented exception, M8).
- **Identity:** caller comes from the item's `caller.employee_id`; the group is still resolved
  from the database, never taken from the file.
- **One trace per turn:** a deterministic request ID per turn (`<item-id>:<n>`); a
  pause-and-resume stays on the same workflow request ID.
- **Output:** `runs.json` in the format `cli eval` already reads
  (`{"<item-id>:<n>": {answer, status, tool_history}}`), plus `trace-index.json`
  (item → session ID, request ID, Langfuse trace ID) captured **at run time**, so the
  submission index does not depend on the Langfuse read API.
- A failed turn is recorded with `status: "failed"` and its error — never silently dropped.

**Order of use:** run against `GOLDEN-DATASETS.json` while developing (score with `cli eval`).
Run `EVALUATION-INPUTS.yaml` **once**, at M11.

🟢 **REVIEW:** the isolation rules — one paragraph in `docs/walkthrough/M6.5-harness.md`.

🟠 **VERIFY:**
```powershell
.\.venv\Scripts\python -m pytest tests/test_harness.py   # fake model: reseed isolation, session continuity, run-file shape
.\.venv\Scripts\python -m novaops.cli run-set --set GOLDEN-DATASETS.json --output runs-golden.json --trace-index trace-index-golden.json
.\.venv\Scripts\python -m novaops.cli eval --runs runs-golden.json --output eval-report-golden.json
```
Record the golden score in Status as the honest baseline M9 starts from.

---

## M7 · Vendor *(optional, +5)* — the cheap win

Smallest milestone with real marks — and the survey found it **~80% pre-built**.

`lesson-03-…\SDD\03-validate-and-retry.py` already does exactly the required shape: one
`converse` call with a **forced** tool (`toolChoice: {"tool": {"name": ...}}`), a
`NoToolUseError` guard, then **local** `jsonschema.Draft202012Validator(schema).validate(data)`
— plus a well-reasoned tenacity policy (retry on validation/throttling, fail fast on auth,
boto's own retries disabled so they don't compound).

**I build:** rename `emit_summary` → `submit_vendor_record`, swap in the supplied vendor schema,
load the three sources, and add the normalization rules — dates → `YYYY-MM-DD`, cost → integer
USD, `null` for anything unstated (never guess from the vendor name), only *required* absences
create follow-up questions. ⚠️ Keep it to **one** semantic call per source — the spec explicitly
says not to reuse the multi-attempt retry wrapper for this.

🟠 **VERIFY:** `pytest tests/test_vendor.py` asserts **0 / 1 / 2** missing required fields across
the intake form / email chain / call transcript. Fully deterministic, no judge needed.

**Boundary check:** if deleting the whole agent graph breaks this module, it's in the wrong
place.

---

## M8 · Renewal *(optional, +10)* — the hard one

**Do this only after M6 is green.** It's the part nobody handed you, which is exactly why it's
worth doing — and why it's last.

**Build order matters here, and it's counterintuitive:**

🔵 **DECIDE first — the persistence contract, before any graph code.** The spec is blunt: *"If
you start with the graph you will rewrite it."* I'll bring you a schema covering: the renewal
run; the recommendation + rationale; the internal decision + actor + approved scope; each
clearance; processed inbound event ids; the provider reply + extracted decision; **the recorded
agreement kept separate from the terms in effect**, with its effective date; every outbound
message + its delivery status (including `uncertain`); and the applied update with its audit
identity.

That "recorded agreement ≠ terms in effect" separation is the single most important line in the
schema. Get it right and step 7 below is trivial; get it wrong and you've built the incident the
PRD was written about.

**Then I build**, in order: ports (`NotificationAdapter`, `ProviderEmailAdapter`, mocks are the
*required* implementation) → the scan (`as_of: 2026-07-01`, 90-day window, notice deadline from
`renewal_notice_days: 45` → 2026-06-05 → **already passed → urgent**) → recommendation → pause →
resume on three events (approval + Finance clearing `AP002` + security review) → provider email
from real contract facts → reply extraction → **scope matching** → record → apply on 2026-07-21
→ notify blocked users.

**The two fixtures that decide this milestone:**
- `conditional` — contains a price, seats, and dates (everything an eager extractor wants)
  *plus* a sentence saying it isn't final. **Write nothing. Escalate.**
- `over_ceiling` — a genuine final approval at USD 24,000 against a 22,000 ceiling. Classifying
  it `approved` is **correct**; recording it is **not**. Match every reply against the approved
  scope, not just its decision field.

Also: the provider email is **untrusted input** — a model reads attacker-controllable text whose
output gates a DB write. Extraction is a *proposal*; deterministic code decides.

🟠 **VERIFY:** run the schedule on 2026-07-01, destroy the process, inject approval + clearances,
resume, inject a reply, **confirm the seat limit still reads 40 on July 3**, then run 2026-07-21
and confirm 50 seats + notifications. Replay every event twice: exactly one update, one
notification per recipient.

Automate each of those as a test before claiming the milestone:
```powershell
.\.venv\Scripts\python -m pytest tests/test_renewal.py -k "scan_urgent"           # 2026-07-01 scan flags C001 urgent
.\.venv\Scripts\python -m pytest tests/test_renewal.py -k "restart"               # new process resumes the paused run
.\.venv\Scripts\python -m pytest tests/test_renewal.py -k "conditional or over_ceiling"  # write nothing, escalate
.\.venv\Scripts\python -m pytest tests/test_renewal.py -k "effective_date"        # 40 on 07-03, 50 on 07-21
.\.venv\Scripts\python -m pytest tests/test_renewal.py -k "replay"                # duplicates: one update, one notice each
```

---

## M9 · Eval loop *(Lesson 12, optional)*

**Your L12 SDD homework isn't done** (the `-sdd` folder has only `agent/` + `tests/`), but the
**course harness is fully built and comes with recorded baselines and 4 frozen builds**. So this
is a port, not a build.

Copy from `lesson-12-…\code\03-eval-loop\`: `loop.py` (evidence → diagnose → change →
re-evaluate, with three budgets), `evals/targets.py` (the `Gate` — targets, **floors like
`forbidden_avoided: 1.0` that aren't tradeable**, and a measured `NOISE_FLOOR = 0.03`),
`shared/builds.py` (snapshot/restore so rounds can branch from best),
`shared/langfuse_reader.py` (returns INVALID on poll timeout rather than scoring partial data),
`shared/journal.py`.

Note the design worth keeping: **the gate never says "pass"** — it reports a *distance*, and the
budget terminates the loop. Also copy the trick of denying the agent edit access to `evals/` so
it can't move the goalposts.

⚠️ **As of 2026-11-16:** `shared/langfuse_reader.py` almost certainly reads the removed v1
`/api/public/traces` endpoint. Port it onto `GET /api/public/v2/observations` (group rows by
`traceId`), or feed the loop from M6.5's local `runs.json` and skip the Langfuse read entirely.

Take an **honest baseline before changing anything**. 🔵 **DECIDE:** which metric you optimize.
🟢 **REVIEW:** each promotion. **Report before *and* after.**

---

## M10 · Packaging *(Lesson 14, optional, +5)*

Also mostly a copy — `lesson-14-…\code\` is complete course code and the SDD homework there was
never materialised, so take the course version.

Directly copyable: `03-containers\Dockerfile` (multi-stage `base → mcp → agent → queue-runtime`,
non-root uid 10001, per-stage `HEALTHCHECK`, `HEALTHCHECK NONE` on the worker — **that's already
the "API and renewal worker as separate entry points" shape**), `compose.yaml` (with
`depends_on: service_healthy` and `stop_grace_period: 30s` so Langfuse can flush),
`Dockerfile.gateway` + `gateway-config.yaml` (LiteLLM, `novaops-approved` alias, no wildcard
route), and `01-agent-service\app.py` — `/health` is process-only with an explicit comment that
a model-calling probe is "a standing bill", `/ready` pings MCP and 503s on failure.

⚠️ **The one real gap: no commit-SHA image tagging anywhere.** Compose uses `build:` only. You
add that.

🔵 **DECIDE:** LiteLLM or not. Nice detail if yes — L14's compose deliberately gives the agent
container **invalid AWS keys** to prove model traffic goes through the gateway. That's a
property *visible by reading the repo*, which is exactly what's gradeable here.

---

## M11 · Submission — the part that's easy to fumble

**I build:** the final README (every claim mapped to the file that implements it — anything
asserted here must be visible in the code), and run M6.5's harness against
`EVALUATION-INPUTS.yaml` once. The harness already enforces:

- **Reseed the DB before each independent case or session.** The three Renewal cases all use
  renewal run `RR-WEBEX-2026` and the same events — run them back to back on one database and
  the first case's write suppresses the second.
- **Preserve state within a session.** A multi-turn session is one thread on one database.
- **Pin the clock to 2026-07-01** everywhere (except Renewal's effective-date run).
- **One trace per turn.** A pause-and-resume stays one trace.

Expect **33 traces** at your scope (20 Maya + 7 Webex + 3 Vendor + 3 Renewal); **27** if you
skip the optional workflows. Fill the `SUBMISSION.md` trace index from M6.5's
`trace-index.json`, not by searching Langfuse afterwards (its v1 trace API is gone).

🟠 **VERIFY — three things only you can do:**
1. **Clone your own repo into an empty directory** and confirm it's readable and complete.
2. **Add your instructor to Langfuse with the `Member` role** — *not* view-only, because traces
   are read through the API and view-only can't. **Confirm the invitation was accepted.**
3. Fill in `SUBMISSION.md` — repo URL, Langfuse host region + project name, which optional
   workflows you built, and the trace index. A run you can't point to didn't happen.

🟠 **Final sweep:** no AWS keys, Langfuse keys, approval tokens, `.env` files, or restricted
source documents committed. I'll run a scan, but you should eyeball `git log -p` for the first
commit too.

---

## 3. Sequencing rules (the ones that actually bite)

1. **M6 → M6.5 before M7–M10.** You can't improve what you haven't measured, and there's no point
   hardening a system whose behavior isn't pinned down.
2. **But bind Langfuse at M3**, not M6. M6 is where it becomes *complete* — nothing stops you
   tracing earlier, and debugging a 12-turn session without traces is masochism.
3. **M2 before M4.** `S9` turns 1 and 4 are permission tests. Building Maya first means
   retrofitting the filter into working code.
4. **M8 last.** It shares the database with the agent; doing it while Maya/Webex are still
   moving means debugging two things at once.
5. **Never skip a VERIFY to save time.** Every one of them is lifted from a spec done-when.

---

## 4. What I need from you to start

*Resolved: the four M0 decisions were made (local FAISS with metadata filtering, one graph with
two scopes, dataset kept in-repo, Webex checks and judges adapted/ported — see
`docs/walkthrough/M0-repo-skeleton.md`). Deadline: none fixed (2026-11-19) — required path
first, then optional work by marks per effort. The instructor-invite and remote-repo items live
in Status → Open items.*

🔵 **Four decisions** (M0) — I'll present options for each: retrieval store, one-graph-vs-two,
dataset placement, port-or-rewrite the Webex evals.

**One thing I don't know and should:** your **deadline**. It determines whether we plan for the
full 120 or protect the required 100 and treat M7–M10 as stretch. Tell me and I'll annotate the
map with a cut line.

**One thing to do yourself, today:** ask in class for the instructor's email address for the
Langfuse invite, and send it. It's the one task with an external dependency and a human in the
loop, and it's the stupidest possible way to lose marks on an otherwise finished project.

---

## 5. The five properties this whole plan exists to produce

Everything above is in service of five things a reader must be able to see:

1. **One agent, two scopes** — retrieval permissions and the write gate implemented **once**.
2. **Writes released by a stored approval record**, never by the model's reading of the chat.
3. **Permissions enforced as a retrieval filter**, before the model sees anything.
4. **Truthful status** — a `blocked` for the right reason beats a confident wrong answer,
   always.
5. **Readable evidence** — the repo and the traces *are* the deliverable.

---

## Appendix A · Reuse map (from the 2026-10-22 survey)

All paths are relative to `C:\Users\navond\PycharmProjects\AICourse\`. The table uses these
shorthand roots, because the real folder names are long:

| Short | Real folder |
| --- | --- |
| `L3` | `lesson-03-…\SDD\` |
| `L4` | `lesson-04-tool-use\` |
| `L6` | `lesson-06-…\lesson-06-rag\SDD\` |
| `L7` | `lesson-07-rag-advanced-20260715T145147Z-1-001\lesson-07-rag-advanced\SDD\` |
| `L8` | `lesson-08-mcp-2.0-20260729T151758Z-1-001\lesson-08-mcp-2.0\code\` |
| `L9` | `lesson-09-agentic-frameworks\code\` |
| `L10` | `lesson-10-context-engineering\` |
| `L11b` | `lesson-11-build\` |
| `L11o` | `lesson-11-observability-and-evals-20260819T165118Z-1-001\lesson-…-evals\` |
| `L11w` | `lesson-11-homework-webex\` |
| `L12` | `lesson-12-…\code\` |
| `L14` | `lesson-14-…\code\` |

| Need | Take from | Change needed |
| --- | --- | --- |
| Front matter + chunking | `L7\ingest.py` | point at 102 docs |
| Retrieval + permission filter | `L7\retrieval.py` | **fix filter semantics** (M2) |
| Reranking | `L7\reranker.py` | regex JSON → forced tool call |
| Retrieval eval harness | `L7\eval_harness.py` | — |
| Labeled questions | `L7\data\eval_questions.jsonl` | new ones for this corpus |
| Vector store (hosted) | `L7\client.py`, `L7\create_index.py` | needs live AOSS |
| Vector store (local) | `L6\ingest.py` (FAISS IndexFlatIP) | **no metadata** — add L7's parser |
| MCP server + 9 tools | `L8\03-multi-tool-server\server\server.py` | add 2 tools, repoint data |
| SQLite tool bodies | `L8\03-multi-tool-server\server\db.py` | `:memory:` → file; idempotency |
| MCP client seam | `L9\*\common.py` | — |
| **Durable approval + resume** | `L9\03-human-in-the-loop\graph.py` | **copy nearly verbatim** |
| Classify/router pattern | `L9\05-router\graph.py` | fold in, don't stack |
| Agent backbone | `L11b\graph.py`, `L11b\agent.py` | checkpointer, gate, scopes |
| Replay + Langfuse identity | `L11b\run.py` | **best single asset** |
| Sessions S1–S8 | `L11b\data\sessions.json` | **author S9** |
| Typed Maya schemas | `L10\homework\maya\schemas.py` | actually *produce* the checklist |
| Maya policy / ports / retrieval | `L10\homework\maya\*.py` | handoff; `RealWebexPort` |
| Distillation / memory | `L10\code\03-history-distillation\` | none — it's good |
| Deterministic checks | `L11o\code\03-score-the-run\checks.py` | — |
| Batched re-scoring | `L11o\code\03-score-the-run\score.py` | — |
| Webex agent (the better one) | `L11o\homework\webex\` + `…\server\` | onto backbone; re-key ids |
| Judges + Langfuse REST | `L11w\evals\register_judges.py` | swap prompts + tag filter |
| Dashboard widgets | `L11w\evals\dashboard.py` | swap `WIDGETS` |
| Session checks / judges / runner | `L10\code\evals\*` | — |
| Forced tool + schema validation | `L3\03-validate-and-retry.py` | → Vendor (M7) |
| Validate-before-dispatch | `L4\homework\02-validate-json.py` | has a `call_model()` too |
| Eval loop + gate + baselines | `L12\03-eval-loop\` | point at your targets |
| Containers, gateway, health | `L14\03-containers\`, `L14\01-agent-service\` | **add SHA tagging** |
| Request-id propagation | `L14\01-agent-service\boundary.py` | → M6 |

### Build from scratch (genuinely nothing exists)

1. **All idempotency machinery** — keys, processed-event tables, duplicate-write guards.
2. **`S9-maya-own-account`** — the 5-turn session.
3. **The seat-gap tool** for `W-I-02`.
4. **`core/tracing.py`** — no lesson has one.
5. **The whole Renewal workflow** (M8).
6. **Commit-SHA image tagging.**
7. **An automated kill-and-resume test** — L9 proves it manually via CLI only.

### Known-bad things to not copy forward

- `_access_filter`'s `{"term": {"audience": "all"}}` employee branch — would block 70 of 102
  docs.
- `plan.action_confirmed` as a write release (L10) and `RachelRequest.approval_token` (Webex
  hw).
- `sqlite3.connect(":memory:")` in the tool server.
- `InMemorySaver()` in `lesson-11-build\graph.py`, `homework\maya\graph.py`,
  `lesson-09-…\SDD\supervisor.py`, and L11-obs's homework graph.
- `lesson-11-homework-webex\webex\tools.py` hardcoded dicts, and ids `TKT-8842`/`AR-2091`.
- `lesson-04-tool-use\SDD\support_agent.py` — good architecture but **Anthropic SDK, not
  Bedrock**.
- The `dashboard.py` unstable API endpoint — fine, but expect it to break.
