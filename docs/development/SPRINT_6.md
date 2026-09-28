# Sprint 6 — optional local semantic retrieval

The optional adapter and retrieval path are implemented. Deterministic lexical/graph retrieval remains the default.
The layer stays off unless explicitly requested. A subsequently authorized [local quality/resource screen](./SPRINT_6_MEASUREMENT.md)
found no net vague-query recall gain and roughly 6.9-second semantic queries on this M1 device. The quality gate was not met.
Independent held-out validation remains unmeasured. Initial implementation used only deterministic tests; the later screen used
existing local weights and no paid calls.

## Use

The IBWD interpreter must already have the optional `sentence-transformers` runtime (declared by the `embeddings` extra),
and a complete local model directory must already exist. IBWD never installs dependencies or pulls weights.
The adapter uses [SentenceTransformer's local-files-only constructor](https://sbert.net/docs/package_reference/sentence_transformer/model.html),
with offline environment settings and remote model code disabled. Only the CPU backend is used.

```bash
ibwd semantic-index --repo /path/to/repo --model-path /path/to/local/model --dimensions 384
ibwd context 'where do we save durable state?' --repo /path/to/repo --semantic
ibwd semantic-config --repo /path/to/repo --enabled
```

Dimensions must match the installed model's native output. `--timeout` bounds indexing inference (default 120 seconds,
range 1–600). `--query-timeout` stores the query inference deadline (default 2 seconds, range 1–30).
Cold subprocess/model startup counts against the deadline; increase it explicitly if appropriate for the device.
The CLI returns embedded/reused chunk counts, elapsed indexing time, index bytes and provenance.
MCP clients can set `ibwd_context(..., semantic=True)` after reconnecting to load the updated schema.
There is no automatic model invocation during setup, scanning or exact discovery. Context calls follow the repository setting
when `semantic` is omitted; repositories remain deterministic until explicitly enabled. The setting lives in ignored local
`.ibwd/semantic-settings.json` and is not shared through Git.

Use the same semantic setting across pages. Cursors bind the source generation, semantic generation and query mode;
a transition to fallback rejects an earlier semantic cursor rather than mixing rankings. Restart without the cursor.
To disable persistently, use `ibwd semantic-config --repo /path/to/repo --disabled`. For one request, use `--no-semantic`
or MCP `semantic=False`. Explicit `--semantic` / `semantic=True` overrides a disabled repository setting.
The disposable `.ibwd/semantic.db` need not be retained.

## Storage and ranking

- Source and documentation chunks reuse the lexical evidence inventory and exclusions. Tests/config stay lexical.
  Chunks carry an exact source hash/range; vectors are never graph edges or evidence of correctness.
- Model inputs contain the path, a bounded declaration signature where available, and up to 4096 bytes of source.
  Total input is capped at 6144 UTF-8 bytes and the worker uses at most 512 model tokens, batches of eight and two CPU threads.
  Long inputs are prefixes; exact reads still return complete requested source ranges.
- Cache identity includes the input content hash, SHA-256 of local weights/configuration, dimensions and preprocessing version.
  Changed chunks are embedded; deleted chunks/vectors are removed. A no-op refresh makes zero embedding calls.
- A separate SQLite generation is fully built and source/model freshness checked before atomic replacement. Failure preserves
  the published generation. Incompatible model/dimension/preprocessing changes rebuild all vectors. Each explicit successful
  refresh publishes a new semantic generation, including no-op refreshes, so existing semantic cursors must restart.
- Queries require a matching graph generation and model digest. Stale data triggers explicit lexical fallback; queries never
  launch a rebuild. After source edits, run `semantic-index` explicitly to reuse unchanged chunks in a fresh generation.
- Reciprocal rank fusion (`1 / (60 + rank)`) combines lexical and cosine-vector ranks. Exact targets retain precedence.
  Existing bounded resolved graph expansion and hash-checked context reads run afterward. Scores are relevance heuristics;
  there is no cross-encoder reranker and no generated summary.

## Bounds and fallback

The optional store supports at most 4096 chunks, 2048 dimensions and 256 MiB on disk. Exceeding a build bound fails explicitly;
it does not publish an undisclosed partial index. Query vector candidates are capped at 200 and truncation is disclosed.
Ranking has a two-second work budget, model hashing a one-second budget per query check, and inference a subprocess deadline.
The packet retains the existing serialized MCP byte budget. Model memory has **no hard process memory cap**; CPU-only inference,
small batches and dimensions/chunk limits bound some costs, but do not establish a device memory budget.

Missing dependencies/weights, a stale/corrupt index, busy semantic lock, timeout, invalid vectors or changed weights produce
`semantic.status="fallback"` and a reason. A fresh non-paginated request continues through lexical/graph retrieval.
Indexing inference runs outside the repository freshness lock; ordinary retrieval remains usable during a slow build.
Optional query inference runs inside its freshness transaction, so it can delay other queries up to its query work deadlines.
Local weights must be an ordinary installed directory with file symlinks resolving inside it; external snapshot symlinks are rejected.
The offline settings are runtime configuration, not an OS security sandbox for arbitrary untrusted Python packages or model files.

## Verification and remaining acceptance

`tests/test_sprint6.py` uses deterministic vectors and a fake installed runtime in a real subprocess. It checks incremental reuse,
deletion, failed-build preservation, incompatible generations, exact precedence, vague-query candidates, scope filtering,
bounded pagination, stale cursors, corrupt/busy/missing models, invalid vectors, offline worker flags and CLI/MCP opt-in behavior.
These checks establish implementation contracts, not semantic model quality or savings.

For independent validation, freeze a held-out local relevance set before selecting a model. Report exact-name,
synonym, code-behavior and documentation recall@5 separately for lexical+graph and fused retrieval. The proposed gate is at least
10 percentage points improvement on vague queries without exact-lookup regression. Record cold/warm latency, indexing time,
peak memory and serialized packet bytes on the actual device. The subsequent local screen is recorded separately and does not
establish independent held-out or downstream task performance.
No reranker is justified until an evaluated candidate pool demonstrates a ranking gap.
