# Optional local assistance

The default remains deterministic. Embeddings and the local helper are separate opt-ins.
Neither setup nor configuration downloads models or runs inference. No API key or paid model is used by these features.
Existing Codex/Claude conversations still consume their normal provider allowance.

## Embedding queries

After building the semantic index with the commands in [daily use](./DAILY_USE.md), enable a resident query worker:

```bash
.venv/bin/python -m ibwd.cli semantic-config --repo "$PWD" --enabled --resident
```

This takes effect on subsequent context calls. Each MCP process keeps at most one CPU embedding worker;
it exits with the server and is killed on inference timeout. Use `--one-shot` to disable residency.
For cold startup, rebuild the index with `semantic-index --query-timeout 15` plus the existing model path/dimensions.
The configured deadline includes startup. Index building still uses the isolated batch subprocess.

Query vectors and model fingerprints are bounded process-local caches. Model identity includes file names,
device/inode, size, modification time and change time; altered weights are rehashed. A no-change scan preserves the
graph generation and semantic index. Actual indexed changes still require explicit `semantic-index` maintenance.
Semantic inference releases the graph lock, then validates the generation again before returning source evidence.

Inventory scans stream file bytes and reuse unchanged fingerprints based on filesystem metadata. They still walk
directories to detect additions, removals, renames and ignore changes. Returned source is hash-checked when read.
These are filesystem change checks, not a background watcher or a claim of constant-time retrieval.

## Local candidate selection

Use an already installed Ollama model served on a loopback IP. For example, when `qwen2.5-coder:7b` is installed:

```bash
.venv/bin/python -m ibwd.cli helper-config --repo "$PWD" \
  --enabled --model qwen2.5-coder:7b --timeout 10
.venv/bin/python -m ibwd.cli context "where is session usage collected" --repo "$PWD"
```

The default endpoint is `http://127.0.0.1:11434`. `--endpoint` accepts only HTTP loopback IPs, optionally with a port.
The model must be listed by the local server with a digest. Cloud model names and remote-model metadata are rejected.
Requests disable environment proxies and redirects. No fallback to a hosted model or automatic download occurs.
The helper uses Ollama `/api/tags` and schema-constrained `/api/chat`; see [structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

Context ranks up to 20 bounded candidate outlines. The model can only return existing integer IDs.
Exact targets remain first; unselected candidates remain available later in the packet/pagination.
Selection caches include the model digest, prompt version, task and source hashes. Invalid IDs, missing models,
timeouts and changed source generations trigger deterministic fallback. Status and elapsed time appear in the result.

```bash
.venv/bin/python -m ibwd.cli context "session usage" --repo "$PWD" --no-helper
.venv/bin/python -m ibwd.cli helper-config --repo "$PWD" --disabled
```

MCP `ibwd_context` supports `helper=false` for one-call bypass. For fully deterministic context, pass both
`helper=false` and `semantic=false`. Reconnect clients to discover the new `ibwd_local_assist` tool.

## Summaries, handoffs and logs

`ibwd_local_assist(kind, payload)` supports:

- `summary`: the same `{evidence, dependencies}` payload as `ibwd_artifact_save`. It selects existing cited extracts
  and saves an artifact. All original evidence/dependencies remain freshness dependencies, including omitted extracts.
  When the dependency limit prevents selection, the complete original artifact is retained. No prose claims are invented.
- `handoff`: the existing artifact handoff payload. The helper prioritizes unresolved questions; the goal, user decisions,
  changed paths, command observations and evidence are preserved. Commands are recorded, never executed.
- `log`: `{text, command, exit_code}`. Supply at most 64 KiB of output. It selects original lines, preserves detected
  error/failure/warning/location lines and nearby context, and retains the original under `.ibwd/local-helper/logs/`.
  Unrecognized diagnostics can be omitted; use the raw-log path and digest for complete output. Command/exit status are
  caller-reported. If mandatory diagnostics cannot fit the output budget, the tool asks for a larger budget.

For CLI use, provide an existing JSON payload file:

```bash
.venv/bin/python -m ibwd.cli local-assist log ./log-payload.json --repo "$PWD"
.venv/bin/python -m ibwd.cli local-assist summary ./summary-payload.json --repo "$PWD"
.venv/bin/python -m ibwd.cli local-assist handoff ./handoff-payload.json --repo "$PWD"
```

A minimal log payload is `{"text":"FAILED tests/test_app.py:14\nassert 1 == 2", "command":"pytest", "exit_code":1}`.
For a summary, obtain fresh file hashes and ranges from `ibwd_context` first; do not invent hashes.
Artifacts are retrieved with `ibwd_artifact_read` or `artifact-read`, which revalidate their dependencies.

Local logs can contain private output. Keep `.ibwd/` ignored. The helper sends only supplied bounded evidence to the
configured local service; it has no shell, filesystem tools or authority to edit source. Its model output is treated as data.

## Measurement

The usage dashboard distinguishes uncached input, cache reads, cache creation and output. Codex cache tokens are
subtracted from its inclusive input counter; Claude counters are already separate. Missing values remain unknown.
Cohorts also separate local-helper profiles. Server evidence records helper inference attempts, cache hits,
latency, retrieval errors and freshness retries without recording prompt/source text.

Smaller responses are not automatically token savings. Compare similar successful ordinary tasks within the same
client/model/effort/version, include rework and missing/provisional data, and account for local latency.
No model quality, performance or savings claim is established by deterministic adapter tests.
