# Use IBWD with Codex and Claude Code

IBWD is a local MCP server. One installation serves both clients; each work repository gets its own `.ibwd` index.
It does not call a model. Your normal agent conversations still use your existing plan allowance. No paid benchmark is needed.

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
Use IBWD for file/symbol discovery, callers, dependencies and call paths.
Scan before the first query and rescan after relevant edits. Use exact symbol_id
values to disambiguate definitions. Read source before editing. Use text search
for unsupported languages, runtime behavior and exhaustive searches. Empty graph
results are scoped, never proof of no uses. Do not run paid benchmarks.
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

Follow [USAGE_MEASUREMENT.md](USAGE_MEASUREMENT.md) after finishing a normal work session. The included analyzer reads local logs;
it does not start Codex, Claude Code, API calls, or benchmark runs.
