# Sprint 3 gate definition — metric, weights, scope and policies

**Status: FROZEN, version 2 (2026-09-20). Both OPEN items from version 1 are closed (§10).** Nothing here may be changed
after paid runs begin without a new version of this file and a full re-run. Paid A/B runs do **not** start merely because the
free-stage scripts complete: they start only on the explicit `READY FOR PAID A/B` verdict (§11) and an owner instruction.
This file supersedes §4.6 (CSV columns), §5.1 (headline) and §5.3 of `SPRINT3_protocol.md`; that file is left as written.
Sprint 3 is **production-only for benchmark answers** (§7): tests are excluded from graph answers and ground truth, but may
appear in diagnostic oracle and runtime inventories.

## 1. Primary metric — cumulative processed tokens

    T[i,c,r] = input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens

- **Usage source: the final `result.usage` object only**, all four fields, once. Never add per-request usage on top.
- Verified on three saved transcripts: input, cache-creation and cache-read tokens equal the per-request sums and are disjoint
  (cache-creation sub-buckets add up); per-request **output** tokens undercount (232 vs 294; 872 vs 1933; 31 vs 1536).
- **Repeats: 3** per task and condition.
- **Missing usage, a missing `result` event, a budget/time-limit exit or any incomplete run = an invalid measurement.** It is
  retained as a failure record in the results (with its raw log), counts as a failed answer, and is never silently rerun,
  dropped or replaced. Only infrastructure failures declared here in advance (network outage before the first request,
  CLI crash before any model output) may be rerun, and each rerun is logged.

## 2. Aggregation — a ratio of weighted medians

    m[i,c] = median over r = 1,2,3 of T[i,c,r]
    R = ( sum_i w[i] * m[i,baseline] ) / ( sum_i w[i] * m[i,IBWD] )

Not an average of per-task ratios. Per-task paired ratios are published as secondary information.

## 3. Weights — hierarchical, equal at each level, predeclared

Equal weight per **language**; within a language equal per **repository**; within a repository equal per **question stratum**;
within a stratum equal per task. `w[i] = 1 / (#languages x #repos in language x #strata in repo x #tasks in stratum)`.

- **Headline strata (four):** Q1 callers, Q2 dependencies, Q3 chain, small-function.
- **Languages / repositories in the headline:** Python — Scrapy and Sphinx (Python-only scope, §10); TypeScript/TSX — Redux
  Toolkit and Bulletproof React.
- **Celery is the labelled negative control and stays outside the headline.** Its results are computed with the same formula
  and published beside it.
- **No task is removed and no weight is redistributed after results are observed.** A stratum that cannot be qualified for a
  repository **blocks that repository's admission**; its weight is never quietly redistributed.
- Planned paid workload: (4 headline repos x 4 strata + Celery 4 strata) tasks x 1 task per stratum x 2 conditions x 3 repeats
  = **120 sessions** (96 headline + 24 Celery), about $11.5 at the historical $0.096 mean; actual cost is unknown.

## 4. Gate

**GO** requires all of: (1) `R >= 3.0`; (2) IBWD answer correctness >= baseline correctness on the same tasks (deterministic
grader on frozen ground truth with exact expected sets, plus a source spot-check); (3) no blocking failure (§9).

Secondary, reported and not gating: output tokens, dollars, tool calls, latency. A clean but `R < 3` result is a STOP.

### Declared population thresholds (free stage, per repository, from adjudicated data)
- overall CALLS precision (resolved edges) **>= 95%**;
- **import-map and same-module precision >= 99%** each; inherited reported separately (target >= 99%);
- recall of in-scope, statically resolvable CALLS **>= 90%**.
Raw high-tier disagreements are **adjudicated before a pass is declared**; any *confirmed* high-tier defect blocks (§9).

## 5. Startup overhead — measured for both configurations

Measured 2026-09-20 (`benchmarks/sprint_3_startup_usage.json`; CLI 2.1.278; model `claude-sonnet-5`; trivial prompt; empty dir):
baseline `H_B` = **6,322** tokens; IBWD with exactly the 4 graph tools `H_I` = **8,362** (+2,040, charged to IBWD); all 7 tools
9,067. `R = (H_B + B)/(H_I + I) >= 3` is equivalent to `B >= 3*H_I - H_B + 3*I = 18,764 + 3*I`. The previous overhead
obstruction has been reduced; **the gate remains untested.** The first request's startup usage is recorded for every real run.

## 6. Frozen run configuration

Model `claude-sonnet-5` passed explicitly; CLI `2.1.278`; fresh session per run; `--no-session-persistence`; `--strict-mcp-config`;
`--tools "Read,Glob,Grep"`. Baseline: empty MCP config. IBWD: the four tools `ibwd_callers`, `ibwd_dependents`,
`ibwd_find_symbol`, `ibwd_trace_path` allowed; `ibwd_scan`, `ibwd_find_files`, `ibwd_list_symbols` disallowed. `--output-format
stream-json --verbose --max-budget-usd 1.00`. Baseline directory never contains `.ibwd`, ground truth or prior answers.
Condition order balanced with a fixed seed; warm/cold cache recorded.

### Frozen IBWD query policy
- **Default edge policy:** only `resolution_status = resolved` edges — import-map, same-module and inherited — appear in
  `ibwd_callers`, `ibwd_dependents` and `ibwd_trace_path`. Unique-name and suffix edges are `candidate` hints; fuzzy is
  **disabled** (experimental flag only). `include_candidates=true` exposes candidates with their status labelled; a candidate
  never contributes silently to an ordinary answer or path.
- **Relation per question:** Q1 and Q2 grade **CALLS**; Q3 uses a **CALLS-only** path (`edge_types=["CALLS"]`); REFERENCES is
  graded in the no-static-use diagnostic. When a pair has both CALLS and REFERENCES, both are preserved.
- **Path cost:** the existing `sum(1/confidence)`, described as a **heuristic cost**, not a probability or a maximum-product score.
- **Nested-owner projection:** the oracle keeps `original_owner`; the Sprint 3 comparison view rolls the *source* owner up to the
  enclosing indexed symbol. A nested *target* is never replaced by its enclosing function. Primary Q3 chains avoid depending
  on callback projection.
- **Task files:** `benchmarks/ground_truth/<repo>.yaml`; the SHA-256 of each is recorded in the manifest when qualified and
  must match at run time.

## 7. Scope definitions

**Benchmark scope** = the files listed in `benchmarks/manifests/<repo>.json` (§10). Ground truth, IBWD exports and oracle
graphs are all filtered by the same manifest.

**In supported scope:** statically visible calls, imports, inheritance and value references between indexed symbols in
manifest files of Python and JS/TS/TSX — including JSX tags, named exports, `tsconfig` aliases, calls through `self`/`super`,
default exports **except anonymous defaults**, and barrel re-exports **except default re-exports (`export { default }`) and
namespace re-exports (`export * as ns`)**.

**Outside supported scope (still reported):** dynamic dispatch; references resolved only through type or data flow (a
function-typed property, a variable's initial value) — such a target is a **possible target**, never a definite one; instance
receivers without type inference; nested functions (rolled up); vendored/generated files.

**Rules.** Difficult but legitimate references are never removed from a denominator after seeing failures. Every result reports
supported-scope recall and broader semantic coverage. An oracle edge is dropped only after an individual evidenced adjudication
that it is an oracle error, and every drop is listed. `unresolved_disagreement` records are never counted as proven correctness.

**Diagnostics.** Test files **are permitted** in oracle and runtime inventories; primary comparisons filter production→production.
The no-static-use and hard-case checks are **mandatory free diagnostics**, not paid strata.

## 8. Sprint 4 readiness (separate from the Sprint 3 gate)

Required and reported as medians of **five timing repetitions** (best-of-three claims are withdrawn): one-file rescan vs full
scan >= 3x per repository; and incremental == fresh-scan equivalence after a body edit, an import change, an export rename, a
base-class method change, file addition/deletion, and a `tsconfig`/alias change.

## 9. Blocking conditions

Progression stops if: a confirmed high-confidence (import-map / same-module / inherited) defect remains; **two or more distinct
adjudicated in-scope missing edges** remain; a golden-task answer is wrong; a declared precision threshold fails; the oracle is
incomplete enough that a claimed metric cannot be established; a qualified stratum is missing; incremental != fresh; stale
configuration invalidation; a fabricated path; silent truncation; or a summary's commit/hashes do not match its inputs.

## 10. Decisions closing the OPEN items (owner, 2026-09-20)

1. **Sphinx is admitted with a Python-only benchmark scope: 243 Python files.** Its 142 JavaScript files are not benchmark
   source; they are kept in a separate classification diagnostic (`benchmarks/manifests/sphinx_js_classification.json`).
2. **Amendment, applied to every repository:** independently identified generated/vendor assets may sit outside the benchmark
   scope. Every exclusion is a manifest record `{file, reason, evidence}` (a source header or documented provenance); a file is
   never excluded merely for containing long lines, and hand-written files are never excluded on heuristics alone.
3. Headline strata: Q1 callers, Q2 dependencies, Q3 chain, small-function. No-static-use and hard-case are mandatory free diagnostics.
4. Celery is the negative control and stays outside the headline. Planned paid workload: 120 sessions.
5. **The current repositories are development data.** They were inspected repeatedly and the resolver was changed in response;
   results on them are labelled as such. TypeDoc is reserved, **uninspected**, as the confirmation repository and must not be
   examined before the paid A/B; results on a confirmation repository are reported separately.

## 11. Verdict vocabulary

The free stage ends with exactly one of `READY FOR PAID A/B` (all required evidence linked) or `BLOCKED` (specific defects or
missing qualifications listed). Pushing to the remote is a separate action requiring an explicit instruction.
