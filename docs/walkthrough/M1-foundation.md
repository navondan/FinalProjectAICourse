# M1 · Foundation

## What was built

The foundation now loads all 102 Markdown documents, parses YAML front matter, and chunks text
using the measured 320/80 word/overlap defaults. Audience metadata is preserved per chunk. A
local FAISS inner-product index uses Bedrock Titan embeddings, while a BM25 scorer supplies the
lexical branch; reciprocal-rank fusion combines both rankings. Index metadata records the
embedding model so a mismatched model cannot silently query an incompatible index.

SQLite is now a file-backed database seeded once from the supplied `schema.sql` and `seed.sql`.
The MCP server exposes the supplied operational read/write surface plus the two missing tools:
`search_hr_documents` and `list_onboarding_tasks`. Manager-only documents are filtered in the
retrieval layer; manager status is computed from whether an employee's `manager_id` points to
the caller, not from their user group.

Bedrock model calls are centralized in `core/model.py`. The `ask` CLI retrieves evidence and
prints source paths; the MCP server is a separate HTTP process on `127.0.0.1:9876`.
The MCP server process also needs access to AWS credentials because it embeds each retrieval
query. Configure the AWS profile or credential chain in the shell that starts the server, and
restart it after changing credentials.

## Pipeline

```text
documents/**/*.md
        |
        v
front matter + 320/80 chunking
        |
        +----> audience metadata -----------------------------+
        |                                                     |
        v                                                     v
Bedrock embeddings -> local FAISS                    permission filter
        |                                                     |
        +----> BM25 lexical ranking --------------------------+
                              |
                              v
                   reciprocal-rank fusion
                              |
                              v
                  cited answer / MCP result

schema.sql + seed.sql -> persistent SQLite -> MCP database tools
```

## Five files to know

| File | Why it matters |
| --- | --- |
| `novaops/retrieval/ingest.py` | Front matter, document loading, and word-overlap chunking |
| `novaops/retrieval/store.py` | FAISS, BM25, rank fusion, persistence, and audience filtering |
| `novaops/core/database.py` | One-time fixture seeding and durable SQLite queries/writes |
| `novaops/mcp_server/server.py` | Registers the MCP boundary and all foundation tools |
| `novaops/core/model.py` | The single Bedrock Converse call boundary |

## Verify

With an AWS profile that can invoke the configured Bedrock models:

```powershell
.\.venv\Scripts\python -m novaops.cli ingest
.\.venv\Scripts\python -m novaops.cli ask "What's the laptop policy for remote employees?"
.\.venv\Scripts\python -m pytest
```

Expected: the answer cites a real file under `documents/`; the test suite also checks corpus
coverage, permissions, persistence, and the MCP tool inventory without making AWS calls.

## What I'd ask in a viva

1. **Why filter by audience in the retrieval layer?** Restricted text must never reach the
   model, so filtering after generation or relying on prompt instructions is too late.
2. **Why seed SQLite only when it has no tables?** Re-running fixture initialization must not
   erase operational writes or silently repair a partially migrated database.
3. **Why combine BM25 and FAISS?** Exact policy terms and identifiers benefit from lexical
   matching, while vector similarity helps with paraphrased questions; reciprocal-rank fusion
   combines their ordering without making their raw scores comparable.
