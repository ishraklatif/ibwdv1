# Sprint 3 — Call Graph & Dependencies (write-up)

**Status: built and tested; benchmark gate OPEN.** The ≥3x token target was met on 2 of 3 demo tasks on IBWD's own
repo and missed on the third (2.3x). Multi-repository validation is in progress
(`benchmarks/SPRINT3_protocol.md`, `SPRINT_3_FINISH_PLAN.md`).

## What was built

- **Extraction** (`scanner/references.py`, extended `python.py` / `javascript.py`): imports, call sites, base classes,
  JSX tags (`<Card />` counts as a call), value uses (`useReducer(fn)`), default exports.
- **Resolution** (`graph/resolution.py`, `graph/modules.py`): `IMPORTS` (file→file), `CALLS`, `INHERITS`, `REFERENCES`
  edges through the plan's 5-tier cascade — import-map 0.95, same-module 0.90, unique-name 0.75, suffix 0.55, fuzzy 0.35 —
  plus 0.85 for methods found on a base class. Resolves relative imports, Python source roots, JS/TS relative
  paths and `tsconfig`/`jsconfig` `paths` aliases (`@/…`). Ambiguous matches stay unresolved; external packages are dropped.
- **Traversal** (`retrieval/traversal.py`): recursive-CTE `callers_of` / `dependents_of` (depth ≤ 5, confidence = product
  of edge confidences) and a weighted shortest path (networkx A*, zero heuristic, edge cost = 1/confidence).
- **MCP tools** (`mcp/server.py`): `ibwd_callers`, `ibwd_dependents`, and — from the path-finding addendum —
  `ibwd_trace_path`. Callers accept a file path (results are its importers) and an optional `file` to disambiguate.
- **Scan pipeline** (`scan.py`): edges are rebuilt from a per-file reference cache (`file_refs` table), so a rescan
  re-parses only files whose content changed. `PRAGMA user_version` (edge-build version) forces a rebuild of graphs built
  by older logic; a `tsconfig` change alone also triggers one.

## Deviations from the plan (and why)

| Deviation | Reason |
|---|---|
| Pinned `tree-sitter < 0.26`; added `networkx` | 0.26.0 segfaults in `Node.text` under repeated parsing (Python 3.13). `networkx` pulled forward from Sprint 7 for path finding. |
| Test files excluded from the call graph | Sprint 2 only indexed `source` files; kept in Sprint 3 to avoid test noise (decision made without asking; see `KNOWN_LIMITATIONS.md` #1). |
| Tier 4/5 defined by me | The plan named them but not their semantics: suffix = receiver name resembles the class/module; fuzzy = the same multi-word name in another naming style (`fetch_data`/`fetchData`), exactly one hit (tightened after real-repo scans). |
| Extra tools/fields | Callers/dependents also follow `IMPORTS`/`INHERITS`/`REFERENCES`, take file paths and a `file` argument, and return `kind` and `relation`. |
| Depth capped at 5 | Path enumeration in the CTE grows with depth. |
| New modules instead of extending two files | `references.py`, `valuerefs.py`, `resolution.py`, `modules.py` keep extraction and resolution separable. |

## Tests

102 tests (up from 24): extraction (Python, JS/TS, JSX, value uses, default exports), each resolution tier, inheritance
and `super()`, tsconfig aliases (JSONC, `baseUrl`, local `extends`), vendored/generated classification, cache correctness
(incremental scan == fresh scan), traversal (depth 1–3, cycles, path preference), and the MCP tools end to end.

## Demo benchmark (IBWD's own repo; real `claude -p` sessions; output tokens)

| Task | Baseline → IBWD | Ratio | Tool calls |
|---|---|---|---|
| q1 callers of `upsert_edge` | 1536 → 308 | **5.0x** | 4 → 1 |
| q2 what `run_scan` calls | 1176 → 508 | **2.3x** | 2 → 1 |
| q3 trace `ibwd_scan` → `upsert_edge` | 1933 → 294 | **6.6x** | 5 → 1 |
| q4 reverse trace (no path) | 1950 → 817 | 2.4x | 6 → 4 (rerun: 2 calls, 2.5x) |

All answers correct. Cost of the whole benchmark ≈ $2.6 including investigations (raw data in `benchmarks/raw/sprint_3*`).

**q2 investigation** (5 repeats, plus a larger-function variant q2b × 3): q2 ratios 2.07–2.49x (median 2.24x, 0/5 ≥ 3x) —
a real miss, not noise. q2b was lower (median 1.52x). The grep-only agent solves these with one file read, and both agents
write a table of similar length, so output-token ratios plateau near 2x on small-function questions.

**Metric caveat, and where it stands:** total context tokens (input + output + cache) were ≈ 1.0x in these runs because
each session carried a large fixed overhead. A follow-up measurement (trivial prompt, empty folder, 4 configurations,
$0.14) showed that overhead was mostly an artifact of the harness: with only `--allowedTools`/`--disallowedTools` a session
costs **29,267 tokens** before any work, but restricting the built-in tool set with `--tools "Read,Glob,Grep"` cuts it to
**6,142** (−79%). `--bare` fails with an OAuth login ("Not logged in"). So **the previous overhead obstruction has been reduced; the gate remains untested.** The earlier ≈1.0x figure
should not be read as a property of IBWD. Startup usage was then measured for both actual benchmark configurations: baseline
**6,322** tokens, IBWD with the four Sprint-3 graph tools **8,362** (its schemas cost +2,040, charged to IBWD). The metric,
weights and scope are frozen in `benchmarks/SPRINT3_gate_definition.md`.

## Diagnosis on a real repo (React Native + TypeScript + Python, 155 functions)

Checked against the TypeScript compiler: callers/renderers matched 100% on 4 questions; a deep chain was confirmed
7/7 hops; **1 of 9 "no callers" claims was wrong** (a function passed as a value). The JSX-tag and `@/`-alias gaps found
here were fixed in Sprint 3 (before: 0 of 67 components used as tags had callers; 0 of 33 `@/` imports resolved).

## Limitations found and fixed after the demo

Documented in `KNOWN_LIMITATIONS.md`: value references (`REFERENCES` edges; false "safe to delete" 1 → 0), default-export
names, `self`/`super` through base classes, vendored/minified files (1,776 → 35 symbols on a real repo), and incremental
rescan speed (1.9x → 4–6.5x).
Still open: test files in the graph (decision pending), nested functions, dynamic dispatch.

## Real-repo scans (Sprint 3 follow-up)

Five real repositories (Sphinx, Scrapy, Redux Toolkit, Bulletproof React, Celery; pinned SHAs in
`SPRINT_3_FINISH_PLAN.md`) scan cleanly in 0.7–4.9 s each, and the one-file rescan cases measured (best of 3) are 5.3–16.2x faster than a full scan,
above the 3x threshold. Sprint 4 readiness is **not yet established**: it also requires the declared repeat procedure and
incremental/fresh equivalence for edits to imports, exports, inheritance, deletion and configuration (gate definition, §8). They exposed **seven** precision/recall bugs that IBWD's own repo could not: an over-loose fuzzy tier (127–235 false callers on one
symbol), builtin-method-name matches (`dict.pop` → a repo `pop`), function-local imports shadowing module names, barrel
re-exports, dynamic imports, a tree-sitter grammar quirk, and `typeof fn` in a type position counted as a value use. All are fixed
with regression tests (102 tests) and recorded in `KNOWN_LIMITATIONS.md`.

Recall against the kit's syntax-only candidate oracles (CALLS, pair level) is 2831/2833 adjudicated-valid edges (**broader
semantic coverage 99.9%**). The two misses are Redux Toolkit **unsupported resolution cases** (type/data-flow-resolved, real
references that stay in the denominator). A third candidate miss, Sphinx `parse_generated_content`, was adjudicated an **oracle
error** (a function-local import legitimately shadows the module-level name) and dropped from the denominator with evidence.
Supported-scope recall is 2831/2831. These candidates hold only import-map and same-module edges, so this is recall of obvious
edges, **not precision**; the 6/6 (Python) and 4/4 (TypeScript) oracle pilots validate examples, not whole-repository precision.

## Definition of done — status

| Item | Status |
|---|---|
| `CALLS` edges always populated with `confidence` + `source_type` | ✅ |
| Depth-2 traversal correct on a known fixture | ✅ (depths 1–3 tested) |
| Addendum: 3-hop path, clean "no path", prefers higher confidence | ✅ |
| All 3 demo tasks ≥3x token reduction, logged to `benchmarks/sprint_3_results.csv` | ❌ q2 = 2.3x; logged |

## Next

Multi-repo validation on Sphinx, Scrapy, Redux Toolkit, Bulletproof React and Celery (pinned SHAs in
`benchmarks/sprint_3_repos.lock` and the finish plan), with independent oracles, free checks first, then a paid A/B on a
pre-declared metric. Sprint 4 waits on that verdict.
