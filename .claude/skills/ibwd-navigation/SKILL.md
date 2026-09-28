---
name: ibwd-navigation
description: Use IBWD tools to navigate repositories before applicable coding, debugging, and UI work; assemble task context and read exact source.
---

<!-- ibwd:managed-skill:v1 -->

# IBWD navigation

For repository coding, debugging, or UI tasks, use at least one applicable IBWD lookup before searching or editing when the tools are available. Respect the user's instructions and project restrictions;
this skill does not authorize new actions or override conflicting instructions.

Choose the smallest useful lookup; do not call every tool by default:

- Files by category or path substring: `ibwd_find_files`.
- Definitions: `ibwd_find_symbol`; file outline: `ibwd_list_symbols`.
- Incoming calls/imports/references: `ibwd_callers`; outgoing dependencies: `ibwd_dependents`.
- Change exposure/test relevance: `ibwd_impact`; select direction, relations and source/test scopes. References are not test coverage.
- Test definitions: `ibwd_find_symbol`/`ibwd_list_symbols` with `scope="test"` and `response_version=2`.
- Optional installed TypeScript evidence: `ibwd_compiler_evidence`; preserve diagnostics and possible-target labels.
- A connection between two endpoints: `ibwd_trace_path`.
- Unfamiliar task: `ibwd_context` with the task and known targets; select relevant source/test/doc/config scopes.
- Exact source before editing: `ibwd_read` with the `expected_hash` and range returned by discovery/context.
- A known target can go directly to its matching lookup; use callers/dependents/path only when relationships matter.

If IBWD is unavailable or the request falls outside indexed scope, say so briefly and use ordinary search. A user request may override this routing rule.

Retrieval checks freshness and refreshes automatically, including after edits and branch switches.
Use `response_version=2` for bounded responses. Follow `next_cursor` with the same query when more evidence is needed;
if a work limit prevents pagination, narrow the query or use source search. A truncated result is incomplete.
Use `ibwd_scan` only for an explicit refresh. Reuse exact `symbol_id` values when names are ambiguous; do not repeatedly search the same unchanged evidence.
Start graph queries at depth 1 unless the task needs a deeper relationship.

Read the exact source needed for an edit and verify the result with appropriate checks. Default graph coverage is production
Python and JS/JSX/TS/TSX; impact adds a separate test scope. Dynamic dispatch and type-inferred receivers remain incomplete. Confidence is heuristic.
An empty result never establishes no uses or safe deletion. An ambiguous path response means no search was performed.

For exact-text/exhaustive searches or unsupported scope, use ordinary source search. If IBWD errors,
briefly explain the fallback and continue with available tools. Do not claim an attempted call succeeded or count a scan as retrieval.
Do not call tools just to raise usage counts. No model jobs, benchmark sessions or local-model downloads are part of this skill.
Automatic reporting runs outside the conversation; do not spend a model turn analyzing the reports after each task.
