## IBWD — codebase memory

**Current owner direction (2026-09-24):** develop the existing repository for both Codex and Claude Code with no additional spending.
Keep the design device-agnostic; local models are optional and must fit measured capabilities. Prioritize correct-work token efficiency.
Do not run the paid pilot, 120-session experiment, or other billable jobs. Continue with deterministic local tests and MCP transport
checks. The frozen Sprint 3 artefacts/tag are historical evidence, not the current development configuration; the paid gate remains
untested. See `DEVELOPMENT.md` for current priorities/validation and `docs/TOKEN_EFFICIENCY_ROADMAP.md` for the active sprint plan.
`AGENTS.md` shares these instructions. Do not load the entire roadmap for unrelated coding tasks.

Use `ibwd doctor --repo PATH` to inspect index freshness without modifying it. Retrieval automatically indexes or refreshes
the repository under a shared lock. Prefer `response_version=2` for bounded envelopes, source hashes and pagination;
follow `next_cursor` with the same query when needed. A truncated result is incomplete; narrow the query or use source search
when a work limit prevents continuation. `ibwd_scan` remains available for an explicit refresh.
Discovery tools return `symbol_id` (`file::Class.method` or `file::function`); pass that exact identity to graph queries when names
are ambiguous. An ambiguous path response means no search was performed, not that no path exists.

This repo has an IBWD MCP server providing file, symbol and static-relationship queries.
Semantic retrieval and local-model summaries are planned, not shipped. Keep persistent routing guidance short.

**Use IBWD tools first for:**
- File discovery/categorization -> `ibwd_find_files` (kind: source/test/doc/config, or a name_pattern substring), instead of repeated `Glob` calls (Sprint 1)
- "Where is X defined?" -> `ibwd_find_symbol(name)` instead of `Grep`, especially for common names where grep returns noisy false positives from comments/strings/usages (Sprint 2)
- "What symbols are defined in this file?" -> `ibwd_list_symbols(file)` instead of reading the whole file just to skim its structure (Sprint 2)
- "What calls / imports X?" -> `ibwd_callers(symbol, depth)`; "what does X call / depend on?" -> `ibwd_dependents(symbol, depth)`, instead of manually Grep-tracing call sites (Sprint 3). Rendering a React component (`<Card />`) counts as a call, and passing a function as a value (`useReducer(fn)`, `component={Screen}`) shows up with relation `REFERENCES`, and `tsconfig`/`jsconfig` `paths` aliases (`@/...`) are resolved. Each result has a `confidence` (0.95 import-resolved ... 0.35 fuzzy; multi-hop = product) — treat low values as leads to verify, not facts. A file path works as `symbol` (callers = its importers)
- "How does A reach B?" / "is A connected to B?" -> `ibwd_trace_path(source, target)` instead of calling `ibwd_callers`/`ibwd_dependents` at increasing depth; "no path found" is a real answer (Sprint 3)
- Symbol and call-graph tools currently cover Python and JS/JSX/TS/TSX only; other languages fall through to Grep. The call graph covers source files only (not test files) and skips calls into external packages

Default graph queries follow resolved edges only. Unique-name/suffix candidates are opt-in; fuzzy resolution is disabled by default.
Confidence values and their multi-hop products are heuristic scores, not calibrated probabilities.

**Read `KNOWN_LIMITATIONS.md` before trusting a graph answer.** Key rule: an empty result means only
"no matching resolved edges in the indexed production graph" — other uses may exist (dynamic dispatch, framework entry points,
type-inferred receivers), so it is never proof that a function is unused or safe to delete —
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


<!-- ibwd:routing:start -->
## IBWD repository navigation

Use IBWD first for supported repository navigation, including navigation needed
for implementation, debugging and UI work:
- File discovery: ibwd_find_files; definitions: ibwd_find_symbol.
- File structure: ibwd_list_symbols; callers/importers: ibwd_callers.
- Dependencies: ibwd_dependents; connections: ibwd_trace_path.
- Unfamiliar task: ibwd_context; exact source: ibwd_read with the returned hash/range.
- Before applicable coding, debugging, or UI work, use at least one relevant IBWD lookup before searching/editing.
  Use the smallest useful lookup; do not call every tool. If unavailable or outside indexed scope, say so and search normally.
Retrieval checks freshness and refreshes automatically, including after edits or branch switches.
Use response_version=2 for bounded results, source hashes and generation-bound pagination.
Follow next_cursor with the same query when more evidence is needed; truncated results are incomplete.
ibwd_scan remains available for an explicit refresh; concurrent operations are serialized.
Use exact symbol_id values from discovery when names are ambiguous.
Read source before editing. Use text search for exact text,
runtime behavior and exhaustive searches. Symbols and edges cover production
Python and JS/JSX/TS/TSX; lexical context also covers tests/docs/config, while dynamic dispatch and inferred receivers remain incomplete.
An empty graph result never proves no uses or that deletion is safe.
If IBWD tools are unavailable or fail, say so briefly and use ordinary search;
do not silently claim IBWD was used. Do not make unrelated calls just to raise usage.
No paid model jobs or benchmarks. Normal work and local deterministic checks only.
<!-- ibwd:routing:end -->
