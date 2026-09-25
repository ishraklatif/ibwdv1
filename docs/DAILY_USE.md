# Use IBWD with Codex and Claude Code

IBWD is a local MCP server. One installation serves both clients; each work repository gets its own `.ibwd` index.
It does not call a model. Your normal agent conversations still use your existing plan allowance. No paid benchmark is needed.

## Recommended: one project setup for both clients

From your existing IBWD checkout, run once per work project:

```bash
.venv/bin/python -m ibwd.cli setup --repo /absolute/path/to/work-project
```

This configures both MCP clients, adds marked IBWD routing sections to `AGENTS.md` and `CLAUDE.md`, enables automatic reports,
refreshes the index, and checks local readiness. If root `AGENTS.override.md` exists, it receives Codex's routing section instead.
Other instructions/settings are preserved. The MCP initialization response also carries the routing guidance.

Reconnect both clients and check IBWD in `/mcp`. In Codex, review and trust the hooks once in `/hooks`; complete the clients'
normal project/MCP approval prompts. IBWD does not grant trust or bypass policies.

Thereafter work normally. Retrieval refreshes automatically; agents use IBWD
first for supported navigation (including implementation/UI work), and explain an unavailable/failed fallback. Read automatic
reports at `.ibwd/usage/latest-codex.md` and `.ibwd/usage/latest-claude.md`; no per-session command is needed.

Use `--dry-run` to validate/list proposed file changes without writing/scanning. Use `--client codex` or `--client claude` for one
client. Repeated setup does not duplicate routing sections or identical hooks. Existing changed files receive a first-install backup
under `.ibwd/setup-backups/`. Conflicting existing IBWD server command/args, malformed configurations/markers, or symlinked targets
stop preflight before writes. Reconcile conflicting server configuration deliberately. Writes are individually atomic, not a
transaction across all files and the scan; fix any reported I/O/scan failure and rerun.

Existing disabled-server/hook settings remain disabled and appear in readiness problems. Machine-specific settings, backups and
the index receive `.gitignore` entries; already tracked files remain tracked. Routing sections have no machine-specific paths.

For troubleshooting only, one read-only command checks configuration, instructions, hooks and freshness together:

```bash
.venv/bin/python -m ibwd.cli doctor --setup --repo /absolute/path/to/work-project
```

Local readiness does not prove client connection or adoption. Global policies, trust and nested instruction overrides are outside
this check. Verify selection during the next ordinary coding task: look for a supported navigation call or an explained fallback.
Do not start extra paid sessions or benchmarks. See [usage measurement](USAGE_MEASUREMENT.md) for evidence limits.

The remaining sections describe first installation and optional manual configuration. Existing users should prefer the single
setup command above; `client-config` and reporting-only `usage-setup` remain available for advanced use.

## 1. Install once

```bash
git clone https://github.com/ishraklatif/ibwdv1.git
cd ibwdv1
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

For the existing checkout, keep using its `.venv`; do not clone or reinstall unnecessarily. Use Python 3.11+.
Set these two paths in your terminal (replace the example values):

```bash
IBWD_PY="/absolute/path/to/ibwdv1/.venv/bin/python"
TARGET_REPO="/absolute/path/to/your/work-project"
"$IBWD_PY" -m ibwd.cli scan --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli doctor --repo "$TARGET_REPO"
```

Add `.ibwd/` to the **work project's** `.gitignore`. The index and usage reports are local derived data.

## 2. Connect each client

Generate both configurations with the same target repository:

```bash
"$IBWD_PY" -m ibwd.cli client-config --client codex --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli client-config --client claude --repo "$TARGET_REPO"
```

These commands print configuration; they do not change client settings.

| Client | Where to merge the output | Verify after reconnecting |
|---|---|---|
| Codex | `[mcp_servers.ibwd]` in the work project's `.codex/config.toml` (trusted project) | Open `/mcp` and check that IBWD is connected |
| Claude Code | `mcpServers.ibwd` inside the work project's `.mcp.json` | Open `/mcp` and check that IBWD is connected |

Preserve existing settings/servers when merging. Restart or reconnect the client and complete its normal trust/approval flow.
Generated paths are machine-specific: keep these configurations local unless your team deliberately shares the same paths.
For multiple work repositories, use project-specific configuration so one project's server is never accidentally used for another.

Codex configuration details: [official MCP documentation](https://developers.openai.com/codex/mcp).
Claude Code configuration details: [official MCP documentation](https://code.claude.com/docs/en/mcp).

## 3. Give both agents the same short routing guidance

Append this to existing `AGENTS.md` (Codex) and `CLAUDE.md` (Claude Code) in your work project. Do not replace your project instructions.

```text
Use IBWD first for file/symbol discovery, callers, dependencies and call paths,
including navigation during implementation, debugging and UI tasks.
Scan before the first query and rescan after relevant edits. Use exact symbol_id
values to disambiguate definitions. Read source before editing. Use text search
for unsupported languages, runtime behavior and exhaustive searches. Empty graph
results are scoped, never proof of no uses. If tools are unavailable or fail,
explain the fallback briefly. Do not run paid benchmarks.
```

Python and JS/JSX/TS/TSX production code are the current symbol/edge scope. Test files, dynamic dispatch and type-inferred receivers
are not fully represented. IBWD does not replace source inspection or tests. See [known limitations](../KNOWN_LIMITATIONS.md).

## 4. Work normally

Use IBWD during an actual task, such as locating the callers of a function you need to change. Do not create demonstration model
sessions just to exercise it. `/mcp` and local index diagnostics can check the connection before your next ordinary task.

After substantial edits or a branch switch:

```bash
"$IBWD_PY" -m ibwd.cli scan --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli doctor --repo "$TARGET_REPO"
```

Scans are explicit, not automatic background monitoring. Avoid simultaneous scans from two clients; finish one scan before the next.
Both clients can then query the same index. Regenerate configuration if you move IBWD's environment or the work repository.

## 5. Measure existing work

Enable automatic reports once per work project:

```bash
"$IBWD_PY" -m ibwd.cli usage-setup --repo "$TARGET_REPO"
```

Reconnect the clients; in Codex, review and trust the new hooks in `/hooks` once. Thereafter reports update after replies and at
normal session end. Open `.ibwd/usage/latest-codex.md` or `.ibwd/usage/latest-claude.md`; no per-session command is needed.
This configures reporting only, so complete the MCP connection and routing steps above as well.
See [USAGE_MEASUREMENT.md](USAGE_MEASUREMENT.md) for limitations, disabling hooks and optional manual comparisons.
The analyzer reads local logs; it does not start Codex, Claude Code, API calls, or benchmark runs.

## Sprint 3B update

After updating IBWD, rerun `python -m ibwd.cli setup --repo /absolute/path/to/work-project` with your installed environment,
then reconnect both clients so they load the updated tool schemas and shared navigation skill. Retrieval now checks and refreshes
the index automatically. Prefer `response_version=2`; use `next_cursor` with the same query for additional results.
See [Sprint 3B](SPRINT_3B.md) for response budgets, compatibility and measured performance.

## Task evidence and source reads

Sprint 4 adds `ibwd_context` for unfamiliar tasks and `ibwd_read` for exact source spans checked against a current hash.
Reconnect the server after updating; rerun setup to refresh installed routing instructions. Known symbols still use the
existing exact discovery tools. See [Sprint 4](SPRINT_4.md) for MCP/CLI examples, scope limits, pagination and opt-in saved-log
reduction. A `budget_tokens` value is a byte-based estimate, not a provider token count.
