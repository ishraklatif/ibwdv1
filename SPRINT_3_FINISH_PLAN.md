# IBWD Sprint 3 — verification plan and measured corpus screening

Prepared 20 September 2026. This responds to the six deliverables in your uploaded prompt. It builds on your implementation claims; it does not claim to have revalidated IBWD itself.

**Recommendation: stay in Sprint 3 until corpus qualification, graph correctness, and the predeclared token gate all pass. Run free deterministic checks before paid agent sessions.**

What was actually done here: cloned public repositories with full history; detached checkouts at recorded SHAs; counted tracked production files under explicit source roots; ran cloc 2.06; inspected source/configuration/license files; ran deliberately incomplete AST/compiler candidate discovery; checked current official oracle and Claude Code documentation; built the accompanying verification scripts. No IBWD checkout/executable or Claude Code executable was available. No paid sessions, real-repository test suites, full SCIP indexes, or complete reference adjudication were run. Every semantic qualification not established below remains UNVERIFIED.

The distinction matters: the shortlist is a screened set of candidates, **not a certified 4–6-repository benchmark**. In particular, React orchestrator multiplicity and Q3 semantics and several full-oracle coverage requirements remain open. The tooling refuses to convert missing evidence into a pass.

## 1. Freeze the experiment before collecting more paid results

Use two separate statements of success:

1. **Structural correctness:** the tools return the agreed production-code relations correctly, including uncertainty and truncation metadata.
2. **Agent efficiency:** the same questions, same model/settings, same output schema and source snapshot require at least 3× fewer cumulative processed tokens, with correctness no worse than baseline.

My recommended primary gate is **cumulative processed tokens**, not output tokens alone. For a provider usage schema with disjoint categories, define:

`T = sum over API requests(input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens)`.

Verify the installed CLI's aggregation semantics against one saved transcript before using its final usage object. Do not add final-session totals to per-request totals. Cache hits still count as processed input tokens here; their cheaper price is reflected separately in cost. Your CSV's `total_context_tokens` field should mean this cumulative quantity, not peak context length. Add `peak_input_tokens` separately if wanted.

Output tokens alone measure generated reasoning/tool arguments/answer text, not how much source or tool output was consumed. Retain output-token ratios as a secondary metric. Do not call 5× fewer generated tokens “5× cheaper” or “5× less context.” If you deliberately choose an output-only business objective, version the protocol and phrase the claim narrowly before running; it is a changed objective, not a retrospective rescue of a failed gate.

Use minimal, matched benchmark sessions and inspect startup context. Remove unrelated personal memory, tools and integrations symmetrically using supported CLI configuration. IBWD's own tool schema overhead remains charged to IBWD. Do not subtract an arbitrary 66k constant after seeing results. Also report a separate real-workflow profile with normal startup overhead.

For task i and condition c, `m[i,c] = median(T[i,c,1..3])`. Primary aggregate:

`R = sum_i w[i] * m[i,baseline] / sum_i w[i] * m[i,ibwd]`.

Predeclare equal weights by repository, language and question stratum; normalize within each cell. Report task-level paired ratios too. Do not use a ratio of two unrelated medians across heterogeneous tasks. Headline excludes the prelabelled dynamic negative-control stratum; publish its results alongside the headline, not hidden in an appendix. Small-function tasks remain in their own declared stratum and in any advertised general-workload aggregate. Do not discard the repeatable 2.3× task.

Go requires R ≥ 3.0, IBWD answer correctness ≥ baseline on the same tasks, and all blocking correctness gates below. Three repeats are a minimum descriptive estimate, not strong statistical evidence; add a repository-clustered bootstrap interval when there are enough repositories. An inconclusive interval should be reported as such rather than asserted as a universal guarantee.

## 2. Independent oracles: ranking and normalization

| Language / purpose | Preferred oracle | Why / limitations |
|---|---|---|
| TS/JS task-level references | TypeScript Language Service `findReferences` in the actual leaf tsconfig, then AST classification | Respects lexical symbols, aliases and types; missing dependencies or wrong project boundaries invalidate apparent negatives. |
| TS/JS whole-repository inventory | scip-typescript | Persistent symbol-occurrence index, useful for every-function sweeps. It shares TypeScript ancestry with the language service, so agreement is not two independent compiler implementations. |
| Python static inventory | scip-python with the actual installed project environment | Pyright-derived symbol index; stronger than name matching for bindings, but still incomplete for runtime dispatch. |
| Python cross-check | Jedi project references, with AST source inspection | Different implementation useful for disagreements. Jedi documents that difficult searches can stop; zero results are not an exhaustiveness certificate. |
| Python executed calls | Runtime tracing during the repository's own tests | Establishes that observed edges occur in that environment. Absence only means “not observed in this run.” |
| Fixture checks / triage | Python AST, TS AST, manual source reading | Strong for syntax and scoped fixtures; name-only matching is not the final semantic oracle. |

These are complementary oracles, not a claim that one tool is the universally strongest analyzer. SCIP is an interchange format, not itself a call graph. Extract symbol IDs, definition occurrences and reference occurrences, then classify each occurrence and attach its lexical caller. [scip-python](https://github.com/sourcegraph/scip-python), [scip-typescript](https://github.com/sourcegraph/scip-typescript), [SCIP protocol](https://github.com/scip-code/scip).

Normalize into `symbols[]` and `edges[]`. A symbol needs canonical `id`, repository-relative `file`, definition `line`, qualified `name` and `kind`. An edge needs canonical source/target IDs, relation, callsite file/line/column, and provenance. Keep IBWD numeric database IDs out of comparisons. Store both declaration and callsite positions: a caller-function definition line is not a callsite line.

Keep CALLS, IMPORTS, INHERITS and REFERENCES separate. Importing a callable is not executing it; a JSX render is a labelled render-use under IBWD's documented convention, not proof of synchronous invocation. Count opening/self-closing JSX once and exclude closing tags. Type-only uses must not become runtime calls. Retain both relations when a function both calls and passes another function; a one-row display can contain a relation set without discarding provenance.

For nested functions, preserve the original nested owner in oracle data, then derive a second comparison view rolled up to the enclosing indexed symbol to match Sprint 3's contract. Never silently compare a function-level oracle with IBWD's rolled-up graph.

Runtime: `trace_python.py` observes Python-to-Python calls in one process and newly started Python threads. Use Python 3.11+ for qualified code-object names. It does not trace subprocesses or C callees and does not prove coroutine scheduling causality; async resumes can repeat events. A production implementation can use Python 3.12+ `sys.monitoring` for finer event handling. Ordinary line/branch coverage alone is not a caller–callee graph. [Python profiling hooks](https://docs.python.org/3/library/sys.html#sys.setprofile), [monitoring events](https://docs.python.org/3/library/sys.monitoring.html).

Let S be the adjudicated static CALLS set and D observed production→production calls. Report `|D−S|/|D|` as the observed static-coverage gap, broken down by cause. Report `S−D` as not exercised, not as static false positives. Record tests passed/failed/skipped, executed-function coverage, workers/processes instrumented and environment. For Celery, a broker-free/eager test run cannot characterize worker-process dispatch; retain that limitation and add an instrumented worker integration run separately.

## 3. Ordered test plan: method, oracle, metric and threshold

The thresholds below are proposed engineering gates, not probabilities implied by IBWD's confidence scores. The tier numbers 0.95/0.90/etc. are heuristic scores until calibrated on held-out labels.

| Order | Test / method | Oracle / evidence | Metric and gate |
|---|---|---|---|
| 0 | Pin corpus, IBWD revision, source roots, model, CLI, dependency locks and task YAML before runs | Git + independent review + admission script | No missing SHA; full history; clean source; 100–600 production files; ≥200 nonmerge commits; every required task verified. Unknown blocks paid runs. |
| A | Compare all resolvable relations; stratify predictions by resolution tier, language, relation and naming ambiguity | Semantic occurrence classification and read source | Overall precision ≥95%; tier 1/2 precision ≥99%; uniquely named, in-scope CALLS recall ≥90%. Report macro and micro scores and denominators. |
| B1 | New value-reference fix: imports, local names, shadowing, aliasing, callbacks and JSX props | TS LS / SCIP + Python references | Precision ≥99%, recall ≥95% on supported value-reference subset; zero spurious shadowed matches and zero unsafe-deletion claims. Exact match on curated regression examples. |
| B2 | Named default export imported under a different name; self/super/base across files and multiple levels | Compiler binding / Python static plus runtime checks | 100% on curated real-code supported regressions. Anonymous exports and unresolved receivers reported as diagnostic categories. |
| B3 | Classify generated/vendor/minified files, including long handwritten lines | Source headers, provenance, tracked-file review | Zero reviewed legitimate-source exclusions; zero known generated/vendor fixture inclusions. Long lines alone never justify exclusion. |
| B4 | Edit body, change imports, rename export, edit base method, delete/recreate file, change alias config; compare incremental to fresh scan | Canonical node/edge snapshot | Exact equality after each mutation; no dangling/stale edges. Five timed repetitions per edit; median full/incremental ≥3× per repo for Sprint 4 readiness. ≥4× is the stretch target. |
| C | Every function: compare no-CALLS and no-(CALLS or REFERENCES) sets separately | Complete static inventory plus dynamic observations | Inventory every mismatch; zero false “safe to delete” outputs. Every unexplained in-scope miss must be triaged before proceeding. |
| D | Depth 1–5 closures in both directions; cycles; diamond paths; source=target; missing node; disconnected and reverse-disconnected pairs | Independent Python BFS/Dijkstra over adjudicated edges | Exact reachable sets; valid consecutive path edges; total cost matches minimum under declared weights; no silent truncation. |
| E | Hard-case matrix: barrels, star/relative/namespace/circular imports, type-only imports, multiple tsconfigs, duplicate names, decorators/properties/overloads, receivers and inheritance | Semantic static + executed examples where applicable | Every item has at least one labelled case per applicable language; exact correctness on supported cases, explicit uncertainty on unsupported cases. No fabricated high-confidence match. |
| F | Syntax errors, malformed encoding, huge files, symlink cycles/out-of-root links, minified code, fan-out; real or labelled synthetic 100/500/2,000-file corpora | Process status, diagnostics, resource profiler, oracle on unaffected files | No crash/hang/corrupt DB; bounded traversal and explicit partial-result metadata; zero silent loss in unaffected valid files. Suggested budget on a recorded reference machine: ≤120 s full scan and ≤2 GiB peak RSS at 2,000 files, subject to predeclared hardware adjustment. |
| G | One-factor ablations: tests on/off; REFERENCES on/off; trace_path allowed/disallowed for chains; limits 20/100/unlimited | Same frozen truth and task set | Publish accuracy, response bytes/tokens, calls and latency. Pagination or truncation must be visible; never count partial answers as complete. |
| H | Paid A/B only after A–G | Frozen YAML, strict structured grader plus source spot-check | ≥3 repeats per task/condition; median headline; primary ratio ≥3×; correctness no worse than baseline. Budget/time-limit exits count as failures and remain in logs. |
| I | Sprint 4 reuse check | History, test filenames, widely used symbol, reviewed no-static-use candidate, timing logs | ≥200 real nonmerge commits; separable tests; both candidates documented; incremental gate above. No claim of universal deletion safety. |

For A, inspect all high-tier errors. If sampling is necessary, predeclare random seed and sample sizes: target ≥300 predictions per high tier pooled across repos plus per-language reporting; “zero errors in 10 cases” is not evidence of 99% precision. Missing-edge recall cannot be assigned to a *predicted* tier because missing edges have no prediction: prelabel oracle strata by binding mechanism instead.

Stop rule: preserve the requested **≥2 distinct, adjudicated, in-scope production missing edges → STOP**. Count unique edges, not the same miss across three repeats. One missing edge that creates an unsafe-deletion statement, breaks a selected golden chain, or reveals an invalidation bug is already blocking. The hand-checked golden tasks require exact expected sets/path validity; population recall thresholds do not authorize misses on golden tasks. Unsupported dynamic edges in the prelabelled negative control do not trigger the ordinary static gate, but must appear in the failure inventory.

A low token ratio does not prove the cascade is broken. If correctness is clean, inspect startup overhead, graph verbosity, result duplication, task difficulty and the baseline's read volume. The old assertion that Q3 must have the largest ratio or traversal has a bug is not justified.

### Path semantics that must be fixed in the protocol

Your current `sum(1/confidence)` objective is a confidence-and-hop-count tradeoff. It is **not** equivalent to maximizing the product of edge confidences. For a max-product objective, minimize `sum(-log(confidence))`; those confidence values still are not calibrated probabilities. Keep the existing objective for Sprint 3 if compatibility matters and test exactly that objective.

Use CALLS-only for a “call chain” question. CALLS+IMPORTS is a mixed dependency path and must be labelled accordingly. Mixed paths can be useful but do not establish runtime reachability. With multiple relations between a node pair, retain distinct edges or define a deterministic relation-aware projection; a plain DiGraph can overwrite edge attributes.

A zero heuristic gives Dijkstra. A cosine-distance A* heuristic does not automatically produce a more semantically relevant optimal path: admissible heuristics preserve the cost objective, and arbitrary heuristics may lose optimality. If semantic relevance is wanted later, make it an explicit cost or tie-break rule and validate it. [NetworkX A* documentation](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.shortest_paths.astar.astar_path.html).

## 4. Measured shortlist and qualification status

Counts below use tracked files and explicit roots, excluding conventional tests, docs/examples, declarations and recognised build/vendor paths. They are source-root counts, not assertions that every repository file is production code. cloc excludes some empty/unclassified files, so its file count can differ. Full manifests are in `*_audit.json`; counts do not include installed dependencies. All six checkouts were clean at audit time and had full history. Active means at least one commit within the preceding 180 days; this is a declared screening rule, not proof of long-term maintenance.

| URL | Pinned SHA | Language | Production files / cloc code lines | Nonmerge commits | Licence | Oracle |
|---|---|---|---:|---:|---|---|
| [sphinx](https://github.com/sphinx-doc/sphinx) | `b04a2101295ac3fb725b16111eda0284b6da4cca` | Python (+ JS assets) | 385 / 101,444 | 16,579 | BSD-2-Clause | scip-python + Jedi + runtime |
| [scrapy](https://github.com/scrapy/scrapy) | `fe30c1882a12651c19c5a94d71e9f14e442456c7` | Python | 190 / 22,267 | 9,372 | BSD-3-Clause | scip-python + Jedi + runtime |
| [redux-toolkit](https://github.com/reduxjs/redux-toolkit) | `c9dac937d77adc3bf04842a43955e81d0e7a46da` | TypeScript / JS | 118 / 15,650 | 3,483 | MIT | TypeScript LS + scip-typescript |
| [bulletproof-react](https://github.com/alan2207/bulletproof-react) | `9506629ed003a561c6627735480cce4994244bb4` | TSX / TypeScript | 321 / 15,008 | 220 | MIT | TypeScript LS + scip-typescript |
| [celery](https://github.com/celery/celery) | `e86ca9ee8d7d7f3558b7fcf0910120f60b9866c6` | Python; negative control | 161 / 28,035 | 12,340 | BSD-3-Clause | scip-python + Jedi + runtime |
| [typedoc](https://github.com/TypeStrong/typedoc) | `6d8c856bbb46b089371952981f113c3e318818fd` | TypeScript; reserve | 208 / 35,291 | 3,369 | Apache-2.0 | TypeScript LS + scip-typescript |

Recommended intended corpus: Sphinx, Scrapy, Redux Toolkit, Bulletproof React and Celery, **conditional on the outstanding semantic and artifact checks**. Sphinx covers layered Python and inheritance; Scrapy is the Python CLI/application orchestration candidate; Redux Toolkit covers TypeScript libraries/barrels and a monorepo; Bulletproof React covers TSX/hooks/aliases and multiple app configs; Celery is the deliberate dynamic-dispatch negative control. Scrapy itself uses callbacks and dynamic loading, so do not assume it is “mostly static”; measure that admission criterion.

TypeDoc is a useful reserve/diagnostic candidate but fails the literal no-committed-node_modules rule: the snapshot has tiny declaration/package test fixtures under `src/test/converter2/.../node_modules`. Do not silently waive that rule. Redux Toolkit has neighbouring `config.example.js` and `.ts` test configs: reading them showed separately authored CommonJS/JSDoc and typed ESM examples, not sufficient evidence of committed compiler output. Keep their provenance review explicit before admission. Broad path-pattern detection alone cannot decide this.

Other screening results, not additional shortlist recommendations: Reflex had 606 files under `reflex` plus `packages`, above the ceiling; oclif/core had 80 under `src`; tsoa had 70 under `packages` and its sampled head was older than the 180-day activity gate. Their audit files preserve the rejections. Do not pad counts with tests/configs to make them qualify.

Two Sphinx “generated” header candidates were false positives: prose/output templates mentioned generation. This is concrete evidence that generated-word or line-length heuristics need review. The accompanying auditor flags these candidates; it does not automatically remove them.

### Proposed questions, with exact definition locations

These locations were obtained from the pinned source trees. **They are candidates, not certified ground truth.** A symbol existing at a location does not certify all its references, its orchestrator fan-out or the uniqueness of a path. The discovery scripts intentionally report `complete: false`; do not use them as the final oracle.

| Repo | Q1 candidate | Q2 candidate | Q3 candidate chain | Status |
|---|---|---|---|---|
| sphinx | `terminal_supports_colour` — `sphinx/_cli/util/colour.py:22` | `_parse_command` — `sphinx/_cli/__init__.py:234` | `Sphinx.__init__` — `sphinx/application.py:165` → `Config.read` — `sphinx/config.py:339` → `_read_conf_py` — `sphinx/config.py:565` → `eval_config_file` — `sphinx/config.py:585` | Definition locations checked; semantic sets/path UNVERIFIED |
| scrapy | `build_component_list` — `scrapy/utils/conf.py:20` | `AddonManager.load_settings` — `scrapy/addons.py:25` | `execute` — `scrapy/cmdline.py:169` → `get_project_settings` — `scrapy/utils/project.py:112` → `init_env` — `scrapy/utils/conf.py:89` → `get_config` — `scrapy/utils/conf.py:106` | Definition locations checked; semantic sets/path UNVERIFIED |
| redux-toolkit | `getOrInsertComputed` — `packages/toolkit/src/utils.ts:103` | `generateApi` — `packages/rtk-query-codegen-openapi/src/generate.ts:224` | `generateApi` — `packages/rtk-query-codegen-openapi/src/generate.ts:224` → `generateReactHooks` — `packages/rtk-query-codegen-openapi/src/generators/react-hooks.ts:85` → `getReactHookName` — `packages/rtk-query-codegen-openapi/src/generators/react-hooks.ts:43` → `getOverrides` — `packages/rtk-query-codegen-openapi/src/generate.ts:216` | Definition locations checked; semantic sets/path UNVERIFIED |
| bulletproof-react | `getDiscussionQueryOptions` — `apps/react-vite/src/features/discussions/api/get-discussion.ts:15` | `DiscussionsList` — `apps/react-vite/src/features/discussions/components/discussions-list.tsx:19` | `DiscussionRoute` — `apps/react-vite/src/app/routes/app/discussions/discussion.tsx:38` → `useDiscussion` — `apps/react-vite/src/features/discussions/api/get-discussion.ts:27` → `getDiscussionQueryOptions` — `apps/react-vite/src/features/discussions/api/get-discussion.ts:15` → `getDiscussion` — `apps/react-vite/src/features/discussions/api/get-discussion.ts:7` | Definition locations checked; semantic sets/path UNVERIFIED |
| celery | `get_implementation` — `celery/concurrency/__init__.py:41` | `AMQP.as_task_v2` — `celery/app/amqp.py:327` | `AMQP.as_task_v2` — `celery/app/amqp.py:327` → `saferepr` — `celery/utils/saferepr.py:66` → `_saferepr` — `celery/utils/saferepr.py:158` → `_safetext` — `celery/utils/saferepr.py:107` | Definition locations checked; semantic sets/path UNVERIFIED |
| typedoc | `resolveAliasedSymbol` — `src/lib/converter/utilities/symbols.ts:3` | `buildNav` — `src/frontend/typedoc/Navigation.ts:29` | `initNav` — `src/frontend/typedoc/Navigation.ts:21` → `buildNav` — `src/frontend/typedoc/Navigation.ts:29` → `showPage` — `src/frontend/typedoc/Application.ts:140` → `scrollToHash` — `src/frontend/typedoc/Application.ts:148` | Definition locations checked; semantic sets/path UNVERIFIED |

The Python discovery pass found at least three Q1 and three Q2 syntactic candidates, plus three-edge chains crossing at least one file boundary, in Sphinx, Scrapy and Celery. The TS pass rolls anonymous/nested calls up to the enclosing indexed symbol for candidate discovery; the final oracle must retain original ownership too. In the selected Bulletproof Vite config it found seven Q1 candidates, one qualifying Q2 orchestrator and a candidate Q3 chain. It has therefore **not established the required three Q2 orchestrators**. Redux Toolkit's candidate counts are recorded in its audit; all semantic qualifications still require independent review. A chain crossing files does not require every hop to cross a different file. No repository has been declared fully admitted from the partial passes.

Bulletproof has 321 production files across three similar app variants. Use the Vite app for the primary React questions and the other variants for config/alias stress testing. Never treat three copies of equivalent implementations as independent repositories or pool all their `@/` aliases into one TypeScript program. Configs explicitly include `@/*` mappings; source inspection confirmed hooks and colocated tests.

For each admitted repo, select ≥3 verified Q1 functions and ≥3 verified Q2 orchestrators before choosing the paid subset. Q1 requires distinct production caller files, not occurrence count. Q2 requires at least three distinct internal callees outside the caller's module. A three-edge Q3 chain has two intermediates; decide separately whether it must be the shortest/unique chain. Recommend requiring a valid three-edge cross-file chain and accepting any equally optimal path under the declared objective, not demanding uniqueness.

## 5. Reproducible verification tooling and oracle commands

The kit contains:

- `verify_repo.py`: Git SHA/history/cleanliness, scoped file classification, cloc, tracked artifact/symlink flags, source header review candidates and graph-structure checks from normalized edges.
- `check_admission.py`: PASS/FAIL/UNKNOWN checks for every admission category; missing semantic evidence blocks admission.
- `discover_python.py`, `discover_ts.cjs`: candidate discovery only, deliberately incomplete. TS supports an optional leaf tsconfig argument; Python does not implement a full lexical/type resolver.
- `ts_references.cjs`: machine-readable TypeScript Language Service reference positions, JSX-closing labels, diagnostics and source-line hashes.
- `python_references.py`: Jedi project references and source-line hashes.
- `trace_python.py`: executed Python call edges while pytest runs, with explicit process/thread scope.
- `compare_graphs.py`: exact edge precision/recall, relation/tier breakdowns and false-no-static-use inventory.
- `ground_truth_template.yaml`, `review_template.json`: evidence contracts; empty values intentionally block admission.
- Audit and candidate JSON files, full SHA lock, and tool dependency lock.

**Automation boundary:** no script can prove every “safe to delete” or dynamic-dispatch claim on arbitrary Python/JS. Generated-vs-handwritten provenance, permissive-license exceptions and semantic source adjudication also need evidence. The kit automates measurable checks and evidence gates; it does not falsely advertise universal automatic semantic proof. The TS/Jedi exports are occurrence sets; CALLS/IMPORTS/INHERITS/REFERENCES normalization and the IBWD export adapter still need integration with your actual graph schema. That schema/source checkout was not attached, so no fabricated SQLite queries are included.

Run tools outside the baseline snapshot, with evidence output in a sibling directory. Example commands assume Bash/WSL/Linux/macOS and these variables:

```bash
KIT=/absolute/path/ibwd-sprint3-kit
CORPUS=/absolute/path/ibwd-test-corpus
EVIDENCE=/absolute/path/sprint3-evidence
mkdir -p "$EVIDENCE"
python3 "$KIT/verify_repo.py" "$CORPUS/sphinx" --roots sphinx --out "$EVIDENCE/sphinx_audit.json"
python3 "$KIT/verify_repo.py" "$CORPUS/scrapy" --roots scrapy --out "$EVIDENCE/scrapy_audit.json"
python3 "$KIT/check_admission.py" "$EVIDENCE/sphinx_audit.json" "$EVIDENCE/sphinx_review.json"
```

For fresh clones, use the exact URL and SHA from `repos.lock.json`, perform `git checkout --detach SHA`, and verify `git rev-parse HEAD` plus `git status --porcelain`. Do not use a shallow clone when measuring total history. Never pull during the experiment.

### TypeScript / JavaScript

Install the oracle tools in a separate tooling directory. The delivered lock pins the tested TypeScript JS API to 5.9.3 and cloc to its measured version. The npm `typescript` package resolved to 7.0.2 in this environment and did not expose the old JS compiler API, so unversioned installation is not reproducible. The supplied JS scripts were smoke-tested with 5.9.3; use a compatible repo-pinned API version for final indexing and record any version mismatch.

```bash
mkdir -p "$EVIDENCE/oracle-tools"
cd "$EVIDENCE/oracle-tools"
npm init -y
npm install --save-exact typescript@5.9.3 @sourcegraph/scip-typescript @sourcegraph/scip-python
# Keep package.json and package-lock.json; subsequent installs must use npm ci.
./node_modules/.bin/scip-typescript --version
./node_modules/.bin/scip-python --version
export PATH="$EVIDENCE/oracle-tools/node_modules/.bin:$PATH"
export NODE_PATH="$EVIDENCE/oracle-tools/node_modules"
```

Install repository dependencies using that snapshot's lockfile. Redux Toolkit's inspected root declares `pnpm@11.23.0`; use its recorded package manager and `pnpm install --frozen-lockfile` in an oracle checkout. Bulletproof's app directories have their own dependency setup; install and index each app independently, not via a synthetic combined alias map.

```bash
cd "$CORPUS/redux-toolkit"
pnpm install --frozen-lockfile
scip-typescript index --pnpm-workspaces
# For a standalone TS leaf project:
cd "$CORPUS/bulletproof-react/apps/react-vite"
yarn install --frozen-lockfile
scip-typescript index
# JS without a tsconfig: scip-typescript index --infer-tsconfig
```

SCIP commands follow the current official README, which also documents supported Node versions and workspace options. No upload to a Sourcegraph service is needed to retain local index files. [scip-typescript setup](https://github.com/sourcegraph/scip-typescript).

An exact reference query for the pinned Vite helper is:

```bash
node "$KIT/ts_references.cjs" "$CORPUS/bulletproof-react" \
  apps/react-vite/tsconfig.json apps/react-vite/src/utils/cn.ts 4 13 \
  > "$EVIDENCE/bulletproof-cn-references.json"
```

Confirm the chosen column is inside the target identifier, inspect diagnostics, and review source lines before certifying a task. Iterate every indexed definition for the every-function sweep, merge per-config references by canonical file/range and preserve project identity. The supplied reference exporter is a per-symbol building block, not a precomputed whole-repo golden graph. [TypeScript Language Service API](https://github.com/microsoft/TypeScript/wiki/Using-the-Language-Service-API).

### Python static and executed references

Use each repository's compatible Python version; the inspected Sphinx head requires Python ≥3.12. Create separate environments rather than resolving every project into one environment.

```bash
cd "$CORPUS/sphinx"
uv sync --locked --group test
# Activate this project's environment for scip-python's pip environment discovery.
. .venv/bin/activate
python -m pip install jedi pytest  # if not supplied by the locked test environment; record freeze
scip-python index . --project-name=sphinx
python "$KIT/python_references.py" "$CORPUS/sphinx" \
  sphinx/_cli/util/colour.py 22 8 > "$EVIDENCE/sphinx-colour-references.json"
python "$KIT/trace_python.py" "$CORPUS/sphinx" "$EVIDENCE/sphinx-runtime.json" -- tests -q
python -m pip freeze > "$EVIDENCE/sphinx-python-freeze.txt"
```

If uv created a pip-free environment, use `uv pip install --python .venv/bin/python pip jedi` first and record the resulting environment. scip-python uses the activated environment's installed packages; it also supports an explicit environment manifest. [scip-python setup](https://github.com/sourcegraph/scip-python).

For Scrapy, use its `tox.ini` test environment rather than assuming `pip install -e .` installs test dependencies. For Celery, inspect/install `requirements/test.txt` and run unit tests first; broker/worker integration has additional services. These project setup commands were inspected but not executed here:

```bash
cd "$CORPUS/scrapy"
python -m pip install tox
python -m tox list
python -m tox run -e py312 --notest
# Run the tracer using the interpreter inside that created tox environment,
# preserving tox's required environment variables; record the exact command.

cd "$CORPUS/celery"
python -m venv .venv
. .venv/bin/activate
python -m pip install -e . -r requirements/test.txt jedi
scip-python index . --project-name=celery
python "$KIT/trace_python.py" "$CORPUS/celery" "$EVIDENCE/celery-runtime-unit.json" -- t/unit -q
```

Do not silently replace a failed real test suite with a tiny fixture and claim runtime coverage. Keep failures/skip counts. `python_references.py` uses documented project-scoped Jedi references, with one-based lines and zero-based columns. [Jedi API](https://jedi.readthedocs.io/en/latest/docs/api.html).

### Normalize and grade

Write per-repo YAML before agent runs. Each expected item includes symbol ID, callsite and definition positions, relation, repo SHA, source blob/hash and human verification status. Retain diagnostic errors from the oracle. Normalize full oracle/IBWD exports into the documented JSON shape, then:

```bash
python "$KIT/verify_repo.py" "$CORPUS/sphinx" --roots sphinx \
  --edges "$EVIDENCE/sphinx-oracle.json" --out "$EVIDENCE/sphinx_audit.json"
python "$KIT/compare_graphs.py" "$EVIDENCE/sphinx-oracle.json" \
  "$EVIDENCE/sphinx-ibwd.json" > "$EVIDENCE/sphinx-comparison.json"
```

Use a JSON answer schema for agent tasks and exact set comparison. The original regex grader can match the wrong line number and does not enforce precision; replace it. For no-reference questions use explicit empty sets and an uncertainty statement, not a `safe_to_delete: true` field. For Q3 check every edge, endpoint, relation and cost; allow equally optimal alternate paths. Source evidence hashes detect stale line references after mutations.

## 6. Paid-run protocol and cost estimate

Baseline contains only Read/Glob/Grep and no IBWD data or MCP connection. IBWD condition has those tools plus exactly the four Sprint 3 graph tools; graph scanning happens before agent sessions. A known, unambiguous symbol task should need one graph query; record symbol-resolution calls separately rather than concealing them. End-to-end questions with ambiguity may legitimately need more than one call.

Use separate isolated snapshot directories/containers per condition and repeat. Baseline must never contain `.ibwd`, generated ground truth or prior answers. Merely creating sibling directories does not restrict absolute-path reads: use a filesystem sandbox/container exposing only that condition's checkout. Keep evidence outside the agent-readable mount. Do not rely only on a prompt telling baseline not to read it.

The current CLI documentation distinguishes `--tools` (built-in tool availability) from `--allowedTools` (permission grants). MCP needs separate restriction. Use `--strict-mcp-config`, an empty MCP config for baseline, and a config exposing only allowed read-only graph tools for IBWD. Record and inspect the actual advertised tools in the startup transcript. [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference).

Example baseline command, after validating the installed CLI version:

```bash
claude --version
claude -p "$QUESTION" --model "$PINNED_MODEL" \
  --tools "Read,Glob,Grep" --allowedTools "Read,Glob,Grep" \
  --disallowedTools "mcp__*" --strict-mcp-config \
  --mcp-config '{"mcpServers":{}}' \
  --output-format stream-json --verbose --no-session-persistence \
  --max-budget-usd 1.00 > "$RUN_LOG"
```

For IBWD, remove the blanket MCP deny, point to its dedicated config, grant the four graph-tool names explicitly, and deny any other exposed MCP tools or filter them server-side. Keep built-ins restricted. Include `ibwd_trace_path` for primary Q3 and remove it only in the separate ablation. Do not assume `--tools` filters MCP. Do not use `--continue`/`--resume`. A fixed full model ID and effort setting must match across conditions; log effective model/fallback events.

Randomize/balance condition order using a fixed seed. Fresh sessions do not guarantee cold provider caches; record cache usage and distinguish warm/cold runs. Preserve raw stream-json, final result, latency, exit status, stdout/stderr, model, CLI version and tool-use events; count unique tool-use IDs, not text mentions. Paid harness integration remains work to run against your installed CLI; the supplied kit does not pretend those sessions were executed.

Required CSV columns:

`repo,repo_sha,task_id,condition,repeat,tokens_in,tokens_out,total_context_tokens,cost_usd,tool_calls,correct,notes`

Also retain `ibwd_sha,model_id,cli_version,cache_creation_tokens,cache_read_tokens,latency_s,exit_code,stratum,oracle_hash,raw_log_path`. Use one row per repeat, including failed runs.

Cost model from your uploaded ten-row CSV: observed mean **$0.096/session**, range **$0.0395–$0.1581**. These are historical observed costs, not a current API price quote. Larger repositories can cost more.

| Design | Sessions | At observed mean | Planning allowance at $0.10–$0.30/session | $1/session nominal cap |
|---|---:|---:|---:|---:|
| 4 repos × 3 tasks × 2 conditions × 3 repeats | 72 | $6.91 | $7.20–$21.60 | $72 |
| 5 repos × 3 tasks × 2 conditions × 3 repeats | 90 | $8.64 | $9–$27 | $90 |
| 6 repos × 3 tasks × 2 conditions × 3 repeats | 108 | $10.37 | $10.80–$32.40 | $108 |
| Preferred expanded: 5 repos × 6 tasks × 2 conditions × 3 repeats | 180 | $17.28 | $18–$54 | $180 |

The six-task design uses Q1/Q2/Q3 plus a small-function question, a no-static-use question and a labelled hard-case question. Adjust negative-control tasks honestly rather than forcing ordinary static questions. An optional six-case ablation suite across all four factors with two levels and three repeats adds at most 144 sessions before reuse of matching primary conditions. Run all graph-level ablations free first; only fund agent ablations that answer a remaining question. Treat CLI budgets as stop controls, not an absolute invoice guarantee at arbitrary request boundaries; enforce a batch-level cap too.

Free-first order: metadata/admission → environment and independent oracles → all source evidence → correctness/regression/robustness → graph ablations → golden YAML freeze → CLI/tool isolation checks → a small paid pilot validating logs → full balanced paid runs → locked analysis/verdict. A real-repo install/index failure is logged as a blocked oracle, not silently replaced with LLM guesses.

## 7. Sprint 4 blockers and the three decisions

| Decision | Recommendation now | Carry-forward boundary |
|---|---|---|
| Test files in graph | Include tests as indexed symbols with `is_test`/scope metadata; default production queries filter them; impact queries can include them | Production-only Sprint 3 benchmark is acceptable if explicit. Excluding tests entirely must be resolved before Sprint 4 promises test impact. `TESTED_BY` alone cannot reconstruct omitted call paths. |
| Token metric | Cumulative processed tokens is the primary gate; output, tool-result size, cost and latency are secondary | A clean but <3× result remains STOP under the stated gate. Output-only success can be reported as a narrower finding, not silently substituted. |
| Nested functions | Keep Sprint 3's enclosing-symbol projection, preserve origin metadata and document it; do not expand the symbol model only to inflate recall | Add named nested symbols in a later version if measurements show important lost navigation/impact. They become blocking now if rolled-up calls disappear, shadowing corrupts resolution or any unsupported case is presented as certain. |

Blocking now: invalid ground truth, missing corpus coverage, gold-task wrong answers, high-confidence spurious edges, unsupported safe-deletion claims, ≥2 unique in-scope missing edges, incremental/fresh inequality, stale config invalidation, fabricated paths, silent output truncation, or failure of the declared efficiency gate.

Can carry with explicit limitations: unobserved/unresolvable runtime dispatch in the negative control; anonymous exports/barrels/receivers outside the declared supported contract, provided ordinary production questions are not quietly excluded after failures; a named nested-function index if enclosing projection is correct; semantic A* enhancements. If a limitation dominates the intended workload, narrow the product claim before benchmarking or fix it—documentation alone does not make the capability pass.

Sprint 4 readiness blockers distinct from Sprint 3's core graph gate: unavailable test linkage, insufficient history and per-repo incremental speed below the predeclared ≥3× target. A correct small graph can finish its structural checks while still being unready for the next sprint's promised impact workflow.

Recommended immediate fix to the experiment: **make the harness enforce tool availability and produce exact structured answers against normalized, relation-aware oracle edges.** That removes two known measurement defects before spending more on the resolution cascade.

Current verdict, based only on evidence actually obtained:

- Headline 3× token gate: **NOT EVALUATED on a qualified corpus**.
- Correctness IBWD vs baseline: existing CSV marks both correct, but no new independent IBWD comparison was possible here.
- **Decision: STOP Sprint 4 progression; complete corpus admission and oracle-backed Sprint 3 validation.**
- Top measured issue in this work: incomplete corpus/task qualification; no new IBWD missing-edge count has been measured.
- Next concrete action: qualify the outstanding orchestrator/reference/chain cases with leaf-project oracles, then freeze YAML and run the free graph comparison suite.

## Validation performed on the kit

Python scripts compiled successfully. A synthetic TypeScript fixture verified imported aliases, a call reference and a value reference with no compiler diagnostics. Jedi found the expected Python reference. The profiling wrapper observed the expected caller→target edge while its one-test fixture passed. A graph comparison fixture with one false positive and one false negative returned precision=recall=0.5. Admission returned STOP for missing review evidence. These are tooling smoke checks, not IBWD or real-repository benchmark results.
