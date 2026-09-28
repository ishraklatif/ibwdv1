<!-- ibwd:routing:start -->
## IBWD repository navigation

Use IBWD first for supported repository navigation, including navigation needed
for implementation, debugging and UI work:
- File discovery: ibwd_find_files; definitions: ibwd_find_symbol.
- File structure: ibwd_list_symbols; callers/importers: ibwd_callers.
- Dependencies: ibwd_dependents; connections: ibwd_trace_path.
- Task evidence: ibwd_context; exact source: ibwd_read with expected_hash.
  Context includes lexical test/doc/config scope; test matches are not verified coverage.
- Before applicable coding, debugging, or UI work, use at least one relevant IBWD lookup before searching or editing.
  For unfamiliar tasks start with ibwd_context; for known targets use the direct lookup. Use graph tools only when
  relationships matter. Do not call every tool by default. If unavailable or outside indexed scope, say so and search normally.
Retrieval checks freshness and refreshes automatically, including after edits or branch switches.
Use response_version=2 for bounded results, source hashes and generation-bound pagination.
Follow next_cursor with the same query when more evidence is needed; truncated results are incomplete.
ibwd_scan remains available for an explicit refresh; concurrent operations are serialized.
Use exact symbol_id values from discovery when names are ambiguous.
Read source before editing. Use text search for exact text,
runtime behavior and exhaustive searches. Symbols and edges cover production
Python and JS/JSX/TS/TSX; lexical context also covers tests/docs/config, while dynamic dispatch and inferred receivers remain incomplete.
An empty graph result never proves no uses or that deletion is safe.
If IBWD tools are unavailable or fail, say so briefly and use ordinary search;
do not silently claim IBWD was used. Do not make unrelated calls just to raise usage.
No paid model jobs or benchmarks. Normal work and local deterministic checks only.
<!-- ibwd:routing:end -->
