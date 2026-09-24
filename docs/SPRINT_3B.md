# Sprint 3B — reliable, bounded retrieval

Implemented for the existing seven tools, with no model calls or added dependencies. Rerun project setup from your updated
IBWD installation and reconnect Codex/Claude Code to load revised schemas and navigation instructions. The shared skill now
uses automatic freshness and version-2 responses. Instructions guide model behavior; ordinary-work adoption and savings are
still unverified. Native Windows hook/lock support remains future work; the current implementation supports macOS/Linux/WSL.

## Publication and freshness

All MCP retrieval and scans use the same per-repository POSIX lock (30-second acquisition timeout). A scan builds an incremental
copy privately, validates file inventory and configuration fingerprints again, and publishes graph, manifest and generation
under the lock. A process killed during publication leaves an explicit stale marker; retrieval rebuilds before answering.
Readers never query a partially published index. Read-only doctor can report stale during publication and never repairs it.
Legacy Python database helpers remain low-level APIs; callers bypassing the service must coordinate their own access.
Process-interruption recovery is tested; power-loss durability across filesystem/hardware failures is not claimed.

Retrieval hashes the current readable inventory on every call (no freshness TTL or Git-clean shortcut), refreshes when needed,
and validates hashes/classification and resolution configuration again after querying. It retries once if evidence changes;
another change fails explicitly. This covers dirty/untracked files, deletions, branch switches, root/nested ignore changes,
ignored TypeScript configs and local `extends` dependencies. Excluded dependency/cache directories are pruned; symlinks are
excluded from the file inventory. Unreadable inventory entries fail instead of certifying a partial inventory as fresh.
The filesystem is not frozen: content can change after validation, so consumers should check emitted hashes before applying edits.
Continuous edits may prevent a result. Abruptly killed scans may leave private `scan-*` staging directories in `.ibwd/`.

## Response contract

Existing tool arguments and version-1 shapes remain supported. Version 1 now refreshes automatically and rejects oversized
answers/work instead of returning silently incomplete lists. Tool schemas expose these optional controls:

```json
{"name":"SomeFunction","response_version":2,"limit":50,"max_bytes":16384,"cursor":null}
```

Version 2 returns `schema_version`, `index_generation`, `scope`, `items`, `files`, `truncated`, `limit_reason`, `next_cursor`.
The `files` dictionary shares indexed source hashes across records. Symbol records include exact `symbol_id`, start `line`
and `end_line`; graph records retain all matching final-hop relations. Paths preserve all requested relations linking each hop.
Call-site ranges are not available in the current edge store and are not fabricated. Counts describe returned items; no global
total is claimed. Generation and complete query/filter arguments bind a cursor; a refresh or changed filter rejects it.
Explicit scans publish a new generation even if unchanged, invalidating previous cursors. Byte/page size may change between pages.

Discovery uses SQL limits and a progress deadline; graph queries use bounded layer expansion or Dijkstra with streamed adjacency.
Depth remains capped at five for callers/dependents. More than ten graph endpoint matches requires an exact identity or file filter.
Graph pages recompute the bounded result for the same generation; cursors do not retain mutable server traversal state.
Result/byte truncation produces a continuation cursor. Node/edge/time exhaustion discards incomplete graph layers and returns
`truncated: true` with no cursor; narrow the query or use source search. This is never a “no path” or “unused” conclusion.
SQL deadline exhaustion returns a clear query-budget error. Paths are indivisible: increase the byte budget to fit a complete path.

Budgets account for both MCP text and structured output plus receipt overhead; real SDK-2 stdio tests verify actual serialized
tool-result sizes and generation metadata. Serialization is checked again at dispatch. Budgets exclude the enclosing JSON-RPC
frame. Tiny budgets that cannot fit a valid envelope/item return an error. Existing scope limitations remain in
`KNOWN_LIMITATIONS.md`. Scan summaries are maintenance responses and do not use retrieval pagination.

## Verification profile

Frozen before measurements: deterministic local fixture, 100 Python source files, one source edit for refresh, five repetitions.
No models, network calls or downloads. Use the same limits on each laptop; record that device's actual measurements separately.

Acceptance limits for this small-fixture engineering profile: cold retrieval p95 <= 10 s, warm retrieval p95 <= 2 s,
one-file refresh p95 <= 10 s, two-client simultaneous retrieval p95 <= 15 s, process peak RSS <= 512 MiB.
These are initial regression ceilings, not performance promises for arbitrary repositories. Graph computation limits are
2 seconds, 2,000 visited nodes and 10,000 visited edges; responses default to 50 items and 16 KiB, with maxima of 200 items
and 64 KiB. Filesystem freshness checks and scan duration are measured separately from graph computation.

Measured on this available device (Darwin arm64, Python 3.13.9, five samples, nearest-rank p95):

| Workload | Median | p95 | Maximum process RSS |
| --- | ---: | ---: | ---: |
| Cold, including process startup | 608 ms | 818 ms | 89.94 MiB |
| Warm query | 8.66 ms | 9.77 ms | 88.59 MiB |
| One-file refresh and query | 28.66 ms | 36.61 ms | 89.91 MiB |
| Two simultaneous processes, including startup | 679 ms | 766 ms | 89.17 MiB each |

All preset ceilings passed. The profile measures Python service calls through the same locking/freshness path; independent
stdio MCP tests verify transport. Other OS/device profiles remain unmeasured; run the same script on each laptop. These small
fixture results do not establish large-repository latency, token savings or whole-system memory. Raw samples:
[`benchmarks/sprint3b_profile_darwin_arm64.json`](../benchmarks/sprint3b_profile_darwin_arm64.json).

```sh
python benchmarks/sprint3b_profile.py
python -m pytest -q tests/test_sprint3b.py tests/test_mcp_server.py tests/test_client_integration.py tests/test_incremental_equivalence.py
python -m pytest -q
```

The test suite covers two live MCP processes, killed publishers before/after database replacement, stale cursors, partial
responses, all existing incremental mutations versus fresh graphs, source changes during retrieval, ignored configs, branch
switches, dense cycles and serialized MCP byte budgets. The macOS sandbox-isolation test must run outside an enclosing sandbox.
