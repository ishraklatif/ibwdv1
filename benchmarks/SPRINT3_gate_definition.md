# Sprint 3 gate definition — metric, weights and scope

**Status: FROZEN on 2026-09-20, except the two parameters marked OPEN (§9).** No paid A/B session may run until
both are closed. Nothing here may be changed after paid runs begin without a new version of this file and a re-run.
This file supersedes §4.6 (CSV columns), §5.1 (headline) and §5.3 of `SPRINT3_protocol.md`; that file is left as written.

Sprint 3 is **production-only**: test files are not part of the graph or of the benchmark ground truth.

## 1. Primary metric — cumulative processed tokens

For session `r` of task `i` under condition `c`:

    T[i,c,r] = input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens

**All four values are taken from the final `result` event's `usage` object, once.** Never add per-request usage on top.

Verification (saved transcripts `benchmarks/raw/sprint_3/*.jsonl`, 3 sessions): for input, cache-creation and cache-read
tokens the final `result.usage` equals the sum over unique assistant messages, and the cache-creation sub-buckets
(`ephemeral_1h` + `ephemeral_5m`) sum to its total, so the three input categories are disjoint and counted once.
**Output tokens are not consistent:** per-message snapshots undercount them (232 vs 294; 872 vs 1933; 31 vs 1536), so
per-request sums must not be used for output. The unique-message count is used only for the request count.

## 2. Aggregation — a ratio of weighted medians

    m[i,c] = median over r = 1,2,3 of T[i,c,r]

    R = ( sum_i w[i] * m[i,baseline] ) / ( sum_i w[i] * m[i,IBWD] )

This is **not** an average of per-task ratios. Per-task paired ratios are also published, as secondary information.

## 3. Weights — hierarchical, equal at each level, predeclared

1. equal weight per **language**;
2. within a language, equal weight per **repository**;
3. within a repository, equal weight per **question stratum**;
4. within a stratum, equal weight per task.

`w[i] = 1 / (#languages x #repos in that language x #strata in that repo x #tasks in that stratum)`; weights sum to 1.

- **Celery is the labelled negative control.** Its results are published alongside the headline, computed with the same
  formula, but it is **excluded from the headline `R`**.
- **Small-function questions are a stratum and stay in the workload.** No task is dropped because it scored low.
- Languages in the headline: Python and TypeScript/TSX. Python repositories: Scrapy and, if admitted, Sphinx (§9).
  TypeScript repositories: Redux Toolkit and Bulletproof React.

## 4. Gate

**GO** requires all of:
1. `R >= 3.0`;
2. IBWD answer correctness >= baseline correctness on the same tasks (deterministic grader on the frozen ground-truth
   YAML with exact expected sets, plus a source spot-check);
3. no blocking structural failure: invalid ground truth; missing corpus coverage; a wrong answer on a golden task; a
   high-confidence spurious edge; an unsupported "safe to delete" statement; >= 2 distinct adjudicated in-scope missing
   edges (unique edges, not repeats); incremental != fresh scan; stale configuration invalidation; a fabricated path;
   silent truncation of output.

Secondary, reported and **not gating**: output tokens (comparable with Sprints 1–3), cost in dollars, tool calls, latency.
A clean but `R < 3` result is a STOP under this gate; an output-token win may be reported only as a narrower finding.

## 5. Startup overhead — measured for both configurations

Measured 2026-09-20 with a trivial prompt in an empty directory (`benchmarks/sprint_3_startup_usage.json`), CLI 2.1.278,
model `claude-sonnet-5`:

| Configuration | T (tokens) |
|---|---|
| Baseline: `--tools Read,Glob,Grep`, empty MCP config | **6,322** (`H_B`) |
| IBWD, exactly the 4 Sprint-3 graph tools | **8,362** (`H_I`) |
| IBWD, all 7 server tools advertised | 9,067 |

IBWD's schemas cost **+2,040 tokens** (4 tools) and are charged to IBWD. With task-specific work `B` (baseline) and `I` (IBWD)
beyond startup, `R = (H_B + B) / (H_I + I) >= 3` is equivalent to `B >= 3*H_I - H_B + 3*I`, i.e. **`B >= 18,764 + 3*I`**.
(With equal overhead `H` this reduces to `B >= 2H + 3I`.) The previous overhead obstruction (29,267 tokens per session
with `--allowedTools` alone) has been reduced; **the gate remains untested.** Startup usage (the first request's tokens)
must also be recorded for every real run, since the real working directory can add project files.

## 6. Frozen run configuration

- Model `claude-sonnet-5` passed explicitly; CLI `2.1.278` (nvm install); fresh session per run, `--no-session-persistence`.
- Baseline: `--strict-mcp-config --tools "Read,Glob,Grep" --allowedTools "Read,Glob,Grep" --mcp-config '{"mcpServers":{}}'`.
- IBWD: same, plus `--mcp-config <ibwd server>` , `--allowedTools` adding `mcp__ibwd__ibwd_callers`, `_dependents`,
  `_find_symbol`, `_trace_path`, and `--disallowedTools` for `_scan`, `_find_files`, `_list_symbols`.
- `--output-format stream-json --verbose --max-budget-usd 1.00`; raw logs kept; budget/time-limit exits count as failures.
- Baseline directory never contains `.ibwd`, ground truth or prior answers; evidence lives outside the agent-readable mount.
- Condition order balanced with a fixed seed; >= 3 repeats per task and condition; warm/cold cache recorded.

## 7. Scope definitions (declared now, before the full comparisons)

**In supported scope:** production files (audit roots, excluding tests, docs, vendored and generated files) in Python and
JS/TS/TSX; statically visible calls, imports, inheritance and value references between indexed symbols, including JSX tags,
default exports, barrel re-exports, `tsconfig` aliases, and calls through `self` / `super`.

**Declared outside supported scope (still reported):** dynamic dispatch (`getattr`, `importlib`, registries, DI); references
resolved only through **type or data flow** (a function-typed property, a local variable's initial value); instance-variable
receivers without type inference; nested functions (rolled up to the enclosing symbol); test files; vendored/generated files.

**Rules.** Difficult but legitimate references are **never removed from a denominator after seeing failures**. Every result
reports both **supported-scope recall** and **broader semantic coverage** (all adjudicated-valid oracle edges). A candidate
edge may be dropped only after an individual, evidenced adjudication that it is an oracle error, and the drop is listed.
Note: the two unsupported categories above were named after the pilot's misses; they are declared before the full comparison.

## 8. Sprint 4 readiness (separate from the Sprint 3 gate)

Timing cases reported so far exceed the 3x threshold (one-file rescan vs full: Scrapy 13.6x, Sphinx 16.2x, Celery 6.7x,
Redux Toolkit 6.3x, Bulletproof 5.3x; best of 3). **Readiness is not yet established.** It also requires:
the declared repeat procedure (five timed repetitions per edit, median full/incremental >= 3x per repository), and
incremental == fresh-scan equivalence after edits that change imports, exports, inheritance, deletion and configuration.
Currently covered by tests: body edits, added/deleted files, `tsconfig` changes. **Not yet covered:** import changes, export
renames, base-class method edits; timing uses best-of-3, not a median of five.

## 9. OPEN parameters — awaiting the owner

1. **Question strata** for the headline: {Q1 callers, Q2 dependencies, Q3 chain, small-function} (the recommended headline)
   versus also including {no-static-use, labelled hard-case} (the plan's six-task design; 180 sessions vs 120).
2. **Sphinx admission.** The audit's "385 production files" = 243 Python + 142 JavaScript: 34 minified files IBWD excludes,
   **70 generated translation catalogs**, 33 other search JS and 5 theme assets (`sphinx_reconciliation.json`). Under the
   original literal rule (no committed minified/vendor bundles) Sphinx does not qualify merely because IBWD excludes them.
   Options: a documented amendment permitting such assets when independently identified and excluded from the benchmark
   scope (applied to every repository), or reject Sphinx.
