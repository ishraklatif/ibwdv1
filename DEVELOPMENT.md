# Current development direction

Owner direction, updated 2026-09-24: build on the existing IBWD repository for **Codex and Claude Code**, with **no additional spending**
and a **device-agnostic** design. Optimize context and recorded usage per correctly completed task; optional local models must earn their resource cost.
The active sprint sequence and acceptance criteria are in [docs/TOKEN_EFFICIENCY_ROADMAP.md](docs/TOKEN_EFFICIENCY_ROADMAP.md).
The original execution plan remains design history. Its paid gate no longer blocks local product engineering; it remains untested,
and no replacement correctness or transport test establishes an agent token-savings claim.

## Delivered in the takeover

- Explicit repository selection for scan and MCP startup, independent of the client's working directory.
- Client configuration output for Codex TOML and Claude Code JSON using the installed Python interpreter; no implicit dependency
  downloads, account changes, model calls, or edits to existing client settings.
- Read-only index diagnostics for file additions, edits, deletions, classification changes, build mismatch, corrupt databases,
  and disagreement between the manifest and database. This checks a snapshot of readable files, not concurrent-edit atomicity or
  semantic completeness. It does not monitor files in the background; rescan after edits.
- Unindexed or stale repositories refresh automatically before retrieval; repeated concurrent edits produce an explicit error.
- Exact symbol identities distinguish same-named methods, and broad path queries disclose ambiguity instead of searching only
  the first ten matches and potentially reporting a false negative.
- Real stdio MCP tests exercise all seven tools using both generated client configurations from a different working directory.
  These establish protocol/configuration compatibility, not a live Codex/Claude model evaluation.

## Local verification

Daily setup for both clients is in [docs/DAILY_USE.md](docs/DAILY_USE.md). Offline analysis of existing Codex and Claude Code
transcripts is in [docs/USAGE_MEASUREMENT.md](docs/USAGE_MEASUREMENT.md); no paid benchmark is required.
`ibwd usage-setup --repo PATH` installs opt-in Codex/Claude command hooks for automatic local session reports.
Codex hook trust remains a client-side user action. Deterministic hook tests validate configuration merging and real handler
subprocesses, not a live model session or measured savings.

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ibwd.cli scan --repo .
.venv/bin/python -m ibwd.cli doctor --repo .
.venv/bin/python -m ibwd.cli client-config --client codex --repo .
.venv/bin/python -m ibwd.cli client-config --client claude --repo .
```

The macOS sandbox-isolation test needs to run outside an enclosing sandbox. Oracle adapter tests skip when their optional local
tooling is absent. None of these commands calls a model. No extra API keys, Ollama models, or paid subscriptions are needed to run
IBWD itself; use of a host coding agent remains subject to that agent's own account and usage arrangement.

## Next priorities

1. **Sprint 3A instrumentation delivered:** shared routing skill, structured nested-call parsing, bounded server observations,
   incremental serialized hooks and automatic comparison reports. See [implementation and limits](docs/SPRINT_3A.md).
   Model adoption and token savings remain unverified; observe the next needed normal task in each client only.
2. **Sprint 3B delivered:** serialized staged index publication, generation-bound pagination, automatic freshness and bounded
   retrieval. [Delivery notes and measured profile](docs/SPRINT_3B.md) describe compatibility and limits.
3. **Sprint 4:** deliver lexical retrieval, budgeted task-context packets and exact source expansion. Measure cumulative evidence size,
   required-evidence retention and latency against competent bounded search; this is not a model-token benchmark.
4. **Sprint 5:** add scoped impact/test relevance and optional installed compiler evidence. Reference linkage is not verified test coverage.
5. **Sprints 6–7:** add optional local embeddings, then specialized local models only if separate quality/resource checks justify them.
   Select a device profile from available capabilities; deterministic operation always remains supported.
6. **Sprint 8:** package portable skills/client adapters and automatic comparison summaries. No additional model sessions for evaluation.

The retrieval improvements are future work, not shipped capabilities. Prioritize regressions on small Python/TypeScript fixtures and unseen local code
over tuning to the five development benchmark repositories. Preserve TypeDoc's reserved confirmation role.

The initial 2026-09-24 roadmap revision was documentation-only; subsequent Sprint 3A implements reporting and skill installation.
No local models are downloaded and the proposed Sprint 4 retrieval interfaces remain future work.
See [the complete document audit](docs/DOCUMENTATION_REVIEW.md).

## Historical experiment

The historical frozen tags, task hashes, gate, and build-27 evidence are preserved. The repository also contains the subsequent
`sprint3-ab-v2` configuration with a total-spend ceiling; it remains unauthorized for paid execution. Current source changes mean that
the historical paid runner's preflight should reject the development checkout. Do not refresh hashes to imply the old validation
covers new behavior. The old report's “harness not built” and preparation record's pending list are historical stage snapshots;
`benchmarks/experiment/README.md` documents the subsequently completed harness. Neither paid stage is authorized.
