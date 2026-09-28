# Sprint 4 — lexical task evidence and exact source

Implemented without new dependencies or model calls. Reconnect the MCP server to load `ibwd_context` and `ibwd_read`.
Existing seven tools and version-1/version-2 contracts remain available. Rerun project setup to update installed routing
instructions and any tool allowlists. Existing indexes migrate on the next retrieval or explicit scan.

## Interfaces

```text
ibwd_context(task, targets=null, budget_tokens=2000, detail="outline", cursor=null,
             scopes=null, max_bytes=16384)
ibwd_read(symbol_id_or_path, expected_hash, range=null, budget_tokens=1000, max_bytes=16384)
```

Context uses explicit symbol identities, symbol names and file paths first, followed by SQLite FTS5 BM25 results with
camel/snake identifier splitting and deterministic file diversity. There is no mixture of incompatible numerical scores.
Scopes are `source`, `test`, `doc`, `config`; all are enabled by default. Tests/configuration are lexical evidence only,
separate from the production symbol graph. Exact known targets remain cheaper through the original discovery tools.

Packets include candidate locations, declaration outlines, exact bounded signatures for explicit symbols, hashes, headings,
definition identities and a small resolved incoming/outgoing graph neighborhood. Natural-language matches use the first
definition in each chunk as the graph seed; that identity is disclosed. Neighbor ranges and hashes permit further reads.
Test matches are verification pointers, not verified coverage. Ancestor AGENTS/AGENTS.override/CLAUDE paths and hashes are
included without rewriting their instructions or authority. Truncated definition/relationship lists are marked; use the
existing paginated symbol/graph tools for additional evidence.

`detail="source"` adds intact source spans. If a whole span cannot fit, the packet retains an outline and explicit read
reference instead of silently shortening source. Overlapping source bodies within a packet are emitted once; omitted
overlaps retain complete read references. Consumers can always expand the complete range. No cross-client/session omission
is performed. Outlines and signatures describe declarations, not complete compilable bodies.

`ibwd_read` requires the file hash returned by context or version-2 discovery. It returns the whole symbol/file, or an
explicit inclusive `[start_line, end_line]`. UTF-8 text and original line endings are preserved. Stale hashes, invalid ranges,
excluded paths and insufficient budgets produce errors. A file cannot be read merely by supplying an arbitrary local path.

```sh
python -m ibwd.cli context 'credential renewal' --repo /path/to/repo --scope source --scope test
python -m ibwd.cli context 'update authentication' --target 'src/auth.py::refreshAccessToken' --detail source
python -m ibwd.cli read 'src/auth.py::refreshAccessToken' --expected-hash HASH
python -m ibwd.cli read src/auth.py --expected-hash HASH --range 10 25
```

CLI emits compact JSON and accepts equivalent controls (`--target`/`--scope` repeat). Both interfaces use the same locked
publication, freshness checks and post-query validation as Sprint 3B. Source changes during expansion retry once; repeated
changes fail. Hashes must still be rechecked before editing because the filesystem is not frozen after a response.

## Bounds and storage

The actual serialized MCP result, including both SDK representations and receipt overhead, is bounded by
`min(max_bytes, 4 * budget_tokens)`. This is a byte-based token estimate, **not** a provider token count.
Token budgets are 512–16000; byte budgets are 256–65536. Too-small budgets fail explicitly.
Read validation errors are exposed through MCP with recovery guidance. An oversized read reports the required serialized
size (including reserved overhead) and effective budget. Raising `budget_tokens` alone cannot exceed the default
`max_bytes=16384`: increase both limits as needed, or request a smaller explicit range. Exact source is never truncated.
Reconnect an already-running MCP server after updating to load the improved error handling.

Context considers at most 200 candidates; each lexical query uses a SQL progress deadline inherited from the retrieval
service. Target lists are limited to 20 and query text to 4096 characters/32 unique lexical terms. Relationship and definition
lists have six entries per item. Result pages bind cursors to generation, task, targets, scope and detail; byte budgets may
change between pages. A candidate cap produces `truncated=true`, `limit_reason="candidate_limit"`, and no further cursor
after the available pool. Narrow the query or use source search; it is not an exhaustive absence claim.

FTS evidence is updated incrementally in the private staged graph database, then published in the same generation.
`evidence_files` records scope, hash and omission reason; `evidence_fts` stores searchable normalized text and exact line
locations. Chunks contain at most 32 lines, normally 4096 bytes, and break at Markdown headings. A long individual line indexes
only its bounded UTF-8 prefix; its read reference still identifies the complete line. Documentation headings are indexed as
search context. Unsupported source languages can have lexical evidence without structural graph coverage.

Ignored/symlinked files, vendor/generated scope, binary/non-UTF-8 files, files over 1 MiB and conservative secret filenames
are excluded from evidence. The filename exclusions cover `.env*`, key containers, common private keys, credential files and
credential directories; they are **not a general secret detector**. Existing inventory/graph scope is unchanged.

Packet cache keys include repository location (directory-local storage), generation/content snapshot, query, scopes, detail,
budget and retrieval version. At most 64 packets are retained under `.ibwd/context-cache`; every cache hit still passes the
freshness service. Cached evidence is returned fully to each client. No global service or model cache is introduced.

## Opt-in saved output reduction

```sh
python -m ibwd.cli reduce-output /path/to/pytest.log --format pytest --exit-code 1
python -m ibwd.cli reduce-output /path/to/tsc.log --format tsc --exit-code 2
```

This command only reads an existing log (maximum 8 MiB). It never runs or intercepts shell commands. It preserves the supplied
exit code in JSON and as its process exit status, reports failure counts when recognized, and keeps assertions, tracebacks,
source excerpts and unknown diagnostics. Pytest progress lines and TypeScript ANSI colors may be removed. Unrecognized
failures retain their full text with an unknown count. The original absolute log path always remains available.

## Verification and remaining acceptance evidence

`tests/fixtures/sprint4_tasks.json` freezes 32 retrieval cases before validation: Python/TypeScript, exact targets, split
identifiers, tests, docs/headings, configuration, empty and malformed-looking search text. Each checks evidence retention,
source expansion and cumulative serialized evidence size. Additional tests cover ambiguous identities, fan-out caps, source
deduplication, migration, incremental equivalence, cache invalidation, Unicode/CRLF, concurrent mutations, secret/ignored
files, byte budgets, CLI errors and both real client transports. Reducer tests are independent of graph retrieval.

```sh
python -m pytest -q tests/test_sprint4.py tests/test_sprint3b.py tests/test_client_integration.py
python -m pytest -q
```

No benchmarks were run under the repository's no-benchmark instruction. The roadmap's comparative 30% median payload target,
bounded-search baseline comparison and device latency/resource profile are **not established** by these tests. These remain
acceptance evidence to collect only when authorized; the features are implemented, but the performance exit criterion is
not claimed complete. Tiny exact lookups can cost less using the existing tools or `rg`. No model-token savings claim is made.
