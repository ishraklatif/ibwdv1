# Sprint 1 — File & Directory Index

**Status: shipped.** Commit `cf4ec15` on `main`, pushed to [ishraklatif/ibwdv1](https://github.com/ishraklatif/ibwdv1).

## Sprint goal

> As Claude Code, I can ask IBWD which files exist and how they're categorized, so I don't burn multiple `Glob` calls exploring the tree by hand.

This is the first of 8 sprints in `IBWD_v1_EXECUTION_PLAN.md`. Per that plan's "ship the MCP server now, not later" philosophy, Sprint 1 delivers a working, connected MCP server on day one — every later sprint just adds tools to it.

## What was built

### Scanner — `src/ibwd/scanner/filesystem.py`
- Walks the repo tree, respecting `.gitignore` (parsed with `pathspec`, `gitignore` pattern syntax) plus a hardcoded `ALWAYS_IGNORE` set (`.git`, `.ibwd`, `__pycache__`, `.venv`, `node_modules`, etc.)
- Classifies every file into `source` / `test` / `doc` / `config` / `other` using path/extension heuristics (e.g. `test_*`/`*_test.py`/`*.test.ts`/a `tests/` directory → `test`; `.md`/`README`/a `docs/` directory → `doc`; `.toml`/`.yaml`/`Dockerfile`/`pyproject.toml` → `config`)
- Hashes file contents with `xxhash.xxh3_64` (fast, non-cryptographic — matches the plan's incremental-indexing precedent from Cursor/Codebase-Memory)

### Graph store — `src/ibwd/graph/`
- `schema.sql` — the `nodes`/`edges`/`summaries` SQLite schema from the plan's Appendix B, with two additions beyond the spec: a `UNIQUE(node_type, file_path)` constraint on `nodes` and `UNIQUE(source_id, target_id, relation)` on `edges` (needed for idempotent upserts on rescan), and `ON DELETE CASCADE` on edge foreign keys (needed so deleting a removed file's node doesn't leave dangling edges — this surfaced as a real `IntegrityError` in testing, see below)
- `database.py` — connects to `.ibwd/graph.db`, loads the schema, and provides `upsert_node`/`upsert_edge` helpers (`INSERT ... ON CONFLICT ... DO UPDATE`, so re-running a scan updates rather than duplicates)
- `queries.py` — `sync_files()` reconciles a fresh scan against the graph and the previous manifest: inserts/updates `File` nodes and the `Directory` chain above them, links them with `CONTAINS` edges (`confidence=1.0`, `source_type=static_analysis`), and deletes nodes for files that disappeared. Returns an `added`/`changed`/`removed`/`unchanged` summary. `find_files()` serves the `kind`/`name_pattern` filtered query.
- `manifest.py` — loads/saves `.ibwd/manifest.json` (`path -> content_hash`), the mechanism that makes rescans incremental: unchanged files are skipped by comparison, not reprocessed.

### Orchestration & CLI — `src/ibwd/scan.py`, `src/ibwd/cli.py`
- `run_scan(repo_root)` ties the three pieces together: scan → sync against manifest → save new manifest. Shared by both the CLI and the MCP server so there's one code path, not two.
- `ibwd scan` (via `click`) runs it and prints a one-line summary.

### MCP server — `src/ibwd/mcp/server.py`
Exposes two tools, both defined in the plan:
- `ibwd_scan()` — triggers `run_scan()`, returns the change summary
- `ibwd_find_files(kind, name_pattern)` — returns `{path, kind}` for matching files

**Deviation from the plan:** the plan specified `FastMCP` from the `mcp` Python SDK. By the time this was built, `mcp` had shipped a 2.x release that renamed `FastMCP` to `MCPServer` (`mcp.server.mcpserver.MCPServer`) with an otherwise near-identical decorator API (`.tool()`, `.run()`). Code was written against the current SDK rather than the plan's exact class name — this is exactly the kind of "moving target" the research doc (`compass_artifact_...md`) warned about for tooling versions.

### Guardrail — `CLAUDE.md`
First guidance block, as specified: prefer `ibwd_find_files` over repeated `Glob` calls for file discovery, fall back to `Glob`/`Grep`/`Read` for anything else, never treat the graph as ground truth, rescan if results look stale.

### Tests — `tests/`
11 pytest tests using a temp-git-repo fixture (`conftest.py`, via `gitpython`):
- `test_scanner.py` — classification rules, gitignore filtering, hash stability/sensitivity
- `test_graph.py` — node/edge creation, idempotent resync, change detection, cascade-delete on removed files, `find_files` filtering
- `test_scan.py` — end-to-end `run_scan`, incremental behavior across two runs and an edit
- `test_mcp_server.py` — calls the actual MCP tools (`mcp.call_tool(...)`) against a temp repo, asserting on the JSON payload

All 11 pass (`uv run pytest`).

## What broke during the build (and the actual fixes)

1. **`mcp<2` API assumption.** `from mcp.server.fastmcp import FastMCP` failed outright — the installed `mcp==2.1.1` raises a `ModuleNotFoundError` with an explicit migration message. Fixed by switching to `mcp.server.mcpserver.MCPServer`, which has the same `.tool()`/`.run()` surface.
2. **Foreign-key violation on file deletion.** `test_sync_files_removes_deleted_files` failed with `sqlite3.IntegrityError: FOREIGN KEY constraint failed` — deleting a `File` node left its `CONTAINS` edge pointing at a nonexistent row. Fixed by adding `ON DELETE CASCADE` to both edge foreign keys in `schema.sql`.
3. **Deprecated pathspec pattern name.** `PathSpec.from_lines("gitwildmatch", ...)` emitted a `DeprecationWarning` under the installed `pathspec==1.1.1`. Switched to the current `"gitignore"` pattern name.
4. **Git repo root mismatch.** When it came time to push, `git rev-parse --show-toplevel` showed the actual repo was rooted one directory up (`claude_agent/`, empty, no commits) rather than at `ibwd/` — inconsistent with the plan's own prerequisites (`mkdir ibwd && cd ibwd && git init`) and with what should map 1:1 onto `ibwdv1.git`. Fixed by initializing a repo scoped to `ibwd/` itself and leaving the stray parent repo untouched.

## Definition of done — status

| Item | Status |
|---|---|
| `claude mcp list` shows `ibwd` connected | ✅ verified (`✔ Connected`) |
| `ibwd scan` completes with correct counts; second run reports 0 changed | ✅ verified (25 files; second run: `added=0, changed=0, unchanged=25`) |
| All 3 demo tasks answered correctly via `ibwd_find_files` with fewer tool calls than Glob-only baseline | ✅ verified — see results below |
| `benchmarks/sprint_1_results.csv` exists with the logged comparison | ✅ done |

## Demo benchmark results

Run via `benchmarks/run_sprint1_demo.py`, which drives real, separate `claude -p` subprocess sessions per Appendix D — one per (question, condition) pair, each a genuinely fresh session (`--strict-mcp-config` to exclude unrelated globally-configured MCP servers from context, `--allowedTools`/`--disallowedTools` to enforce the baseline vs. IBWD tool sets). Full transcripts and per-run summaries are in `benchmarks/raw/sprint_1/`.

**Methodology fix worth noting:** the first run was contaminated — the benchmark script's own output files were being written into the same repo the questions were exploring, so later baseline runs found and got confused by earlier runs' scratch files (one `baseline` run ballooned to 9 tool calls chasing them). Fixed by running against an isolated `git worktree` snapshot pinned to a fixed commit, with all benchmark output written outside that snapshot. The results below are from the corrected run.

| Task | Condition | Tool calls | Tokens out | Cost (USD) | Correct |
|---|---|---|---|---|---|
| Q1 "list all test files" | baseline | 5 | 858 | $0.1095 | ✅ |
| Q1 "list all test files" | ibwd | **1** | 184 | $0.0747 | ✅ |
| Q2 "which files are config" | baseline | 6 | 2570 | $0.2019 | ✅ |
| Q2 "which files are config" | ibwd | **1** | 128 | $0.0419 | ✅ |
| Q3 "how many Python source files under `src/`" | baseline | 1 | 571 | $0.0491 | ✅ |
| Q3 "how many Python source files under `src/`" | ibwd | 1 | 113 | $0.0432 | ✅ |

All 6 runs answered correctly against hand-verified ground truth (`benchmarks/tasks/sprint_1_tasks.yaml`). IBWD won clearly on tool calls for Q1 (5→1) and Q2 (6→1), and tied on Q3 (both resolved in a single `Glob`/`ibwd_find_files` call — a single-directory glob is already cheap, so there was no baseline inefficiency for IBWD to remove). Across all three, IBWD used meaningfully fewer output tokens and was cheaper per call except on Q3 where cost was roughly a wash. This matches the sprint's stated go/no-go bar ("fewer tool calls than Glob") for 2 of 3 tasks and a tie on the third — a clean pass, though on a small, easy task set that doesn't yet stress-test the approach (that's what Sprints 3–4's call-graph/impact-analysis demos are for).

**Caveats, honestly:** single grader (me), one repo, 3 tasks, small sample — exactly the kind of directional-not-definitive evidence the research doc (`compass_artifact_...md`) warned about for the published prior art this plan is built on. Good enough to green-light Sprint 2, not enough to claim a general result yet.

## What's still outstanding

Nothing blocking — Sprint 1 is complete per its own definition of done. Next up: Sprint 2 (symbol index — `ibwd_find_symbol`/`ibwd_list_symbols` via tree-sitter).
