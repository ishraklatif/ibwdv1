# Sprint 3A — adoption instrumentation

Implemented for Codex and Claude Code without model calls. Live model adoption, causal token savings and the historical paid
Sprint 3 gate remain unverified. The next normal needed task in each client is the appropriate adoption check.

## Activate on each device/project

Update your existing IBWD checkout/install first. From that checkout, with its environment activated:

```sh
python -m ibwd.cli setup --repo /absolute/path/to/work-project
```

Use that device's actual project path; do not copy another laptop's generated configuration. Setup preserves unrelated
settings/instructions, backs up changed existing files and refuses conflicting user-owned skills. Restart both clients,
review/trust project configuration and hooks in the host. No reinstall of dependencies is required for an existing editable
installation. A non-editable installation must be updated from the changed source first. Hooks support macOS/Linux/WSL POSIX;
native Windows hooks remain unsupported.

One packaged skill source installs to `.agents/skills/ibwd-navigation/SKILL.md` (Codex) and
`.claude/skills/ibwd-navigation/SKILL.md` (Claude Code). Its description scopes invocation to navigation; it respects user/project
restrictions, uses only the existing seven tools, verifies source and explains fallback. Skills guide but do not guarantee
model behavior. There is no need to add “use IBWD” to every prompt after the client loads the setup.

## Automatic local evidence

- `latest-codex.md`, `latest-claude.md` and `sessions/` under `.ibwd/usage/` retain session snapshots.
- `comparison.md` separates client/model/effort/version/task/condition cohorts, shows missing data and unattributed server requests.
  These are descriptive totals, not matched experiments or savings estimates.
- Report schema 2 includes `configured`, `connection_observed`, `scan_calls`, `retrieval_calls`, `retrieval_errors`,
  `fallback_observed`, `attribution_unknown`, `usage_incomplete`, each with an evidence source. Unobserved facts stay `unknown`.
  Call counts are observed lower bounds. Configuration is a disk snapshot, not proof the host loaded it.
- Codex `response_item` calls and structured `event_msg/mcp_tool_call_begin/end` are deduplicated by call ID; retries with new IDs
  remain separate. Claude tool-use/result records are joined by tool-use ID. Exact shared server observation IDs deduplicate wrappers.
  Mere mentions and executable source strings are never interpreted as invocations.
- The optional version-1 `ibwd_observation` fallback adapter event is accepted only with explicit `kind: fallback` and
  `reason: unavailable|error|unsupported`. Current native clients need not emit it; ordinary prose never establishes fallback.
- Child-marked records and child hook events are excluded. Observed child activity flags uncertain parent accounting.
  No independent child total or parent-child attribution is invented; disjoint child accounting is not available in this adapter.

`events.sqlite3` retains at most 10,000 invocation/completion rows. It records pseudonymous request/client IDs, random observation
and connection IDs, process ID, tool, status, duration and available response metrics, never arguments, error text or source.
Successful SDK 2 responses carry `_meta.ibwd.observation_id`; only that exact ID joins a session to the ledger. Hosts may omit it.
Connection identity or nearby timestamps never join conversations. SDK errors generated outside the wrapper have no receipt or
response byte count. Missing client identity, result cardinality, truncation and index generation remain null. Index publication
generations are Sprint 3B work. Byte counts describe serialized tool-result JSON, not the enclosing JSON-RPC frame or tokens.
Ledger write failures do not fail retrieval. This is local telemetry, not a remote collector.

## Incremental parsing and privacy

Hooks serialize publication with a local advisory lock. Atomic report writes precede checkpoint advancement, so interrupted
publication can replay safely. `state/` stores counters, pseudonymous IDs and complete-line offsets, not conversation contents.
Incomplete trailing lines are reread next time. Oversized records over 8 MiB are discarded with an incomplete-data warning.
Parsing is bounded to the file size observed when the hook starts. Rotation, truncation, same-size rewrites and changed boundary
fingerprints rebuild the reducer. This assumes append-only transcripts: an in-place middle rewrite combined with an append can
escape boundary detection. Delete the affected local state checkpoint to force a rebuild after editing old transcript content.
Reports remain provisional; unsupported formats, skipped hooks and omitted child logs cannot establish complete accounting.
The ledger is bounded, but accumulated session reports/checkpoints need local retention management. They are not uploaded.

## Verification

```sh
python -m pytest -q tests/test_sprint3a.py tests/test_usage.py tests/test_usage_hooks.py tests/test_setup.py tests/test_client_integration.py
python -m pytest -q
```

Tests exercise structured nested calls, retries, direct-call deduplication, plain-text Claude errors, privacy, partial/resumed logs,
checkpoint recovery, bounded ledger retention, concurrent hook processes, skill preservation/idempotence and actual stdio MCP
responses using both generated client configurations. Existing tests cover counter resets and missing usage. No model is invoked.
