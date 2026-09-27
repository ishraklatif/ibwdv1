# IBWD — Local Codebase Context for Codex and Claude Code

IBWD indexes repository files, symbols and static relationships, then exposes them through a local MCP server.
It helps coding agents locate relevant code and trace relationships before reading exact source and making changes.
The objective is less model context per correctly completed task. General token savings are **not yet established**.

## Current status

Sprints 1–6 deliver eleven MCP tools, incremental indexing, automatic freshness, bounded task-context packets, exact source reads,
explicit repository selection, index diagnostics, project setup and automatic local session reports for both clients.
Default symbol/graph support covers production Python and JS/JSX/TS/TSX. Explicit scoped discovery and impact also cover tests; lexical evidence includes documentation and configuration.
Sprint 5 adds scoped impact and optional compiler evidence. Sprint 6 adds opt-in local embeddings, disabled by default;
generated summaries are not shipped. The [local M1 semantic screen](docs/SPRINT_6_MEASUREMENT.md) found no net vague-query gain
and substantial latency; independent held-out acceptance remains pending.

The active plan is [the token-efficiency roadmap](docs/TOKEN_EFFICIENCY_ROADMAP.md), revised 2026-09-24.
It prioritizes trustworthy reporting, bounded retrieval and context assembly before optional local AI.
It is device-agnostic: deterministic operation is the base profile; each laptop can enable local models according to its capabilities.

No paid model calls, new subscriptions, usage credits, hosted inference or hardware purchases are part of this development plan.
IBWD itself requires no model or API key. Ordinary Codex/Claude conversations remain subject to the user's existing access and allowance.

## Set up once per repository

From an installed IBWD checkout:

```bash
.venv/bin/python -m ibwd.cli setup --repo /absolute/path/to/work-project
```

This configures both clients, installs routing guidance and reporting hooks, refreshes the index and checks local readiness.
Reconnect the clients and complete their normal trust/approval flow. Work normally; repeated “use IBWD” reminders should not be necessary,
but configured instructions alone do not prove the agent selected a tool.

Reports update at normal client hook events:

```text
<work-project>/.ibwd/usage/latest-codex.md
<work-project>/.ibwd/usage/latest-claude.md
```

Reports count direct and supported structured nested MCP calls, separate scans from retrievals, and retain unknowns.
Open `.ibwd/usage/comparison.md` for automatic client-separated summaries. Opaque orchestration and child usage can be missing.
See [Sprint 3A delivery notes](docs/SPRINT_3A.md); live adoption and token savings remain unverified.
A scan is maintenance; token totals are not tokens saved. See [measurement and limitations](docs/USAGE_MEASUREMENT.md).

For first installation or another laptop, follow [the full setup guide](docs/NEW_DEVICE_SETUP.md).
Use a new environment on each device and run setup for each work repository; do not copy virtual environments or machine-specific settings.
Current setup/hooks support macOS, Linux and WSL; native Windows hook generation is not implemented.

## Shipped tools

| Tool | Purpose |
| --- | --- |
| `ibwd_scan` | Refresh the local index |
| `ibwd_find_files` | Find files by category or path substring |
| `ibwd_find_symbol` | Find definitions and exact `symbol_id` values |
| `ibwd_list_symbols` | List indexed symbols in a file |
| `ibwd_callers` | Find incoming calls, imports, inheritance and value references |
| `ibwd_dependents` | Find outgoing relationships |
| `ibwd_trace_path` | Find a scoped path; CALLS-only by default |
| `ibwd_context` | Assemble bounded task evidence and graph links; optional local vector fusion |
| `ibwd_read` | Read exact source spans using a current expected hash |
| `ibwd_impact` | Bounded exposure/dependency paths, test relevance and previous-index diff |
| `ibwd_compiler_evidence` | Opt-in installed TypeScript references with diagnostics and provenance |

Use returned exact identities to disambiguate symbols. Read source before editing.
Retrieval refreshes automatically after edits or branch changes; concurrent scans and queries share a repository lock.
Use [context/read](docs/SPRINT_4.md) for unfamiliar tasks and source expansion, or the original exact discovery tools for known targets.
For troubleshooting, run:

```bash
.venv/bin/python -m ibwd.cli doctor --setup --repo /absolute/path/to/work-project
```

## Accuracy and evidence

Tree-sitter and SQLite provide deterministic extraction and storage, not complete program semantics.
Default graph queries use resolved edges; candidate hints are opt-in and fuzzy resolution is disabled by default.
Confidence numbers are heuristic scores, not calibrated probabilities.

An empty result means no matching resolved edges in the indexed scope. It never proves a function is unused or safe to delete.
Tests, dynamic dispatch and type-inferred receivers are not fully represented. Read [known limitations](KNOWN_LIMITATIONS.md).

Historical Sprint 3 validation found strong supported-scope precision on development repositories but substantial runtime gaps.
The qualified paid token-efficiency gate remains untested and is not authorized to run. The new roadmap changes future engineering priorities;
it does not turn that historical gate into a pass. See [Sprint 3](SPRINT_3.md) and [the frozen report](benchmarks/SPRINT3_free_stage_report.md).

## Revised development sequence

| Stage | Focus |
| --- | --- |
| Sprint 3A | Adoption evidence and accurate automatic usage reporting |
| Sprint 3B (implemented) | Automatic freshness, locked publication and version-2 bounded results |
| Sprint 4 (implemented; performance acceptance pending) | [Lexical retrieval, task-context packets and exact source expansion](docs/SPRINT_4.md) |
| Sprint 5 | [Delivered](docs/SPRINT_5.md): scoped impact, test relevance, previous-index diff and optional installed TypeScript evidence |
| Sprint 6 | Optional local embeddings, justified by retrieval quality |
| Sprint 7 | Optional local specialists and source-backed reusable memory |
| Sprint 8 | Portable skills, dual-client packaging and ordinary-work efficiency review |

Local deterministic tests establish engineering quality; existing ordinary-work logs provide observational usage evidence.
Neither establishes a causal savings claim on its own. Local models remain optional and are selected by available resources,
supported runtime and measured benefit rather than a fixed laptop or model name.

## Project documentation

- [Active roadmap](docs/TOKEN_EFFICIENCY_ROADMAP.md): architecture, sprint acceptance criteria, device profiles and model strategy.
- [Documentation review](docs/DOCUMENTATION_REVIEW.md): all 22 original Markdown paths and reasons for the pivot.
- [Development guide](DEVELOPMENT.md): delivered work and local validation commands.
- [Daily use](docs/DAILY_USE.md), [new-device setup](docs/NEW_DEVICE_SETUP.md), [usage measurement](docs/USAGE_MEASUREMENT.md).
- [Schema reference](SCHEMA.md) and [graph limitations](KNOWN_LIMITATIONS.md).
- [Original execution plan](IBWD_v1_EXECUTION_PLAN.md): preserved design history; future sequencing is superseded.
