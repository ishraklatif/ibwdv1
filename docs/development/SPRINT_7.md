# Sprint 7 — durable evidence without additional inference

Delivered: on-demand extractive navigation summaries and shared task handoffs through
`ibwd_artifact_save` / `ibwd_artifact_read` and equivalent CLI commands. Both clients use
the same repository-local `.ibwd/durable/` store. No background sweep or transcript
summarization runs. Ordinary context retrieval remains independent of these artifacts.

## Model decision

The embedder remains the only implemented local model role, retaining Sprint 6's explicit
repository opt-in and deterministic fallback. No reranker or generative summarizer is
enabled or implemented: neither has the independent ablation/resource evidence required
by the roadmap. Sprint 6's local screen failed its vague-query quality gate. This sprint
does not claim to improve that result or its latency. No model jobs, downloads, or
benchmarks were run. Role quality/resource acceptance remains pending; the delivered
slice uses zero additional inference and has no new model residency requirements.

## Extractive summaries

Obtain current file hashes from discovery/context and select small source ranges. Save a
JSON payload with **both** lists (dependencies may be empty):

```json
{
  "evidence": [{"file": "src/app.py", "hash": "CURRENT_HASH", "range": [10, 15]}],
  "dependencies": [{"file": "src/store.py", "hash": "CURRENT_HASH", "range": [1, 4]}]
}
```

```bash
ibwd artifact-save summary /tmp/summary.json --repo /path/to/repository
ibwd artifact-read ARTIFACT_ID --repo /path/to/repository
```

MCP accepts the same object as `payload`, with `kind="summary"`. Keep the returned
`artifact_id` to retrieve the reference from either client. Extracts are copied verbatim
and retain file, hash and inclusive one-based range. All declared dependencies are checked,
including ranges; whole-file hash changes invalidate the artifact even outside a cited
range. Unrelated file edits do not invalidate it. No generated prose or inferred edges are
added to the graph. The reserved graph `summaries` table is not used.

Selection is explicit: callers identify the useful excerpts and dependencies. IBWD cannot
prove that the excerpts are sufficient or that all behavioral dependencies were declared.
These are compact navigation references, not complete semantic descriptions. Source text
and retrieved instructions remain data, never new system authority; read exact edit targets.

## Shared handoffs

Use `kind="handoff"` with all six fields:

```json
{
  "goal": "Repair session refresh",
  "user_decisions": ["Preserve the public API"],
  "changed_paths": ["src/session.py", "tests/test_session.py"],
  "commands": [{"command": "pytest tests/test_session.py", "exit_code": 0,
                "result": "Caller observed 4 tests passing"}],
  "unresolved_questions": ["Check dynamic registration"],
  "evidence": [{"file": "src/session.py", "hash": "CURRENT_HASH", "range": [10, 15]}]
}
```

`ibwd artifact-save handoff /tmp/handoff.json` stores the record without executing any
command or parsing a transcript. User decisions and command/results are explicitly supplied
by the caller; IBWD cannot authenticate them. Their provenance is `caller_reported`.
Changed readable paths receive current hashes. Absent paths receive null hashes so a
deleted file's recreation invalidates the handoff. Evidence may include additional
dependencies or verification pointers. Unlisted runtime/configuration changes are outside
this check. Hash freshness does not certify that tests ran, still pass, or that work is done.

## Persistence, validation and bounds

- Immutable SHA-256 IDs cover the complete canonical JSON record, including provenance.
  Atomic replacement publishes only complete records; both clients can save independently.
- Version 1 records store source/dependency hashes, ranges, origin generation, validation
  status, `model_digest=null` and `prompt_version="extractive-v1"`.
- Read rechecks the current evidence inventory and source under the existing freshness
  protocol. Changed/deleted/excluded evidence returns `status="stale"` without artifact
  contents. Missing/corrupt/incompatible records return `missing`/`invalid`. Recreate stale
  artifacts explicitly; old command observations are never automatically re-certified.
- Evidence and dependency lists each allow 32 spans, each at most 80 lines. Handoffs allow
  32 changed paths, decisions and questions, 16 command observations, and 4096 UTF-8 bytes
  per text field. Canonical input and total extracted text are each limited to 24 KB. Existing source exclusions apply.
- Responses default to 16384 bytes, support 256..65536, and budget the duplicated MCP
  representation. Oversize artifacts fail explicitly; no excerpt is silently cut. Reduce
  the selected evidence or increase `max_bytes`. Reads do not paginate.
- State stays in ignored `.ibwd/`; no cross-device synchronization or automatic eviction.
  Artifact IDs are references, not authentication. Explicit notes can contain sensitive
  text supplied by a caller; the store is local, not a general secret detector.
- Artifact reads count as retrieval observations. Saves are not counted as retrieval.
  Neither kind of call establishes a completed task or token savings.

## Verification

Deterministic tests cover exact excerpts, dependency edits, deletions, exclusions, stale
hashes, invalid ranges, corruption, budgets, deleted-path recreation, command non-execution,
CLI/MCP parity, and real stdio transport through both generated client configurations.
These checks validate evidence contracts, not model quality, human audit, or retrieval savings.
