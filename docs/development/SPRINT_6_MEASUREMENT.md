# Sprint 6 local semantic measurement

Measured on 2026-09-27 following explicit authorization to enable local semantic retrieval and measure it.
**The local screening quality gate failed:** vague-query recall@5 did not improve overall. Synonym gains were offset by
code-behavior regressions. Exact names remained first, but semantic queries cost about 6.9 seconds each versus 0.064 seconds
for the baseline. This supports selective experimentation, not a general-default or token-savings claim.

The user-requested opt-in is enabled for this checkout only. Other repositories remain off until explicitly enabled.
Use `ibwd context 'task' --no-semantic` to bypass it, or `ibwd semantic-config --repo . --disabled` to turn it off persistently.
Repository changes require an explicit `semantic-index` refresh; until then requests fall back to deterministic retrieval.

## Quality

24 predeclared queries, six in each category, ran against the same frozen 114-file source/documentation corpus (766 chunks).
The query labels and evaluation runner/results were excluded from that corpus. There was no model selection or ranking tuning
against these outcomes. This is local screening, **not independently adjudicated held-out validation**.

Recall@5 is the fraction of labelled relevant files appearing in the first five emitted evidence items, averaged per query.
Repeated chunks from one file do not earn extra credit. Labels can include multiple files. It is a file-level relevance metric,
not proof that the excerpt answers the question, not a completion-quality score, and not a token-savings measurement.

| Query category | Lexical + graph | Fused semantic | Change |
| --- | ---: | ---: | ---: |
| Exact names | 100.0% | 100.0% | 0.0 pp |
| Synonyms | 16.7% | 50.0% | +33.3 pp |
| Code behavior | 58.3% | 25.0% | −33.3 pp |
| Documentation | 91.7% | 91.7% | 0.0 pp |
| Vague subset: synonyms + behavior | 37.5% | 37.5% | 0.0 pp |
| All queries | 66.7% | 66.7% | 0.0 pp |

All six exact-name targets stayed first. All repeated top-five rankings were stable. All 48 semantic requests used the model
successfully, with **zero fallbacks**. The proposed +10 percentage-point vague-query improvement was not reached.
No reranker was added or evaluated.

## Resource measurements

Apple M1, MacBookPro17,1, 16 GiB RAM, macOS 26.6.2; Python 3.13.9. Existing cached
`sentence-transformers/all-mpnet-base-v2` weights, 768 native dimensions, CPU backend, two CPU threads, batch size eight.
The local weight directory totals 438,682,922 bytes. Runtime versions: sentence-transformers 6.1.0, transformers 5.17.0,
torch 2.14.0. Dependencies were installed for this evaluation; weights were copied from the existing cache, not downloaded.
The inference worker uses offline settings and local files only. No paid service was used.

| Measurement | Lexical + graph | Fused semantic |
| --- | ---: | ---: |
| Query median, all 48 requests per mode | 0.064 s | 6.903 s |
| Query p95, nearest observed rank | 0.100 s | 7.685 s |
| First request median | 0.096 s | 6.907 s |
| Repeated request median | 0.047 s | 6.900 s |
| Largest parent-process peak RSS | 30.7 MiB | 50.8 MiB |
| Largest embedding-worker peak RSS | No worker | 787.1 MiB |
| Median estimated serialized MCP packet bytes | 31,354 | 31,312 |
| Largest estimated serialized MCP packet bytes | 31,986 | 31,999 |

The standardized packet budget was 8,000 byte-estimated tokens / 32,000 estimated MCP bytes, large enough to inspect the
first five results. This is not the default 2,000-token budget and the byte metric includes both MCP representations plus
the existing overhead reserve; it is not a provider token count or a captured wire response.

The initial index took **209.78 seconds**, embedded all **766 chunks**, and produced a **13,361,152-byte** SQLite index.
Its worker peak RSS was **1,511,358,464 bytes** (about 1.41 GiB); parent peak RSS was 127,172,608 bytes.
A no-op refresh took **2.33 seconds**, reused every chunk, and made **zero embedding calls**.

Each case/mode used a fresh parent process and two consecutive requests; mode order alternated by case. The worker reloads
weights for every semantic query. “Repeated” therefore means OS-cache-warm operation, not a resident-model warm path.
No disk cache was flushed. The first index build overlapped the 8.4-second focused test run during startup; this was an ordinary
workstation, not an isolated performance environment. Timings are observational. Peak RSS comes from `getrusage`, reported
separately for parent and completed workers; it is not the simultaneous process-tree total.

Query inference used a 30-second deadline. The shipped two-second default would be too short for this measured adapter/model
combination. This checkout's semantic index is configured with the measured 30-second deadline; other repositories are unchanged.

## Artifacts and reproduction

- [Frozen queries and relevance labels](../../benchmarks/semantic_tasks.json)
- [Raw per-query measurements](../../benchmarks/results/sprint6_local_semantic.json)
- [Frozen corpus file hashes](../../benchmarks/results/sprint6_corpus_manifest.json)
- [Local evaluation runner](../../benchmarks/tools/evaluate_semantic.py)

Query-file SHA-256: `0ff1b7ac1b26df5a26217f9bffc1e16baec4afeff44c76a3f3d3c3fcd7f18237`.
Model digest: `e44f7740e1c734de818b06648e361f79991c622f3b4c64a8a69ae22ef3d5e5b4`.
The frozen corpus and weights remain locally under ignored `.ibwd/semantic-eval/corpus` and `.ibwd/models/all-mpnet-base-v2`.
The corpus captures the working tree used for screening; it is not a claim that the current edited checkout has identical hashes.

Explicitly authorized reruns can use:

```bash
.venv/bin/python benchmarks/tools/evaluate_semantic.py \
  --repo .ibwd/semantic-eval/corpus \
  --cases benchmarks/semantic_tasks.json \
  --model .ibwd/models/all-mpnet-base-v2 \
  --output .ibwd/semantic-eval/rerun.json
```

An existing compatible index makes the runner's first build incremental; use a new frozen snapshot with no semantic index to
measure full indexing again. Comparing different snapshots requires new corpus hashes. The runner neither downloads weights
nor changes repository opt-in settings. Portable held-out validation, resident-model performance and downstream agent outcomes
remain unmeasured.
