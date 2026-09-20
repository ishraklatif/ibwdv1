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
