---
name: ibwd-navigation
description: Locate unfamiliar repository code and trace definitions, callers, dependencies or paths with IBWD during implementation and debugging. Skip when the required source and edit location are already known.
---

<!-- ibwd:managed-skill:v1 -->

# IBWD navigation

Use the smallest supported lookup needed for the user's task. Respect the user's instructions and project restrictions;
this skill does not authorize new actions or override conflicting instructions.

When navigation is needed, use the available IBWD MCP tools:

- Files by category or path substring: `ibwd_find_files`.
- Definitions: `ibwd_find_symbol`; file outline: `ibwd_list_symbols`.
- Incoming calls/imports/references: `ibwd_callers`; outgoing dependencies: `ibwd_dependents`.
- A connection between two endpoints: `ibwd_trace_path`.

Run `ibwd_scan` before the first structural query in a session and after relevant edits or a branch switch.
Avoid concurrent scans. Reuse exact `symbol_id` values when names are ambiguous; do not repeatedly search the same unchanged evidence.
Start graph queries at depth 1 unless the task needs a deeper relationship.

Read the exact source needed for an edit and verify the result with appropriate checks. Current graph coverage is production
Python and JS/JSX/TS/TSX; tests, dynamic dispatch and type-inferred receivers are incomplete. Confidence is heuristic.
An empty result never establishes no uses or safe deletion. An ambiguous path response means no search was performed.

For exact-text/exhaustive searches or unsupported scope, use ordinary source search. If IBWD is unavailable or errors,
briefly explain the fallback and continue with available tools. Do not claim an attempted call succeeded or count a scan as retrieval.
Do not call tools just to raise usage counts. No model jobs, benchmark sessions or local-model downloads are part of this skill.
Automatic reporting runs outside the conversation; do not spend a model turn analyzing the reports after each task.
