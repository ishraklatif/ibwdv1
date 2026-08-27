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
| All 3 demo tasks answered correctly via `ibwd_find_files` with fewer tool calls than Glob-only baseline | ⏳ **not yet run** — needs a fresh Claude Code session (see below) |
| `benchmarks/sprint_1_results.csv` exists with the logged comparison | ⏳ **not yet created** — depends on the item above |

## What's still outstanding

Sprint 1's build and unit-level verification are done, but the formal **Appendix D demo protocol** — asking the same 3 questions ("list all test files," "which files are config," "how many Python source files under `src/`") in a fresh Claude Code session with and without `ibwd_find_files`, and logging tokens/tool-calls/correctness — hasn't been run. It requires a session started *after* MCP registration (this build happened across a session that predates registration), so it can't be faked from inside the build itself. That's the next step before moving to Sprint 2.
