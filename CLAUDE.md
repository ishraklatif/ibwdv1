## IBWD — codebase memory

This repo has an IBWD MCP server providing structural + semantic queries
over the codebase (tree-sitter-derived facts + local-LLM-inferred summaries,
added in later sprints). It's built incrementally — this file grows one
block per sprint.

**Use IBWD tools first for:**
- File discovery/categorization -> `ibwd_find_files` (kind: source/test/doc/config, or a name_pattern substring), instead of repeated `Glob` calls (Sprint 1)
- "Where is X defined?" -> `ibwd_find_symbol(name)` instead of `Grep`, especially for common names where grep returns noisy false positives from comments/strings/usages (Sprint 2)
- "What symbols are defined in this file?" -> `ibwd_list_symbols(file)` instead of reading the whole file just to skim its structure (Sprint 2)
- "What calls / imports X?" -> `ibwd_callers(symbol, depth)`; "what does X call / depend on?" -> `ibwd_dependents(symbol, depth)`, instead of manually Grep-tracing call sites (Sprint 3). Rendering a React component (`<Card />`) counts as a call, and passing a function as a value (`useReducer(fn)`, `component={Screen}`) shows up with relation `REFERENCES`, and `tsconfig`/`jsconfig` `paths` aliases (`@/...`) are resolved. Each result has a `confidence` (0.95 import-resolved ... 0.35 fuzzy; multi-hop = product) — treat low values as leads to verify, not facts. A file path works as `symbol` (callers = its importers)
- "How does A reach B?" / "is A connected to B?" -> `ibwd_trace_path(source, target)` instead of calling `ibwd_callers`/`ibwd_dependents` at increasing depth; "no path found" is a real answer (Sprint 3)
- Symbol and call-graph tools currently cover Python and JS/JSX/TS/TSX only; other languages fall through to Grep. The call graph covers source files only (not test files) and skips calls into external packages

**Read `KNOWN_LIMITATIONS.md` before trusting a graph answer.** Key rule: "no callers" is not proof a
function is safe to delete (dynamic dispatch and framework entry points have no static caller) —
check the code before deleting.

**Fall back to Glob/Grep/Read for:**
- Reading full file contents or exact source needed to make an edit
- Anything not yet covered by an IBWD tool (most query types — this file
  will list more as later sprints ship)

**Always:**
- Run `ibwd_scan` (or `/graphify` once it exists) if results look stale
  relative to recent changes
- Never treat the graph as ground truth — it is a map to speed up
  navigation, not a replacement for reading the code you're about to change
