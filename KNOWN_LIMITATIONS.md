# Known limitations (call graph, Sprint 3)

The graph is a map built by reading syntax with tree-sitter — it never runs the code and has
no type information. Every item below is a place where the map can differ from the territory.
Each entry says what the limit is, **why it exists**, an example, what it does to answers, and
its status. Treat each as a hypothesis to keep testing, not a settled trade-off.

Status key: **Fixed** (code + tests), **Open** (documented, not fixed), **Inherent** (cannot be
fully fixed without running code), **Decision pending** (needs an owner's call).

| # | Limitation | Origin | Status |
|---|---|---|---|
| 1 | Test files are excluded from the call graph | Sprint 2 scope + a Sprint 3 choice | Decision pending |
| 2 | Functions nested inside other functions are not indexed | Sprint 2 scope cut | Open |
| 3 | Functions passed as values create no `CALLS` edge | Sprint 3 omission | Fixed (`REFERENCES`) |
| 4 | No dynamic-dispatch handling | Inherent to static analysis | Inherent |
| 5 | Edges rebuilt repo-wide on any source change | Sprint 3 design choice | Fixed (4–6.5x) |
| 6 | Default-export name mismatches unresolved | Sprint 3 omission | Fixed (named exports) |
| 7 | `self.method()` did not follow inheritance | Sprint 3 design choice | Fixed (via base classes) |
| 8 | Vendored / minified / generated files indexed as source | Sprint 1 ignore list | Fixed |

---

## 1. Test files are excluded from the call graph
- **What:** files classified as tests (`tests/`, `test_*.py`, `*.test.ts`, …) get no symbol nodes and
  contribute no `CALLS`/`IMPORTS`/`INHERITS` edges. `ibwd_callers(f)` therefore never lists a test.
- **Why:** Sprint 2 only extracted symbols for `kind == "source"` files (test fixtures also produced
  false hits for symbol lookup). In Sprint 3 this was kept on purpose so "callers" stays free of test
  noise, on the assumption that Sprint 4's test linkage (`TESTED_BY`) would cover tests. That choice was
  made without asking the owner.
- **Example:** `CallTracer.stop` in jonga is called from `jonga.py`, 3 example scripts and 1 test; only
  `jonga.py` is reported.
- **Effect:** callers/impact answers omit tests; a benchmark that counts test callers as ground truth
  will record them as "missing edges".
- **Plan:** decide after multi-repo results whether tests join the graph (probably as a separate,
  filterable relation) or ground truth is scoped to production code.

## 2. Nested functions are not indexed
- **What:** a function/closure/callback declared inside another function is not a symbol. Calls made
  inside it are attributed to the enclosing top-level symbol.
- **Why:** an explicit Sprint 2 scope cut (pinned by `test_nested_functions_are_not_indexed`). It keeps
  the symbol index small and avoids indexing every callback and lambda.
- **Example:** in a React component, `const onPickDate = () => …` inside `ConnectScreen` is not listed
  in `ibwd_dependents("ConnectScreen")`, although the compiler sees it.
- **Effect:** `dependents` misses inner helpers (measured: 11 of 13 for `App`). No cross-file
  dependency was missed, because calls inside inner functions still count for the enclosing symbol.
- **Plan:** left open. Indexing nested symbols would change what `find_symbol` returns and needs its
  own decision.

## 3. Functions passed as values create no `CALLS` edge
- **What:** `useReducer(fn, …)`, `component={Screen}`, `onPress={handler}`, `items.map(render)` use a
  function without calling it. Extraction only recognised call syntax, so no edge existed.
- **Why:** the Sprint 3 plan defines edges as calls, imports and inheritance; value uses were not
  considered until a real-repo comparison against the TypeScript compiler found one.
- **Example:** `reduceWithContext` had 0 callers but is used by `useKitScanner` — "safe to delete" was
  wrong.
- **Effect:** "no callers" could look like "safe to delete" when it wasn't (measured: 1 of 9 zero-caller
  claims on a real repo; 6% of function uses are value uses).
- **Fix:** a separate `REFERENCES` relation, so `CALLS` stays accurate. It is resolved conservatively
  (imports and same-module names only, never the loose unique-name fallback) and skips names shadowed by
  a local variable or parameter. `ibwd_callers` returns these labelled `relation: "REFERENCES"`.
- **Verified:** on a 155-function React Native + TypeScript app, checked against the TypeScript compiler:
  false "safe to delete" claims went from 1 to 0, and all 5 genuine value references were found (no false
  ones). A node that both calls and passes the same function appears once in `ibwd_callers`, labelled with
  one of the two relations.
- **Still true:** "no callers" is still not proof of safety — dynamic dispatch (item 4) and framework
  entry points (a Next.js page or `main` invoked by a runtime) have no static caller.

## 4. No dynamic-dispatch handling
- **What:** `getattr`, `importlib`, registries, decorators that register callbacks, dependency
  injection, `obj[name]()` and reflection are invisible to a syntax-only tool.
- **Why:** inherent — deciding the callee needs runtime values or full type inference.
- **Mitigation:** the confidence tiers (0.95 → 0.35) expose how sure a link is; ambiguous names are left
  unresolved rather than guessed.
- **Effect:** missing edges (false "no callers") in plugin-style code. Test on at least one
  metaprogramming-heavy repo to measure the size of the problem.

## 5. Edges rebuilt repo-wide on any source change
- **What:** resolving one file's calls depends on other files, so any source change re-derived every
  edge from every file.
- **Why:** correctness and simplicity first; the cost was not measured until Sprint 4 planning.
- **Measured before the fix:** a one-file rescan was only about 1.9x faster than a full scan at 100,
  400 and 1,200 files (Sprint 4 requires ≥ 3–4x).
- **Fix:** per-file references are cached in the `file_refs` table (keyed by content hash), so unchanged
  files are never re-parsed; only cheap in-memory resolution and a diff-based edge write re-run.
  `benchmarks/timing.py` reports the ratio.
- **Measured after the fix:** one-file rescan is 4.3x / 5.9x / 6.5x faster than a full scan at 100 / 400 /
  1,200 synthetic files, and about 4x on IBWD's own repo (best of 3). Tests assert that an incremental rescan yields exactly the same edges as a
  fresh scan after body edits, added/deleted files and `tsconfig` changes; **not yet covered:** import changes, export renames
  and base-class edits, and the five-repetition median timing procedure. Sprint 4 readiness is therefore not yet established. Import resolution now uses plain strings (the `pathlib`
  version was most of the remaining cost).

## 6. Default-export name mismatches
- **What:** `import Card from './Bar'` binds `Card` to the *default export* of `Bar.tsx`. Resolution
  matched by the local name, so it worked only when the names agreed.
- **Why:** the extractor never recorded what a file exports by default.
- **Fix:** the extractor records `export default <name>` (function, class, or identifier) per file, and
  default imports resolve through it.
- **Still open:** anonymous default exports (`export default () => …`) have no name to resolve, and a
  default re-exported through a barrel (`export { default } from './x'`) is not followed.

## 7. `self.method()` did not follow inheritance
- **What:** `self.step()` in a subclass resolved only within the subclass's own file/class, so a method
  defined on a base class (same or another file) fell through to the loose name tiers.
- **Why:** the plan's five tiers have no inheritance step; `INHERITS` edges existed but were not used
  for call resolution.
- **Fix:** if the method is not on the calling class, the resolved base classes are searched (depth
  bounded, cycle-safe); `super().method()` / `super.method()` search bases only. Inherited matches carry
  confidence 0.85.
- **Still open:** `obj.method()` on an instance variable does not know `obj`'s class (no type
  inference); it falls to the name-based tiers.

## 8. Vendored, minified and generated files indexed as source
- **What:** `vendor/`, third-party copies, minified bundles and generated code were parsed like source,
  polluting symbols and inflating fan-out (one repo: 1,776 "symbols", one "function" with 454 callees).
- **Why:** the Sprint 1 ignore list covered `node_modules`/`.git`/build caches but not `vendor/` or
  minified/generated files; it never mattered on IBWD's own repo.
- **Fix:** such files are classified `vendor` or `generated` (still visible to `ibwd_find_files`) but get
  no symbols or edges. Detection is by directory name, `.min.` / `.bundle.` names, an `@generated` /
  "DO NOT EDIT" header, or minified-looking content (very long lines).
- **Measured:** on a real repo that ships minified d3/mermaid bundles, symbols dropped from 1,776 to 35 and
  the largest fan-out from 454 callees to 5.
- **Still open:** a compiled `.js` sitting next to its `.ts` source in a different folder is not detected.

---

## Found by the first real-repo scans (Sprint 3 follow-up)

Scanning five real repositories (Sphinx, Scrapy, Redux Toolkit, Bulletproof React, Celery) exposed **seven** precision/recall
bugs that IBWD's own small repo could not show. All are fixed with regression tests; precision still needs measurement
against independent full-repository oracles.

- **Fuzzy tier over-matched.** The 0.35 tier compared names ignoring case *and underscores*, so `d.get()` matched
  `__get__`, `logger.warning()` matched the class `Warning`, and `cache.update()` matched `_update`: one symbol
  collected 127–235 false callers. **Fix:** fuzzy now requires the *same multi-word name in a different naming style*
  (`fetch_data` / `fetchData`), the same leading/trailing underscores, and never applies to single-word names.
- **Unique-name tier matched builtin method names.** `dict.pop()`, `re.findall()`, `dict.setdefault()` and
  `dict.items()` were linked to any repo method with the same name (100+ false callers each in Sphinx). **Fix:** a
  per-language list of ubiquitous method names (`items`, `get`, `pop`, `update`, `map`, `concat`, …) is not matched by name
  alone when the receiver isn't `self`/`super`. The suffix tier can still resolve them when the receiver is named like
  the class (`registry.get()` → `Registry.get`, 0.55).
- **Trade-off:** a genuine repo method that shares one of these names and is called on a receiver whose type can't be
  inferred is now unresolved (a missing edge) instead of guessed. A real example is Redux's `Tuple.concat`, which
  middleware chains call on `getDefaultMiddleware()`. Without types this is unknowable, so the graph favours precision.
- **Function-local imports leaked file-wide.** `from x import parse` inside one function shadowed a same-named
  module-level `parse` for every call in the file (Sphinx). **Fix:** imports carry their enclosing function's line range
  and only bind inside it.
- **Barrel re-exports were not followed.** `export * from './capitalize'` / `export { a as b } from './x'` left imports
  through `utils/index.ts` unresolved, and names defined in several packages could not be told apart (5 of 8 Redux Toolkit
  misses). **Fix:** re-export chains are followed (cycle-safe) when resolving an import.
- **Dynamic imports were not followed.** `const { initializeDb } = await import('./db')` and
  `lazy(() => import('./Page'))` now create import bindings. `import('./x').then(...)` is still not followed.
- **A tree-sitter-python grammar bug hid calls.** A lone starred list element, `[*g(x)]`, parses as `(*g)(x)` (and
  `[*self.f(x)]` as `(*self).f(x)`), so the call vanished and `g` looked like a value use. Worked around in the extractor.
- **`typeof fn` in a type position counted as a value use** (`QueryConfig<typeof getOptions>`), producing a spurious
  `REFERENCES` edge. Found by the TypeScript-oracle pilot; type-query subtrees are now skipped.
- **Recall vs the kit's syntax-only candidate oracles (CALLS, pair level, production scope), after the fixes:**
  Scrapy 703/703, Celery 618/618, Bulletproof React 82/82, Sphinx 1190/1191, Redux Toolkit 238/240 (12 misses before). Each
  of the three remaining misses was adjudicated individually against the source:

  | Candidate edge | Verdict | Evidence |
  |---|---|---|
  | Sphinx `parse_generated_content` → itself (`directive.py:120`) | **Oracle error** — dropped from the denominator | Line 118 imports `parse_generated_content` *inside* the function, so the call at 120 targets the imported function; IBWD's edge to `_directive.py::parse_generated_content` is correct. |
  | Redux Toolkit `retryWithBackoff` → `defaultBackoff` (`retry.ts:190`) | **Unsupported resolution case** — stays in the denominator | `options.backoff` is typed `typeof defaultBackoff` (l.134) with `defaultBackoff` as the default (l.138); the compiler resolves the call through the property's type. |
  | Redux Toolkit `raceWithSignal` → `noop` (`task.ts:36`) | **Unsupported resolution case** — stays in the denominator | `let cleanup = noop` (l.26), then `cleanup()` through the variable: local data flow. |

  **Both figures are reported:** broader semantic coverage 2831/2833 (99.9%); supported-scope recall 2831/2831, where the two
  unsupported cases are the declared "type/data-flow-resolved" category (`benchmarks/SPRINT3_gate_definition.md` §7; that
  category was named after seeing these misses, so the broader figure is the honest headline). These candidate graphs hold
  only import-map and same-module edges, so they measure recall of obvious edges, **not precision**. The oracle pilots (Jedi:
  6/6 use-sites; TypeScript language service: 4/4) validate examples, not whole-repository precision.
- **After the fixes (measured on the five repos):** the largest single-symbol caller counts are genuine hubs
  (`getLogger`, `useUser`, `nanoid`, `load_object`, `shared_task`); unique-name edges in Sphinx fell from 899 to 445.

## Other gaps found while writing this (not in the original list)
- **Barrel / re-export files:** now followed (see above), except `export { default } from './x'` and
  `export * as ns from './x'`.
- **`from m import *`** binds no names.
- **Bundler aliases** (Babel `module-resolver`, webpack `resolve.alias`) are not read; only
  `tsconfig`/`jsconfig` `paths`. Such imports stay unresolved rather than mis-resolved.
- **`tsconfig` `extends` of a package** (e.g. `"@react-native/typescript-config"`) is skipped; local
  `extends` is followed; only the nearest config applies.
- **Dynamic `import()` in `.then(...)` chains and `require(variable)`** are not followed.
- **Language coverage:** Python and JS/JSX/TS/TSX only.
- **Edge weights in `ibwd_trace_path`** use edge confidence only; there is no type information.
