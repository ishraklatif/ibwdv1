# Sprint 5 — scoped impact and test evidence

Implemented 2026-09-26. Local deterministic engineering only; no model jobs, dependency downloads or benchmark sessions.

## Contracts

Production discovery, callers, dependencies and path queries retain their defaults and production-only resolution.
The staged SQLite generation also contains a separate source+test graph. Test definitions are accessible through
`ibwd_find_symbol` and `ibwd_list_symbols` with `scope="test"` or `scope="all"`, `response_version=2`.
Their returned identities and hashes work with `ibwd_read`. Existing context packets retain their production graph links.

`ibwd_impact` accepts up to 20 exact identities, names or file paths. File targets expand to their contained symbols.
Incoming paths show change exposure; outgoing paths show dependencies. Select CALLS, IMPORTS, INHERITS and REFERENCES,
a depth of 1–5, and output scopes source/test. Traversal may cross production intermediates when only tests are requested.
Every graph result includes a directed evidence path, static provenance and confidence scores, which are heuristic.
Only resolved syntax edges are followed. One shortest evidence path per seed/result is returned; this is not every path.

Test relevance comes from reference paths first, then labelled filename heuristics (`test_name`, `name_test`,
`name.test`, `name.spec`). Disable the latter with `heuristics=false`. Coverage is always `unknown`:
no runtime coverage artifact is ingested, and no reference proves execution, breakage, or safe deletion.

Results use schema version 2, generation-bound cursors, snapshot-specific hashes, a maximum of 200 items/page and
256–65536 byte budgets. Computation reuses the 2-second, 2000-node and 10000-edge traversal limits. Work limits explicitly
return truncated results without a cursor; narrow the query. Output limits support pagination without cutting evidence paths.
An indivisible path that does not fit fails explicitly. MCP dispatch verifies actual serialized response bytes.

## Local diff

Scan a baseline before editing. `diff=true` compares the previous indexed snapshot with the current working tree after
freshness refresh. Both old/deleted and new identities are included, alongside old and new impact paths. A changed file
conservatively contributes all its symbols, including changes to signatures. No-op scans retain the prior snapshot.

This is the last indexed change, **not a Git commit or PR comparison**. A subsequent changed scan advances the baseline;
it is not a durable task-start snapshot. On first indexing there is no prior baseline, so diff fails explicitly.
Historical file hashes identify old evidence; `ibwd_read` only reads current source and rejects those hashes after edits.
Old source bodies are not retained. Scanner exclusions and language limitations still apply.

```bash
ibwd scan --repo /path/to/project
# Make edits, then:
ibwd impact --repo /path/to/project --diff --max-bytes 65536
ibwd impact 'src/core.py::work' --repo /path/to/project --scope test --relation CALLS
ibwd impact 'src/app.py::run' --repo /path/to/project --direction outgoing --no-heuristics
```

## Optional installed TypeScript evidence

`ibwd_compiler_evidence` / `ibwd compiler-evidence` queries a symbol at a one-based UTF-16 line/column using the installed
TypeScript checker. The default compiler is the repository's `node_modules/typescript/lib/typescript.js`; an explicit
installed compiler path is supported. Node, compiler and project must already exist. No package manager runs.

```bash
ibwd compiler-evidence src/core.ts --line 5 --column 17 --project tsconfig.json --repo /path/to/project
```

The result preserves compiler version/digest, selected project/hash, program fingerprint, diagnostics, and source hashes.
References are labelled `SYMBOL_REFERENCE` / `possible`; they are never inserted as definite CALLS into the syntax graph.
Diagnostics produce `incomplete`; absent environments, symbols or project membership produce `unknown`.
There is a 20-second process deadline, 512 MiB V8 heap cap, 200000 AST-node visit cap, 200-reference cap, diagnostic
truncation flags and an output byte budget. Compiler startup/program construction has its own deadline, separate from
ordinary graph traversal. The compiler may read declaration dependencies outside the indexed scope; its program digest
includes them, but IBWD source reads remain limited to its inventory. It does not emit code or execute project scripts.

This sprint ships the TypeScript adapter. Python language-server/SCIP adapters and provenance-validated coverage ingestion
remain optional extensions. Anonymous default declarations, dynamic registration and nested symbol identities retain
existing syntax limitations; nested callback calls can be attributed to their indexed enclosing symbol.

## Verification

`tests/test_sprint5.py` covers production/test isolation, exact test source reads, paths and relation filtering,
transitive test relevance, filename heuristics, changed/deleted identities, absent baselines, no-op snapshot retention,
pagination, stale cursors, budgets, aliases, nested callbacks, value-based registration, and missing compiler environments.
An installed-compiler fixture covers typed receivers and incomplete imports; it skips if no local compiler is available.
Both generated client configurations exercise the new tools over real stdio MCP. Existing production resolution and
freshness tests remain regression gates. No new model-token savings, runtime coverage or broad precision claim is made.

The separate graph costs extra indexing work and storage: production plus current scoped and one prior scoped snapshot.
No device-wide performance claim is made. Test symbols are reparsed when the scoped graph rebuilds; no-op scans skip it.
