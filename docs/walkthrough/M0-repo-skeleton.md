# M0 · Repository skeleton and decisions

## What was built

The project now has a single installable Python package, a test configuration, a private
environment-file template, and package boundaries for retrieval, tools, the shared agent,
workflow scopes, and evaluations. The supplied 102-document corpus and operational database
remain in `novaops-enterprise-agent-dataset/`; no duplicate copy is needed.

The selected architecture is local FAISS with metadata-aware filtering, one graph with separate
Maya and Webex scopes, and adapted/ported Webex checks and judges. Bedrock, Langfuse, and the MCP
tool boundary remain part of the chosen stack. The evaluation clock is explicit and defaults to
2026-07-01.

## Configuration path

```text
.env / process environment
             |
             v
    core/config.py
      |      |       \
    paths   date    model + tracing settings
```

`Settings.from_env()` reads `.env` without overriding values already supplied by the process.
Relative data and persistence paths resolve from the project root, and invalid dates, chunk
sizes, or overlap settings raise clear errors. AWS credentials are intentionally not duplicated
in the configuration: boto3 uses the standard AWS credential chain.

## Five files to know

| File | Why it matters |
| --- | --- |
| `pyproject.toml` | Installable package, runtime dependencies, and pytest defaults |
| `.env.example` | Names and safe defaults for local configuration |
| `novaops/core/config.py` | Validates environment-backed settings and project paths |
| `.gitignore` | Keeps secrets and generated local state out of version control |
| `tests/test_config.py` | Protects deterministic defaults and invalid-setting behavior |

## Verify

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

## What I'd ask in a viva

1. **Why is the evaluation date configurable?** Date-dependent decisions must be reproducible
   against the fixed evaluation set instead of drifting with the machine clock.
2. **Why keep AWS keys out of `.env.example`?** boto3 already supports profiles and environment
   credentials; duplicating secrets encourages unsafe storage and defeats the standard chain.
3. **Why use a local vector index?** It keeps the project clonable and avoids a billed external
   search service while still allowing FAISS retrieval with document metadata.
