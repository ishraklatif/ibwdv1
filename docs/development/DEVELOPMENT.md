# Current development direction

Owner direction, updated 2026-09-24: build on the existing IBWD repository for **Codex and Claude Code**, with **no additional spending**
and a **device-agnostic** design. Optimize context and recorded usage per correctly completed task; optional local models must earn their resource cost.
The active sprint sequence and acceptance criteria are in the [roadmap](./ROADMAP.md).
Proposed methods for evaluating agent use of IBWD are in [Agent evaluation methods](./AGENT_EVALUATION_METHODS.md).
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

Daily setup for both clients is in [docs/DAILY_USE.md](../guides/DAILY_USE.md). Offline analysis of existing Codex and Claude Code
transcripts is in [docs/USAGE_MEASUREMENT.md](../guides/USAGE_MEASUREMENT.md); no paid benchmark is required.
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
   incremental serialized hooks and automatic comparison reports. See [implementation and limits](./SPRINT_3A.md).
   Model adoption and token savings remain unverified; observe the next needed normal task in each client only.
2. **Sprint 3B delivered:** serialized staged index publication, generation-bound pagination, automatic freshness and bounded
   retrieval. [Delivery notes and measured profile](./SPRINT_3B.md) describe compatibility and limits.
3. **Sprint 4 features delivered:** lexical retrieval, budgeted task-context packets, exact source expansion and saved-output reduction.
   [Contracts and verification](./SPRINT_4.md) include 32 fixed retrieval cases. Comparative payload/latency acceptance remains
   unmeasured under the no-benchmark instruction; no savings claim is made.
4. **Sprint 5 delivered:** scoped impact/test relevance, previous-index diff with deleted identities, and optional installed TypeScript evidence.
   [Contracts and limits](./SPRINT_5.md) describe provenance, bounded queries and deterministic checks. Reference linkage is not verified test coverage.
5. **Sprint 6 optional implementation delivered:** local-only embedding adapter, atomic vector generations, incremental cache and
   rank fusion. [Contracts and limits](./SPRINT_6.md) document deterministic tests. A subsequently authorized
   [M1 local screen](./SPRINT_6_MEASUREMENT.md) found no net vague-query gain and substantial query latency; the quality gate failed.
   This checkout is explicitly enabled; other repositories stay off.
6. **Sprint 7 deterministic slice delivered:** [extractive summaries and shared task handoffs](./SPRINT_7.md),
   with source/dependency revalidation and CLI/MCP access. Additional model roles remain deferred until separate checks justify them.
7. **Sprint 8 implemented:** [portable setup and ordinary-work evidence](./SPRINT_8.md), with fresh/update adapter tests,
   request-linked freshness/fallback reporting, project cohorts and explicit snapshot-bound outcome/rework labels.
   Client model adoption and savings remain unverified; no additional model sessions for evaluation.

Further retrieval improvements remain future work. Prioritize regressions on small Python/TypeScript fixtures and unseen local code
over tuning to the five development benchmark repositories. Preserve TypeDoc's reserved confirmation role.

The initial 2026-09-24 roadmap revision was documentation-only; subsequent Sprint 3A implements reporting and skill installation.
No local models are downloaded; Sprint 4's context/read interfaces are now implemented.
See [the complete document audit](../archive/reviews/DOCUMENTATION_REVIEW_2026-09-24.md).

## Historical experiment

The historical frozen tags, task hashes, gate, and build-27 evidence are preserved. The repository also contains the subsequent
`sprint3-ab-v2` configuration with a total-spend ceiling; it remains unauthorized for paid execution. Current source changes mean that
the historical paid runner's preflight should reject the development checkout. Do not refresh hashes to imply the old validation
covers new behavior. The old report's “harness not built” and preparation record's pending list are historical stage snapshots;
`benchmarks/experiment/README.md` documents the subsequently completed harness. Neither paid stage is authorized.
