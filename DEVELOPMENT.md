# Current development direction

Owner direction, 2026-09-20: build on the existing IBWD repository for **Codex and Claude Code**, with **no paid model calls**.
The original execution plan remains design history. Its paid gate no longer blocks local product engineering; it remains untested,
and no replacement correctness or transport test establishes an agent token-savings claim.

## Delivered in the takeover

- Explicit repository selection for scan and MCP startup, independent of the client's working directory.
- Client configuration output for Codex TOML and Claude Code JSON using the installed Python interpreter; no implicit dependency
  downloads, account changes, model calls, or edits to existing client settings.
- Read-only index diagnostics for file additions, edits, deletions, classification changes, build mismatch, corrupt databases,
  and disagreement between the manifest and database. This checks a snapshot of readable files, not concurrent-edit atomicity or
  semantic completeness. It does not monitor files in the background; rescan after edits.
- Unindexed repositories produce an actionable MCP error rather than an empty graph result.
- Exact symbol identities distinguish same-named methods, and broad path queries disclose ambiguity instead of searching only
  the first ten matches and potentially reporting a false negative.
- Real stdio MCP tests exercise all seven tools using both generated client configurations from a different working directory.
  These establish protocol/configuration compatibility, not a live Codex/Claude model evaluation.

## Local verification

Daily setup for both clients is in [docs/DAILY_USE.md](docs/DAILY_USE.md). Offline analysis of existing Codex and Claude Code
transcripts is in [docs/USAGE_MEASUREMENT.md](docs/USAGE_MEASUREMENT.md); no paid benchmark is required.

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

1. Improve deterministic retrieval quality and concise output, with explicit scope and truncation metadata. Measure response bytes,
   correctness, latency, and incremental/fresh equivalence locally; label bytes as bytes, not measured agent tokens.
2. Add bounded impact traversal with evidence paths, and separately labelled test linkage. Keep inferred test relevance distinct
   from verified coverage; the current production-only graph cannot establish test coverage.
3. Add opt-in local lexical/concept retrieval before requiring embeddings or generated summaries. Preserve source provenance and
   clear invalidation for every derived result.

These are future work, not shipped capabilities. Prioritize regressions on small Python/TypeScript fixtures and unseen local code
over tuning to the five development benchmark repositories. Preserve TypeDoc's reserved confirmation role.

## Historical experiment

The historical frozen tags, task hashes, gate, and build-27 evidence are preserved. The repository also contains the subsequent
`sprint3-ab-v2` configuration with a total-spend ceiling; it remains unauthorized for paid execution. Current source changes mean that
the historical paid runner's preflight should reject the development checkout. Do not refresh hashes to imply the old validation
covers new behavior. The old report's “harness not built” and preparation record's pending list are historical stage snapshots;
`benchmarks/experiment/README.md` documents the subsequently completed harness. Neither paid stage is authorized.
