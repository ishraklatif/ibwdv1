# Sprint 2 — Symbol Index

**Status: shipped.**

## Sprint goal

> As Claude Code, I can ask exactly where a class/function/method is defined, so I don't grep for a common name and wade through false positives.

Second of 8 sprints in `IBWD_v1_EXECUTION_PLAN.md`. Extends the Sprint 1 MCP server (still one server, `src/ibwd/mcp/server.py`) with two new tools; the scan/graph/manifest pipeline built in Sprint 1 is unchanged in shape, just fed by a new extraction step.

**No local models needed for this sprint** — symbol extraction is pure tree-sitter static analysis (deterministic, `confidence=1.0`). The plan only requires Ollama/local models starting Sprint 5 (embeddings) and Sprint 6 (summarizer).

## What was built

### Scanners — `src/ibwd/scanner/{symbols,python,javascript}.py`
- `symbols.py`: shared `SymbolInfo` dataclass (`name`, `kind`, `qualified_name`, `file_path`, `start_line`, `end_line`) and `extract_symbols(abs_path, file_path)`, which dispatches on extension (`.py` -> Python, `.js/.jsx/.mjs/.cjs` -> JS, `.ts/.mts` -> TS, `.tsx` -> TSX) and returns `[]` for anything else (Go/Rust/etc. — out of scope this sprint, per the plan).
- `python.py`: walks the tree-sitter CST looking for `class_definition`/`function_definition` (unwrapping `decorated_definition` so a decorator's line counts as the symbol's start), tracking a class-name stack so nested defs become `Method` with a qualified name like `Class.method`, top-level ones become `Function`. Deliberately does **not** recurse into a function's body — nested/closure functions are out of scope (see the honest caveat in the demo results below).
- `javascript.py`: same idea for the JS/TS/TSX grammars (they share node type names almost entirely) — `function_declaration`/`generator_function_declaration`, `class_declaration` (recursing into `class_body` with the class-name stack), `method_definition`, and arrow-function/function-expression assignments (`variable_declarator`, and `field_definition` for class-property arrow methods like `onClick = () => {}`).
- Qualified names are `"{file_path}::{Outer.Inner}"` — globally unique across the repo, which is what makes them usable as an upsert key (see schema note below).

### Graph store — schema + upsert changes
Sprint 1's `nodes` table had a single `UNIQUE(node_type, file_path)` constraint, which only ever needed to hold one row per file (`File`, `Directory`). That assumption breaks for symbols: a file can define many `Function`/`Method` nodes sharing the same `node_type` + `file_path`. Fix, in `graph/schema.sql`:
- Replaced the table-level `UNIQUE(node_type, file_path)` with a **partial** unique index scoped to `node_type IN ('File', 'Directory')` — `upsert_node`'s `ON CONFLICT` clause had to be updated to repeat that `WHERE` (SQLite requires the conflict target to match a partial index's predicate exactly, or it errors `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint` — hit this directly during testing, see below).
- Added a second unique index on `qualified_name` (not partial — SQLite's `NULL != NULL` semantics in a UNIQUE index mean File/Directory rows, which leave `qualified_name` null, never collide with each other or with symbol rows).
- New `database.upsert_symbol_node()`, keyed on `ON CONFLICT (qualified_name)` — the symbol equivalent of Sprint 1's `upsert_node`.
- `graph/queries.py` additions: `sync_symbols(conn, file_id, file_path, symbols)` (delete-then-reinsert the file's symbol set, plus a `DEFINES` edge from the `File` node to each — `confidence=1.0, source_type=static_analysis`), `delete_symbols_for_file` (also called from `sync_files`'s existing removed-file branch, so deleting a file cleans up its symbols too — `DEFINES` edges cascade via the existing `ON DELETE CASCADE` on `edges`, but the symbol *nodes* themselves needed an explicit delete), `find_symbol(conn, name)` (exact match first, case-insensitive `LIKE` substring fallback), `list_symbols(conn, file_path)` (ordered by `start_line`).

### Orchestration — `src/ibwd/scan.py`
`run_scan` now reparses a source file's symbols only when its content hash actually changed since the last manifest (added/changed files, compared the same way Sprint 1 already tracked incrementality) — unchanged files are skipped entirely, matching the plan's "only reparse files whose content hash changed" requirement.

### MCP server — two new tools
- `ibwd_find_symbol(name)` — exact match first, falls back to case-insensitive substring; returns `{name, kind, file, line}` per match, including all matches when a name is ambiguous (e.g. two functions both named `greet` in different files).
- `ibwd_list_symbols(file)` — every symbol defined in a file, in source order.

### Guardrail — `CLAUDE.md`
Added the Sprint 2 block: prefer `ibwd_find_symbol` over `Grep` for "where is X defined," prefer `ibwd_list_symbols` over reading a whole file just to skim its structure, and a note that symbol tools currently only cover Python/JS/JSX/TS/TSX.

### Tests — `tests/`
11 new tests across 4 files (22 total now, up from 11):
- `test_scanner_python.py` / `test_scanner_javascript.py` — extractor unit tests (class/function/method kinds, qualified names, decorator line handling, JS class fields/arrow methods)
- `test_graph_symbols.py` — end-to-end via a new `symbol_repo` fixture (added to `conftest.py`, additive — doesn't touch the existing `git_repo` fixture other tests depend on) with a Python class+function file, a second Python file redefining `greet` as a top-level function, and JS/TS files. Covers: symbol extraction across languages producing correct node/edge counts, `find_symbol` returning **both** matches for the same name in different files/kinds (the exact scenario the plan's Claude Code prompt asked for), case-insensitive substring fallback, `list_symbols` ordering, reparse-only-on-change, and symbol cleanup on file deletion.
- `test_mcp_server.py` — extended with a real `mcp.call_tool` test for both new tools.

All 22 pass (`uv run python -m pytest`). Note: plain `uv run pytest`/`uv sync` picked up a stale system Python (`.venv/bin/python` symlinked to Anaconda's `python3`, which didn't have the `dev` extras installed) — worked around with `uv sync --extra dev` and `uv run python -m pytest`; not an IBWD bug, just this machine's venv state.

## What broke during the build (and the actual fixes)

1. **The Sprint 1 schema's `UNIQUE(node_type, file_path)` doesn't survive contact with symbols.** First real symbol-sync test failed with `sqlite3.IntegrityError: UNIQUE constraint failed: nodes.node_type, nodes.file_path` — `User.__init__` and `User.greet` are both `Method` nodes in the same file, which the Sprint 1 constraint (designed only for one-`File`-node-per-path) forbade. Fixed by scoping that constraint to a partial index over just `File`/`Directory`, and keying symbol upserts on `qualified_name` instead.
2. **SQLite's `ON CONFLICT` target doesn't infer partial indexes.** After switching to a partial unique index, `upsert_node`'s existing `ON CONFLICT (node_type, file_path) DO UPDATE ...` broke with `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint` — SQLite requires the `WHERE` predicate to be restated in the `ON CONFLICT` clause itself for it to match a partial index, even though there's only one plausible match. Fixed by adding `WHERE node_type IN ('File', 'Directory')` to that clause.
3. **`.ibwd/graph.db` isn't migrated, it's rebuilt.** The schema change above isn't something `CREATE TABLE IF NOT EXISTS` retrofits onto an already-created database. No migration tooling exists yet (out of scope until this becomes a real problem) — the fix was deleting this repo's own `.ibwd/graph.db` and rerunning `ibwd scan`, consistent with `CLAUDE.md`'s existing "never treat the graph as ground truth, rescan if stale" guidance.

## Definition of done — status

| Item | Status |
|---|---|
| Symbol extraction produces correct node/edge counts on a small known fixture | ✅ `test_graph_symbols.py` |
| Every symbol node has a verifiable `file:line` | ✅ — spot-checked against this repo's own graph (e.g. `run_scan` at `src/ibwd/scan.py:15`) |
| All 3 demo tasks: `ibwd_find_symbol` wins on tokens and/or precision vs. Grep baseline | ✅ 2/3 clean pass, 1/3 partial (see below) |
| Logged to `benchmarks/sprint_2_results.csv` | ✅ done |

## Demo benchmark results

Run via `benchmarks/run_sprint2_demo.py`, same methodology as Sprint 1: real, separate `claude -p` subprocess sessions per (question, condition) pair, `--strict-mcp-config` + `--allowedTools`/`--disallowedTools` to enforce baseline (`Read,Glob,Grep`) vs. IBWD (`+ ibwd_scan, ibwd_find_files, ibwd_find_symbol, ibwd_list_symbols`), run against an isolated snapshot (a plain rsync'd + freshly `git init`'d copy of the repo, not the repo being edited) so the benchmark's own output can't contaminate what the agent finds while exploring. Full transcripts and per-run summaries are in `benchmarks/raw/sprint_2/`.

| Task | Condition | Tool calls | Tokens out | Cost (USD) | Correct |
|---|---|---|---|---|---|
| Q1 "where is `connect` defined?" | baseline | 2 (`Grep`×2) | 811 | $0.2584 | ✅ |
| Q1 "where is `connect` defined?" | ibwd | **1** | 117 | $0.2441 | ✅ |
| Q2 "list every function in `scanner/python.py`" | baseline | 1 (`Read`) | 605 | $0.0572 | ✅ |
| Q2 "list every function in `scanner/python.py`" | ibwd | 1 | 169 | $0.0445 | ⚠️ partial |
| Q3 "find function named `main` — how many, where?" | baseline | 1 (`Grep`) | 879 | $0.0561 | ✅ |
| Q3 "find function named `main` — how many, where?" | ibwd | 1 | 179 | $0.0447 | ✅ |

Q1 and Q3 are a clean pass against the sprint's own bar ("fewer tokens than Grep, esp. common names") — Q3 in particular is the target case: `main` is defined 4 times in this repo, and `Grep` returned a 5th, unrelated hit (a JS fixture string in a test file containing the literal text `function main() {`) that the baseline run had to reason through and explicitly discount, burning ~5x the output tokens IBWD needed for a clean, pre-structured answer.

**Q2 is an honest partial, not a clean win — worth calling out plainly.** `src/ibwd/scanner/python.py` actually defines 4 functions, including `visit`, a closure nested inside `extract_python_symbols`. The baseline (which reads full source) found all 4. `ibwd_list_symbols` found only the 3 top-level ones — nested/closure functions are explicitly out of scope for this sprint's extractors (documented in `python.py`/`javascript.py` as a deliberate simplification, not a bug). For "where is X defined" / "how many top-level symbols does this file export" questions this is the right tradeoff (closures usually aren't things another file would ever look up), but for a literal "every function defined in this file" question, IBWD's answer is incomplete. Logged as `partial` in the CSV rather than papering over it as a pass.

**Caveats, honestly:** same as Sprint 1 — single grader (me), one repo, 3 tasks, small sample. The Q2 result is exactly the kind of thing that sample size is too small to generalize from cleanly, but it's a real, reproducible gap (not a flaky run) worth tracking if a later sprint's demo task depends on exhaustive symbol listing.

## What's still outstanding

Nothing blocking Sprint 3. Two things worth keeping in mind, not urgent:
- Nested/closure functions aren't indexed (see Q2 above) — revisit if a later sprint's call-graph work (Sprint 3) needs to resolve calls *into* closures, not just top-level symbols.
- No schema migration path exists yet — Sprint 2 needed one ad hoc (`rm -rf .ibwd && ibwd scan`). Fine at this scale; would need real handling before this ships beyond a single-developer prototype.

Next up: Sprint 3 (call graph — `ibwd_callers`/`ibwd_dependents` via `IMPORTS`/`CALLS`/`INHERITS` edges and a 5-tier resolution cascade).
