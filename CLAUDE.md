## IBWD — codebase memory

**Current owner direction (2026-09-24):** develop the existing repository for both Codex and Claude Code with no additional spending.
Keep the design device-agnostic; local models are optional and must fit measured capabilities. Prioritize correct-work token efficiency.
Do not run the paid pilot, 120-session experiment, or other billable jobs. Continue with deterministic local tests and MCP transport
checks. The frozen Sprint 3 artefacts/tag are historical evidence, not the current development configuration; the paid gate remains
untested. See `DEVELOPMENT.md` for current priorities/validation and `docs/TOKEN_EFFICIENCY_ROADMAP.md` for the active sprint plan.
`AGENTS.md` shares these instructions. Do not load the entire roadmap for unrelated coding tasks.

Use `ibwd doctor --repo PATH` to inspect index freshness without modifying it. Scan before querying an unindexed repository.
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
