# Use IBWD with Codex and Claude Code

IBWD is a local MCP server. One installation serves both clients; each work repository gets its own `.ibwd` index.
Default retrieval does not call a model. Optional embeddings and local assistance require explicit configuration.
Your normal agent conversations still use your existing plan allowance. No paid benchmark is needed.

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
For a visual local dashboard of the latest saved session plus comparison cohorts, run:

```bash
.venv/bin/python -m ibwd.cli usage-dashboard --repo /absolute/path/to/work-project
```

This writes `.ibwd/usage/dashboard.html` and opens it in your browser. Use `--no-open` to generate the file without opening a
window, `--client codex|claude` to choose a client, or `--session-key KEY` to focus a specific saved report.
The command refreshes the captured transcript before opening the report. You do not need to end the conversation.
For a browser window that keeps updating, add `--watch` (every five seconds; Ctrl+C stops it).
For current totals directly in the terminal, run `.venv/bin/python -m ibwd.cli usage-refresh --repo "$TARGET_REPO" --client codex`.
Automatic summaries show activity, tool failures, recorded checks, and returned evidence without manual labels.
Check results and completed replies do not independently establish task success or retrieval usefulness.
Comparison rows display model, effort, client version, sample size, provisional reports and missing usage.
The table scrolls horizontally on smaller screens; the client selector filters comparison rows.

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
Do not start extra paid sessions or benchmarks. See [usage measurement](./USAGE_MEASUREMENT.md) for evidence limits.

The remaining sections describe first installation and optional manual configuration. Existing users should prefer the single
setup command above; `client-config` and reporting-only `usage-setup` remain available for advanced use.

## 1. Install once

For optional resident embeddings, local candidate selection, summaries, handoffs and log condensation, see
[local assistance](./LOCAL_ASSISTANCE.md). It includes enable/disable commands and the MCP/CLI payload contracts.

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

Python and JS/JSX/TS/TSX production code remain the default symbol/edge scope. [Sprint 5](../development/SPRINT_5.md) adds explicit test discovery,
impact paths and optional installed TypeScript evidence. Dynamic dispatch and type-inferred receivers remain incomplete. IBWD does not replace source inspection or tests. See [known limitations](../reference/KNOWN_LIMITATIONS.md).

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
See [USAGE_MEASUREMENT.md](./USAGE_MEASUREMENT.md) for limitations, disabling hooks and optional manual comparisons.
The analyzer reads local logs; it does not start Codex, Claude Code, API calls, or benchmark runs.

### Agent evaluations on the dashboard

Score an existing case/trace pair locally:

```bash
"$IBWD_PY" -m ibwd.cli eval-agent cases.json traces.json --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli usage-dashboard --repo "$TARGET_REPO"
```

Results save automatically in `.ibwd/usage/evaluations/` and appear in the
**Agent Evaluations** section, with method scores and per-run details. Add
`--client codex --session-key "$SESSION_KEY"` to `eval-agent` to link results
to the session key shown in the dashboard; omit both for repository results.
Missing expectations or recorded outcomes remain **Not evaluated**. Ordinary
usage totals alone do not establish task correctness. See
[evaluation input formats and scoring](../development/AGENT_EVALUATION_METHODS.md#running-the-trace-evaluator).

## Sprint 3B update

After updating IBWD, rerun `python -m ibwd.cli setup --repo /absolute/path/to/work-project` with your installed environment,
then reconnect both clients so they load the updated tool schemas and shared navigation skill. Retrieval now checks and refreshes
the index automatically. Prefer `response_version=2`; use `next_cursor` with the same query for additional results.
See [Sprint 3B](../development/SPRINT_3B.md) for response budgets, compatibility and measured performance.

## Task evidence and source reads

Sprint 4 adds `ibwd_context` for unfamiliar tasks and `ibwd_read` for exact source spans checked against a current hash.
Reconnect the server after updating; rerun setup to refresh installed routing instructions. Known symbols still use the
existing exact discovery tools. See [Sprint 4](../development/SPRINT_4.md) for MCP/CLI examples, scope limits, pagination and opt-in saved-log
reduction. A `budget_tokens` value is a byte-based estimate, not a provider token count.

Context packets default to an effective 8,000-byte budget: the smaller of `4 * budget_tokens`
and `max_bytes`, including MCP serialization overhead. This keeps repository evidence from consuming
the whole agent context. Follow `next_cursor` with the same query to retrieve subsequent candidates.
Oversized first candidates now shed optional enrichment, marked by `budget_omissions` and graph/definition
truncation flags, while preserving the hash-checked `read` reference. Instruction locations remain intact.
For richer packets, pass `budget_tokens: 8000` and `max_bytes: 32768` to `ibwd_context`;
raising only one may leave the other limit active. Supported maxima are 16000 tokens and 65536 bytes.
An irreducible oversized packet reports its required bytes. Exact `ibwd_read` source spans are never
silently shortened: increase both budgets or request a smaller explicit line range.

## Optional local semantic retrieval

Sprint 6 adds an explicit `semantic-index` command for already installed weights/runtime, and `context --semantic`
(`ibwd_context` with `semantic=True`). Default work remains deterministic. See [Sprint 6 setup and limits](../development/SPRINT_6.md);
the [local M1 screen](../development/SPRINT_6_MEASUREMENT.md) failed the quality gate. No model download or evaluation is required for ordinary use.

Build or rebuild the optional local vector index explicitly when you want semantic retrieval, or after source/index
generation changes make the old semantic index stale:

```bash
"$IBWD_PY" -m ibwd.cli semantic-index \
  --repo "$TARGET_REPO" \
  --model-path "$TARGET_REPO/.ibwd/models/all-mpnet-base-v2" \
  --dimensions 768
```

Use the model path and dimensions that match the local model you already installed. `setup`, `scan`, reporting hooks and
normal `ibwd_context` requests do not build embeddings or download weights automatically. If no usable semantic index exists,
semantic requests fall back to deterministic retrieval.

After building the optional index, `ibwd semantic-config --repo /path/to/project --enabled` persists the local opt-in
for CLI/MCP context requests. Use `--disabled` to undo it, or `context --no-semantic --no-helper` for a deterministic request.

To reuse an embedding worker within each MCP server, use
`ibwd semantic-config --repo "$TARGET_REPO" --enabled --resident`. Its startup shares the index's configured query deadline;
allow enough cold-start time with `semantic-index --query-timeout 15` when building the index.

Optional local candidate selection is independent of embeddings. With an already installed local Ollama model:

```bash
"$IBWD_PY" -m ibwd.cli helper-config --repo "$TARGET_REPO" \
  --enabled --model qwen2.5-coder:7b --timeout 10
"$IBWD_PY" -m ibwd.cli context "where is session usage collected" --repo "$TARGET_REPO"
"$IBWD_PY" -m ibwd.cli helper-config --repo "$TARGET_REPO" --disabled
```

The helper returns validated existing candidate IDs; exact targets remain first and source hashes are revalidated.
It falls back on missing models, deadlines or invalid responses, with no downloads or hosted-model fallback.
Reconnect clients for `ibwd_local_assist`, which accepts cited summary/handoff payloads and bounded caller-supplied logs.
See [local assistance](./LOCAL_ASSISTANCE.md) for all payloads, limits and cache behavior.

## Reusing evidence and switching clients

Sprint 7 adds `ibwd_artifact_save(kind, payload)` and `ibwd_artifact_read(artifact_id)`.
Save small cited extracts as `summary` or an explicitly supplied task record as `handoff`.
Keep the returned ID when switching clients; both clients read the same local store and
revalidate source/dependency hashes. Stale records return status without obsolete content.
Commands in handoffs are recorded, never executed or independently certified.

The equivalent CLI is `ibwd artifact-save summary /tmp/payload.json --repo /path/to/project`
and `ibwd artifact-read ARTIFACT_ID --repo /path/to/project`. See [payload examples and limits](../development/SPRINT_7.md).
Reconnect an existing MCP server to expose the new tools. No model setup is needed.
