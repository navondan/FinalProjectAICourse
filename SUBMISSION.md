# NovaOps final project — submission

Fill this in and commit it to your repository as `SUBMISSION.md`. It is the first thing read,
and it is how every trace gets found.

---

## 1. Repository

| | |
| ------------------- | ------------------------------------------------ |
| **Repository URL**  | `https://github.com/navondan/FinalProjectAICourse` |
| **Access**          | public |
| **Commit reviewed** | `12c52746b06a634a20efc6310a8970d5a694e2b7` (code; evaluation run tag `20261119T164501`) |

## 2. Langfuse

| | |
| ------------------ | --------------------------------------------- |
| **Host region**    | `cloud.langfuse.com` EU |
| **Project name**   | My Project |
| **Instructor role**| Member *(required — traces are read through the API)* |
| **Invitation accepted** | no — invitation pending (instructor email needed) |

## 3. Scope completed

| Workflow | Built | Notes |
| --------------------------- | ------ | ----- |
| 1 — Maya · HR and onboarding | required | yes — `novaops/workflows/maya/`, `docs/walkthrough/M4-maya.md` |
| 2 — Webex · IT operations    | required | yes — `novaops/workflows/webex/`, `docs/walkthrough/M5-webex.md` |
| 3 — Vendor · CRM extraction  | no | not attempted |
| 4 — Renewal · contract renewal | no | not attempted |

| Optional stage | Done | Evidence |
| ------------------------------- | -------- | -------- |
| Lesson 12 — loop engineering     | no | Offline scorer with golden before/after in `eval-runs/` (0.323 → 0.686); not a full loop |
| Lesson 14 — packaging and deploy | no | |

## 4. Durable-behavior evidence

Some required behavior cannot be seen in a single trace. Point at the commit, test, or trace
that proves each — one line, or "not attempted".

| Check | Evidence |
| ------------------------------------------------------------- | -------- |
| Replaying a request creates no second ticket or access request | `tests/test_database.py::test_access_and_ticket_writes_are_idempotent`; replay step of `tests/test_webex.py::test_webex_idempotent_restart_resume_after_approval` |
| A pending approval survives the process being killed and restarted | `tests/test_webex.py::test_webex_idempotent_restart_resume_after_approval` — interrupt persisted in the SQLite checkpointer, resumed by a fresh graph and connection, same `workflow_request_id` |
| The write gate releases only against a recorded approval | `tests/test_write_gate.py`; `tests/test_mcp_tools.py::test_access_request_tool_requires_persisted_exact_scope_approval`; `tests/test_webex.py::test_unauthorized_cli_approver_cannot_release_an_interrupted_write` |
| *Renewal (optional): every event replayed twice leaves one update and one notification per recipient* | not attempted |

## 5. Trace index

One row per item in `EVALUATION-INPUTS.yaml` — 27 required, 33 with both optional workflows.
**Fill in the trace id column only**; the ids and turn numbers are already correct. Leave a row
blank if you did not run it.


### Maya - HR and onboarding

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `M-I-01` | — | `56d089469ad392a17277511fe631eb19` |
| `M-I-02` | — | `0d43c2077c854dd01b70513ed872807d` |
| `M-I-03` | — | `e99c0264148549f64f7940b49a3a96ac` |
| `M-S-01` | 1 | `5a6341de4ddd0312510cafb76f21269f` |
| `M-S-01` | 2 | `3d04cbf46ee8af0c00d51b141d074117` |
| `M-S-01` | 3 | `ad5674be784b032572d753e61aa17010` |
| `M-S-01` | 4 | `6749e7dacf5ea7237e7e70c9d2ec20ba` |
| `M-S-01` | 5 | `f869eea990931cd58e36db8f9797e227` |
| `M-S-01` | 6 | `295c23fb1347ff9e95978086a08ab8c5` |
| `M-S-01` | 7 | `daf7e3e93c0b6497e097f1e111c1a068` |
| `M-S-01` | 8 | `ca35981607c32654efece937593913b5` |
| `M-S-01` | 9 | `280bba3b711b95688b1b0c0dfe2f2a05` |
| `M-S-01` | 10 | `44376fc6c08bb52457e8e35698ef346e` |
| `M-S-01` | 11 | `63a5c12099e56fc76e0ecca4af774dfe` |
| `M-S-01` | 12 | `d2eb598e5ae269233ad601d2953f1015` |
| `M-S-02` | 1 | `0d93a2a73c784c5627d44f9fb553828c` |
| `M-S-02` | 2 | `aaf9c1f00638f7da12b0dd43d6e50d50` |
| `M-S-02` | 3 | `477e84103bca6337d4af7a67360d21ee` |
| `M-S-02` | 4 | `58f958bf88c7037ba2d9142f53c8195a` |
| `M-S-02` | 5 | `f60ce529bab93816c7e714cb3e569d2d` |

### Webex - IT operations

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `W-I-01` | — | `ec8a557159323e452868c7da8db98f79` |
| `W-I-02` | — | `9036cc3e7d01c1ddded42842ff5eeaac` |
| `W-I-03` | — | `69372c1709b7c38531cea045fba58983` |
| `W-S-01` | 1 | `d44b4ff5bce89a650c5aae01b7a4c064` |
| `W-S-01` | 2 | `d1877c6a25dc494a35b22047def46169` |
| `W-S-01` | 3 | `63fb52d23763e1ac93d39673e14ba1f4` |
| `W-S-01` | 4 | `929734d9db17f184c703847b6c80c75f` |

### Vendor - CRM extraction  *(optional)*

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `V-I-01` | — | |
| `V-I-02` | — | |
| `V-I-03` | — | |

### Renewal - contract renewal  *(optional)*

| Item | Turn | Trace ID |
| ---- | ---- | -------- |
| `R-I-01` | — | |
| `R-I-02` | — | |
| `R-I-03` | — | |

---

## 6. Anything I should know

Two or three sentences, optional. Known gaps, a deliberate deviation and why, something you
would do differently. Honesty here costs nothing and helps — a stated limitation reads better
than one discovered while marking.

Each evaluation item ran once through `cli run-set` on a freshly seeded database (session turns
share one database and thread); outputs are in `eval-runs/runs-evaluation.json` and
`eval-runs/trace-index-evaluation.json`. Known gap: in `W-S-01` turns 3–4 the misspelled name
"rrachel stein" was passed through unresolved, so the agent refused (`blocked`, no write) rather
than guess a person — safe, but not the intended answer. Webex requests for Rachel are otherwise
blocked on recorded evidence (42/40 seats, AR001 blocked), a deliberate M5 decision.
