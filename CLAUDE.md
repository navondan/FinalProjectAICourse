# Project conventions

- Follow `BUILD-PLAN.md` and complete each milestone's verification before moving on.
- Read the `Status` section of `BUILD-PLAN.md` at the start of every working session and update
  it (state, test count, next action, deviations, risks) before ending one.
- Keep Maya and Webex on one shared graph with workflow-specific scopes and tool visibility.
- Keep model calls in `novaops/core/model.py`; tool calls cross the MCP server boundary.
- Treat caller identity and authorization as application inputs, never model inferences.
- Enforce document permissions during retrieval, before content reaches a model.
- Release writes only after checking a persisted approval record; never trust a chat flag or
  request-carried approval token.
- Make database writes idempotent and preserve pending workflows across process restarts.
- Use the supplied dataset as the source of truth. Cite retrieved documents and do not invent
  operational facts.
- Pin date-sensitive behavior to `NOVAOPS_AS_OF_DATE`, not the system clock.
- Keep secrets in `.env` or the standard AWS credential chain. Never commit credentials,
  `.env`, or generated database/index files.
- Add focused tests for behavior changes and run `pytest` before completing a milestone.
- Document each completed milestone in `docs/walkthrough/`.
