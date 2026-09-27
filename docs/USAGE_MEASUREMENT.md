# Measure ordinary work without extra benchmark runs

The question is whether IBWD helps finish comparable work correctly with less recorded model usage. A smaller graph response alone
does not establish that. The tools here summarize existing transcripts locally; they do not run an agent or estimate extra messages.

## 1. Keep ordinary client logging

No IBWD logging hook, remote telemetry collector, API key, or paid analyzer is required.

| Client | Existing local transcripts | Accounting used by IBWD |
|---|---|---|
| Codex | Usually `$CODEX_HOME/sessions/**/rollout-*.jsonl`, default `~/.codex/sessions/`; archived sessions may be elsewhere | Last `event_msg/token_count` cumulative `total_token_usage` snapshot; repeated snapshots are not summed |
| Claude Code | `~/.claude/projects/<project>/<session>.jsonl`, or under `CLAUDE_CONFIG_DIR` | Assistant usage deduplicated by message ID; largest observed counters per message, then summed |

Codex locations and JSONL fields are based on local observed logs, not a stable public export contract. Both formats can change.
Do not use `history.jsonl` as a usage transcript. Cloud-only sessions may have no local log. If your client disables persistence,
this analyzer cannot reconstruct missing usage; it will not launch another session to fill the gap.

### Automatic reports (recommended for daily use)

Run this once per work project using your installed IBWD interpreter:

```bash
"$IBWD_PY" -m ibwd.cli setup --repo "$TARGET_REPO"
```

This installs MCP configuration, routing guidance, reporting hooks and a fresh index for both clients. See
[daily setup](DAILY_USE.md) for preservation, backups and readiness checks. Use `--client codex` or `--client claude` for one client.
The narrower `usage-setup` command remains available to install reporting only; its existing-file backups use `.ibwd-backup`.

Restart/reconnect the client. **In Codex, open `/hooks` once to review and trust the installed hooks.** Project configuration
must also be trusted; disabled or administrator-restricted hooks cannot be enabled by IBWD.

After each normal assistant turn (`Stop`) and normal session end (`SessionEnd`), the client supplies its exact transcript
path to IBWD. No log search or separate analysis command is needed. Open these files in your editor:

- `.ibwd/usage/latest-codex.md` or `.ibwd/usage/latest-claude.md`: most recently captured report for that client.
- `.ibwd/usage/sessions/`: one JSON and readable Markdown report per client/session, updated on resume.
- `.ibwd/usage/comparison.md`: automatically refreshed observational cohorts and missing-data counts.

Sprint 3A adds evidence-backed configuration, connection, scan/retrieval/error, fallback and incompleteness fields.
Supported structured MCP lifecycle events count even when the outer call is orchestration; source strings do not count.
The bounded local server ledger joins only on exact response observation IDs. See [format and limits](SPRINT_3A.md).
Setup also installs the shared navigation skill for both clients; rerun setup after upgrading and reconnect.

Reports include recorded token totals, direct tool counts, and whether IBWD was mentioned in recognized Codex instruction
records. Ordinary conversation mentions do not count as instruction evidence. Claude instruction loading is not reconstructed.
Absence of recorded instructions does not prove instructions were absent. Availability, task category, success and condition
remain unknown rather than being guessed; the automatic reports are not eligible for comparative savings claims.

Hooks run local Python only: no model requests, transcript copies, daemon, or remote telemetry. Successful hooks emit no model context,
and failures are advisory rather than requests to continue a turn. The client must retain readable transcripts. Reports are
snapshots, including at session end: final accounting may not have reached the transcript yet. Crashes/forced exits may skip hooks;
the last completed Stop snapshot remains. Subagent events with agent metadata are ignored, and child logs are not aggregated.

Codex session-end hooks have a maximum three-second runtime; the setup also uses a 30-second Stop hook to save progress before
shutdown. Very large logs can exceed those budgets; the client will report hook failure and the previous report remains. Hooks
use POSIX shell quoting (macOS/Linux). Transcript formats are version-dependent and may require parser updates.

To disable reporting, remove only the command entries containing `ibwd.cli usage-hook` from the two client configuration files,
then reconnect. Existing reports remain. Do not restore a backup over settings you changed after installation.

Hook contracts: [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Claude Code hooks](https://code.claude.com/docs/en/hooks).
The commands below remain useful for manual analysis and explicitly labelled comparisons.

Claude Code documents its [transcripts, retention and logging controls](https://code.claude.com/docs/en/claude-directory).
Normal sessions must retain their transcripts; do not use `--no-session-persistence` or logging opt-outs if you want to analyze them.
Export your small reports before transcript cleanup. Do not change managed retention rules just for this experiment.

List candidate files locally without displaying their contents:

```bash
rg --files --hidden "${CODEX_HOME:-$HOME/.codex}/sessions" -g '*.jsonl'
rg --files --hidden "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" -g '*.jsonl'
```

Choose the actual session for your work project; ignore unrelated sessions, superseded copies, and child-agent files for the initial
comparison. Raw transcripts can contain private code and credentials. Keep them in their existing local locations, out of GitHub.
The report exports no prompts, tool arguments, tool outputs, repository paths, or raw session IDs. Tool/model names and timestamps
are retained, so review even a summary before sharing it.

## 2. Label the work before reviewing token totals

Keep a short local ledger per session:

| Field | Example |
|---|---|
| Session report filename | `codex-refactor-01.json` |
| Project and task category | project A; structural navigation / implementation / debugging |
| Size/complexity | small, medium, large; note unusual changes |
| IBWD configuration | enabled throughout / disabled throughout / unknown |
| Model, effort, client version | keep these stable; note defaults or changes |
| Outcome | passed / failed / unfinished; cite relevant tests and manual review |
| Confounders | other MCP servers, compaction, resumed history, subagents, interruptions, cache state |

Use `enabled` when the server was configured, even if the agent never called it: its overhead can still matter. Do not infer
`disabled` from zero calls. Mark mixed configuration sessions as `unknown`. Record outcomes from actual task verification, not from
whether the assistant said it succeeded. `passed` does not mean that every intermediate tool call succeeded.

Prefer fresh sessions for naturally separate work tasks. Do not rerun tasks or start paid A/B sessions. Existing disabled sessions
can be useful context, but unrelated historical tasks are not matched controls. If all normal work uses IBWD, you can measure its
usage and adoption but cannot infer savings relative to a missing baseline.

## 3. Generate reports after finishing work

From a terminal, using the paths from [daily setup](DAILY_USE.md):

```bash
mkdir -p "$TARGET_REPO/.ibwd/usage"

"$IBWD_PY" -m ibwd.cli usage-report /path/to/codex-rollout.jsonl \
  --client codex --condition enabled --task-kind structural --outcome passed \
  > "$TARGET_REPO/.ibwd/usage/codex-task-01.json"

"$IBWD_PY" -m ibwd.cli usage-report /path/to/claude-session.jsonl \
  --client claude --condition enabled --task-kind structural --outcome passed \
  > "$TARGET_REPO/.ibwd/usage/claude-task-01.json"
```

Replace the example labels with reality. `--task-kind` accepts `structural`, `implementation`, `debugging`, `mixed`, or `unknown`.
Condition and outcome default to `unknown`. Use `failed` for a completed unsuccessful task; leave unfinished work `unknown`.
Reports from still-active sessions are provisional even if their last JSONL line is complete.

The report contains:

- Recorded token totals and their component counters, model/effort/version metadata, timestamps and direct tool-call counts.
- A pseudonymous session key, so copies/resumed snapshots cannot silently count as separate sessions.
- Warnings for incomplete usage, malformed logs, decreasing cumulative counters, mixed models/effort, or contradictory labels.
- `comparable`: only a metadata/format eligibility flag, **not** proof that two tasks are comparable or a session is finished.

Codex cache and reasoning counters are subcounts and are not added to `total_tokens` again. Claude's input, cache creation, cache
read and output counters are summed; nested cache breakdowns are not added again. Claude transcript message totals are an estimate:
they can omit output present only in final provider accounting. Neither client's report is a bill or a subscription quota meter.
Other event formats, cost snapshots and API-result totals are deliberately not added on top of the chosen source.

## 4. Summarize without mixing clients

```bash
"$IBWD_PY" -m ibwd.cli usage-summary "$TARGET_REPO"/.ibwd/usage/*-task-*.json
```

Groups remain separate by client, model, effort, client version, task category and declared IBWD condition. Each group shows session
counts, passing outcomes, median tokens across all outcomes, and median tokens for passing outcomes. Missing/ambiguous records are
excluded from grouped medians and counted explicitly; do not hide those exclusions. Missing effort metadata means the report is
excluded, even if you know the default: preserve the report and compare manually with the ledger rather than inventing log fields.
For a resumed session, replace its old report with the latest one. Supplying two snapshots of one session raises an error.

The tool does not combine child-agent logs. If you use subagents, the parent transcript may omit their usage: mark the session
`unknown` for outcome/condition in this report workflow and analyze it separately. Indirect calls inside shell/orchestration tools
are not attributed to IBWD, so `direct_ibwd_calls` is a lower bound, not a complete adoption metric.

## 5. Interpret conservatively

Compare within the same client/model/effort/version and similar tasks in the **same project**, with the same success criteria.
Check failures and rework alongside tokens: a cheap wrong answer is not a win. Keep slow, interrupted and failed work in your ledger;
do not discard it to improve the result. Medians reduce outlier sensitivity but cannot correct differences in task difficulty,
caching, provider, other tools or human behavior. Whole-session logs mix navigation with coding, reasoning and validation.

A useful observational finding is: “For this group of normal tasks, completed-task usage fell while success stayed similar.”
It is not a causal guarantee, and small groups warrant little confidence. The summary intentionally computes no savings percentage.
Codex's [official usage guidance](https://developers.openai.com/codex/pricing) also explains that context, tools and caching influence
allowance. No conversion from these totals to extra subscription messages is established.

The next optimization decision should follow evidence: compact a frequently oversized result, fix a stale answer, improve routing,
or disable IBWD for task types where it adds overhead. No extra model benchmark is required to make those local improvements.

## 6. Automatic Sprint 8 comparisons

The setup-installed hooks now publish `comparison.json` alongside `comparison.md`.
Automatic cohorts also separate project identities and retain failed/incomplete/unknown
outcomes, reported rework and provisional sessions. Exact response-ID joins expose
retrieval freshness, refreshes, semantic fallbacks, returned evidence counts and payload
bytes. Missing ledger observations remain unknown; byte counts are not token savings.

Record an assessment using the session key in the automatic report:

```bash
ibwd usage-label SESSION_KEY --repo /path/to/repo --client claude \
  --task-kind debugging --condition enabled --outcome failed --rework yes
```

Labels are user-reported and expire when the transcript snapshot changes. They never
establish final token accounting. See [Sprint 8 contracts and tested compatibility](SPRINT_8.md).

## 7. Compare optional embeddings during ordinary work

Embedding mode is recorded automatically for MCP `ibwd_context` requests, including
repository defaults, explicit overrides and cached deterministic responses. Modes are
`disabled`, `used`, `fallback`, or `unknown`. A fallback may happen before inference or
after an attempted model call; reports distinguish these with `inference_attempts`.
Freshness retries retain each attempt. Other retrieval tools do not use embeddings.
No additional model request is made for observation or reporting.

Each observation includes the indexed model's weights/configuration digest and
preprocessing version when known. The digest identifies the model contents without
exporting the local installation path; on fallback, it may describe an unusable index.
Optional elapsed time includes model validation, runtime startup, inference and ranking.
It excludes the rest of context retrieval; total request duration remains a separate
counter. Memory usage is not measured. A successful embedding call does not establish
that its returned evidence helped the task.

Configure the mode for the next ordinary task using the existing commands:

```bash
ibwd semantic-config --repo /path/to/repo --disabled
ibwd semantic-config --repo /path/to/repo --enabled
```

These commands select a repository default; they do not build an index or install a
model. Enabling requires an existing usable local semantic index to avoid fallback.
An explicit `semantic` argument on a context request overrides that default. Existing
client processes can observe a changed default on subsequent requests.

After reviewing the task, record all applicable labels with the session key from its
report (omitted options become unknown):

```bash
ibwd usage-label SESSION_KEY --repo /path/to/repo --client codex \
  --task-kind implementation --condition enabled --outcome passed --rework no \
  --retrieval-usefulness useful
```

Use `useful` when the returned evidence materially helped, `partly-useful` when some
helped but required substantial additional searching, `not-useful` when it did not
help, and `unknown` when you cannot assess it. This is a user-reported assessment of
retrieval across the captured session, not an individual-result relevance score or
proof that embeddings caused success. Mixed-mode sessions are kept separate. Ratings
expire with their transcript snapshot, just like outcome/rework labels. Older label
files remain readable with usefulness unknown.

Automatic comparisons separate mode/model/preprocessing profiles in addition to the
existing project/client/model/effort/version/task/IBWD-condition fields. A partially
linked session is explicitly grouped as incomplete attribution. Older records lacking
instrumentation stay unknown. Cohorts show usefulness counts and the number of known
ratings alongside outcomes, rework, tokens, fallback and timing evidence. Snapshot
profiles survive ledger eviction; current timing totals cover only retained events.
Sessions without observed context calls never become an embeddings-disabled baseline.

Before collecting evidence, record the decision criteria: eligible task kinds, the
minimum number of rated sessions per mode, the improvement in usefulness required,
acceptable outcome/rework rates, and maximum optional latency and memory. Choose these
for the project/device before looking at results. Compare comparable ordinary tasks
using one mode per session where practical, keeping failures and unknowns visible.
Do not pool fallback or mixed-mode sessions with successful embedding use. Memory
needs separate local evidence before a memory requirement can pass. The reports do
not automatically accept a role, create causal savings claims, or run evaluation jobs.
