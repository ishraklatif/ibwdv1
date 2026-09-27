# IBWD roadmap: less context, reliable work, no additional spending

Updated 2026-09-24. This is the active plan from Sprint 3 onward for **both Codex and Claude Code**.
It supersedes the future sprint order in [the original plan](../IBWD_v1_EXECUTION_PLAN.md), not the historical results or frozen experiment.
This revision is a documentation/design change. Capabilities below are **proposed unless explicitly marked shipped**.

## 1. Product decision

Build IBWD into a **local, evidence-backed context service**, with a small skill teaching each coding client when to use it.
Its job is to deliver the source, relationships and verification evidence needed for the current task with minimal repeated exploration.
The primary objective is **less recorded model usage per correctly completed task**, while preserving correctness and acceptable latency.
More graph edges, more IBWD calls, more local models and shorter answers are not success metrics by themselves.

Keep tree-sitter, SQLite, explicit repository selection, the existing seven MCP tools, and source verification.
Move bounded output and context assembly ahead of embeddings. Add local AI only where a measured retrieval gap remains.
Use GPT-6 Astra or the user's chosen Claude model for difficult reasoning and edits; prepare its evidence locally.

The no-spend policy means no new subscriptions, API jobs, usage credits, paid benchmarks, hosted embeddings, rented compute or hardware purchases.
Existing authorized client access may be used for ordinary work within its allowance. IBWD cannot make proprietary models free or extend account entitlements.
If no such access is available, its deterministic tools remain usable and optional local models provide a separate, lower-capability path.
No automated fallback may start a billable model. Stop when the existing client allowance is exhausted.

## 2. What the evidence actually says

The [documentation review](DOCUMENTATION_REVIEW.md) inventories all 22 project Markdown paths and records the conflicts behind this pivot.
Source inspection also checked `setup.py`, `usage.py`, `usage_hooks.py`, `scan.py`, `health.py`, graph storage and MCP/traversal implementations.

| Finding | Consequence |
| --- | --- |
| Sprint 3's historical demo won on generated tokens for some tasks; the qualified total-token gate remains untested | Keep the result, but make no general 3× or 5× savings promise |
| Context budgeting was deferred until Sprint 7 | Pull it into Sprint 3 hardening and Sprint 4 |
| Setup and reporting already work locally for both clients | Improve their adoption evidence; do not rebuild onboarding |
| Current reports count direct calls; Codex orchestration can appear only as `exec` | Zero direct calls does not establish zero actual use |
| A recent Claude snapshot contains a scan but no graph lookup | Separate maintenance from retrieval; a scan alone demonstrates no retrieval benefit |
| MCP discovery/traversal lists lack output budgets; a depth cap does not bound result size or traversal work | Bound both computation and serialized output |
| Source graph omits tests and type-inferred uses; runtime coverage is incomplete | Add test scope and optional compiler evidence before stronger impact claims |
| Current scan/query path is not a shared, atomic freshness protocol | Coordinate two clients and branch changes before adding background work |
| Original plans hard-code substantial local models and sometimes conflate syntax with semantic certainty | Use measured device profiles and provenance; local inference stays optional |

Recent report snapshots are observations, not matched controls. Their different token totals cannot establish whether Codex or Claude is more efficient.
The older validation corpus was used for tuning; preserve its labels and TypeDoc's reserved confirmation status.

## 3. Target workflow and ownership

```text
Ordinary task in Codex or Claude Code
  -> small routing rule / task-specific skill
  -> exact lookup, graph query OR bounded task-context request
  -> local freshness check + deterministic retrieval
  -> optional local retrieval enhancement, only when enabled and useful
  -> source-backed result within an explicit budget
  -> host model reads edit targets, implements, verifies
  -> silent local report updates after normal client events
```

**Instructions:** a short stable routing block in `AGENTS.md` and `CLAUDE.md`; target at most 250 estimated tokens per block.
Do not insert the roadmap, entire graph, session history or full limitations document into every prompt.
Keep essential scope/empty-result warnings visible; load detailed guidance only when needed.

**Skill:** one portable workflow source with thin Codex/Claude adapters. Trigger on repository discovery, unfamiliar code,
relationship tracing and context preparation during implementation/debugging. Skip it when the exact edit and required source are already known.
Detailed skill content loads on use. This follows documented [Codex progressive disclosure](https://developers.openai.com/codex/skills)
and [Claude skill loading](https://code.claude.com/docs/en/skills); instruction loading does not guarantee tool selection.

**Tools:** keep narrow tools for exact questions. Add one bounded context-assembly operation only when it replaces a sequence of navigation calls.
Share implementation with an equivalent CLI for hosts where MCP is unavailable or incurs more overhead; do not build a second retrieval engine.
Keep tool metadata stable and concise. Do not assume all clients defer schemas in the same way.

**Hooks:** deterministic reporting/maintenance only, with empty success stdout and bounded work. Never start another model turn to analyze usage.
A skill directs behavior, an MCP tool retrieves evidence, and a hook performs lifecycle work; installing any one does not prove the others ran.

**Model:** the host owns reasoning, edits and final validation. IBWD does not override the selected model, rewrite its system prompt,
silently reduce its effort, or send its conversation to another service.

## 4. Revised sprint board

| Sprint | Deliverable | Exit evidence |
| --- | --- | --- |
| 3A | Reliable adoption and usage attribution | Supported fixture events counted exactly; ambiguity remains unknown; ordinary-work reports separate scan/retrieval/fallback |
| 3B | Freshness, concurrency and bounded results | Mutation/concurrency tests pass; result and work budgets enforced; incremental/fresh equivalence preserved |
| 4 | Task-context packets and lexical retrieval | Required evidence retained under budget; smaller payloads than a frozen competent navigation baseline |
| 5 | Useful impact analysis and test/type evidence | Correct scoped paths, test relevance and compiler provenance on held-out fixtures |
| 6 | Optional local semantic retrieval | Beats lexical+graph on held-out vague queries within measured device budgets; otherwise stays off |
| 7 | Optional local specialists and durable evidence | Each additional model earns its cost in latency/resources; stale or unsupported summaries excluded |
| 8 | Portable skill packaging and continuous efficiency review | Both clients install/update cleanly; reporting automatic; documented ordinary-work results without extra model jobs |

Sprint 3's structural implementation remains delivered; its historical efficiency gate remains **untested**.
3A/3B are new product-hardening work, not a retroactive pass of that gate. Later engineering is permitted under the no-spend owner direction.
Complete one reviewable slice at a time. Do not implement all eight stages as one rewrite.

### Sprint 3A — trustworthy usage and adoption

Implementation: [Sprint 3A delivery notes](SPRINT_3A.md). Local instrumentation is delivered; live model adoption and savings
remain unverified. Child records are explicitly excluded, not merged into parent totals; no supported disjoint child accounting
contract is assumed. The criteria below remain the design/acceptance reference.

Ship the first minimal routing skill with this sprint, using only the existing seven tools, source verification and explicit fallback rules.
Generate client-specific installation from one shared skill source through setup; test trigger metadata and conflicting instructions.
Do not wait until Sprint 8 to try ordinary-work adoption. Sprint 8 consolidates and packages the workflow after it has useful evidence.

1. Extend the existing report schema to distinguish `configured`, `connection_observed`, `scan_calls`, `retrieval_calls`,
   `retrieval_errors`, `fallback_observed`, `attribution_unknown`, and `usage_incomplete`. Each field needs an evidence source.
   An unavailable observation is `unknown`, not `false` or zero. Do not infer task success from assistant prose.
2. Instrument the server dispatch boundary with a local bounded event ledger: request identity, process/connection identity,
   client identity when supplied, tool, duration, response bytes, result count, index generation, status and truncation.
   Record completion/error separately from invocation. Exclude prompts, raw source, secrets and tool arguments by default.
3. Correlate with structured client events/request IDs where the host exposes them. Never execute transcript scripts to recover calls,
   or count a tool name merely because it appears inside an `exec` string. Server connection identity alone is not a conversation ID.
   Concurrent sessions without a reliable join stay unattributed; timestamp proximity alone is insufficient.
4. Extend supported nested-event parsing and versioned fixtures. Deduplicate client/server representations of one call.
   Child usage must be separately accounted for with explicit parent links and accounting semantics; avoid adding it twice when already included.
5. Make reporting incremental: persist parser state and byte offsets, buffer incomplete lines, detect rotation/truncation,
   update cumulative counters without double counting, and rebuild state safely when necessary. Serialize competing Stop/SessionEnd writes.
6. Keep the existing `latest-codex.md`, `latest-claude.md` and per-session reports. Add one automatically refreshed local comparison summary
   with clearly labelled cohorts and missing-data counts. Normal use requires no additional shell command or reminder prompt.

**Exit:** deterministic fixtures cover direct/nested calls, mere mentions, retries, mixed clients, concurrent sessions, resumed sessions,
partial logs, counter resets and missing child usage. Verify adoption during the user's next needed task in each client; if that observation
is unavailable, release instrumentation but label model adoption unverified. No fabricated live-client certification.

### Sprint 3B — inexpensive and reliable retrieval

Implementation and measured local profile: [Sprint 3B delivery notes](SPRINT_3B.md). Automatic freshness is now active;
version 1 keeps legacy shapes with hard failure on oversized queries, while version 2 provides bounded envelopes and pages.
The criteria below remain the design/acceptance reference. Other laptops need their own measurements using the same frozen profile.

1. Establish a per-repository scan lock and a published index generation. Publish graph and manifest consistently; interrupted scans
   must leave the previous complete generation or an explicit unavailable/stale state. Test two MCP processes and a crashing writer.
2. Check freshness inside retrieval so future clients need not spend an extra model round trip requesting a scan each session.
   Cache checks only within a bounded validity policy. Cover untracked files, deletions, ignore/config changes, dirty files and branch switches;
   a clean Git diff or watcher event stream alone is insufficient. Validate emitted source hashes and retry once if they change mid-read.
   Automatic freshness replaces the former mandatory per-session scan workflow; explicit scan remains available.
3. Add limits to discovery and traversal: result count, output bytes, visited nodes/edges and elapsed time. Limit during computation,
   not only after constructing an enormous result. Use progress interruption or bounded traversal for dense/cyclic graphs.
4. Version a compact response envelope: `schema_version`, `index_generation`, `scope`, `items`, `truncated`, `limit_reason`, `next_cursor`.
   Bind cursors to query/filter/generation; reject stale cursors. Only report an exact total when actually known. Preserve all requested relations.
5. Return exact symbol identities, definition ranges and optional call-site evidence. Group shared paths and metadata instead of repeating prose
   on every row. Never silently truncate exhaustive questions; pagination and source-search fallback must remain accessible.
6. Test MCP serialization as the host receives it, including whether structured and text representations duplicate payloads.
   Budget the actual response representation, not merely an internal Python object. Version compatibility instead of silently changing all list contracts.
   If a budget cannot fit the minimal valid envelope, return a clear budget error; never cut JSON or a source span into misleading partial syntax.

**Exit:** deterministic/fresh equivalence after all existing mutations, visible partial-result semantics, stable pagination, and bounded dense-graph
work. Record median/p95 latency and peak memory on each device profile. Reject stale source citations and silent completeness claims.
Freeze per-profile latency/memory limits before evaluating changes. Include cold start, warm query, refresh and simultaneous-client workloads;
do not retune the limits after seeing a failure without recording the revision.

### Sprint 4 — useful evidence in one request

Implemented interfaces and limits: [Sprint 4 delivery notes](SPRINT_4.md). Comparative performance acceptance remains pending;
no benchmarks were run under the repository restriction. The criteria below remain the acceptance reference.

```text
ibwd_context(task, targets=[], budget_tokens=2000, detail="outline", cursor=null)
ibwd_read(symbol_id_or_path, expected_hash, range, budget_tokens=1000)
```

Expose an equivalent CLI for these operations. Keep exact lookups cheaper than broad context construction.
The 2,000/1,000 figures are initial tuning defaults, not proven optimal budgets or guaranteed provider token counts.

1. Add SQLite FTS5 lexical search over identifiers (including split camel/snake case), paths, signatures, docstrings and selected documentation.
   Use headings plus source spans for docs; bound chunks. Index tests/config separately with scope labels. Respect ignores and secret exclusions.
2. Route explicit symbols/paths directly. For unfamiliar concepts, retrieve lexical candidates and expand a small resolved graph neighborhood.
   Start with deterministic rank fusion, exact-match priority, query relevance and diversity. Graph centrality is a tie-breaker, not an instruction
   to return popular utilities for every task. Do not combine incompatible raw scores with arbitrary equal weights.
3. Assemble the smallest useful packet: candidate edit locations, signatures, selected exact source, relevant relationships,
   scope gaps, and targeted verification pointers. Include relevant project-instruction locations without rewriting or overriding their authority.
4. Deduplicate overlapping source spans; keep exact code intact. Prefer outlines for discovery and bodies for edit targets.
   Make source expansion easy so compression does not force speculative edits or repeated full-file reads.
5. Cache by repository/worktree, query, scopes, index generation, content hashes and retrieval version. Share derived artifacts between clients,
   but never omit evidence merely because a different client previously received it. A compacted/resumed session may need the full packet again.
6. Provide an opt-in deterministic command-output reducer for supported tests/builds: preserve exit code, failure count, assertions,
   actionable diagnostics and a local full-output path. Do not transparently rewrite arbitrary shell commands or hide failures.
   Validate this separately from graph retrieval; test logs may dominate a session's avoidable context.

**Exit:** freeze at least 30 local retrieval tasks across supported languages and documentation/config cases, including tiny edits,
ambiguity, empty results and large fan-out. Compare against competent bounded `rg`/source-read recipes on identical snapshots.
Initial target: at least 30% lower median serialized evidence size on structural tasks, with every required evidence item retained
or explicitly available through pagination. Also report cumulative payload over all expansions, retrieval latency and regressions.
This is a **local payload target**, not a claim of model-token savings or task success. If evidence loss drives extra reads, change the packing policy.

### Sprint 5 — impact that helps edits

Implementation: [Sprint 5 delivery notes](SPRINT_5.md). Separate test scope, bounded impact paths, previous-index diff,
reference/filename test relevance and an opt-in installed TypeScript adapter are delivered. Python compiler/SCIP integration
and coverage-artifact ingestion remain optional future extensions; no test coverage or runtime safety is inferred.

1. Index test symbols and references with a separate test scope. Keep production query defaults compatible.
2. Add bounded impact traversal with evidence paths and relation filters. Distinguish incoming change exposure from outgoing dependencies;
   neither proves breakage. For a diff, include both changed/deleted old identities and new identities so deletions do not erase their impact.
3. Add test relevance from imports/calls, then clearly labelled filename/path heuristics. Existing coverage artifacts may supply observed
   coverage only when their source revision and execution provenance match. A reference is not verified test coverage.
4. Add optional installed TypeScript/Python language-server or SCIP adapters for difficult receivers and symbol references.
   Preserve compiler version, project boundaries and diagnostics; a missing environment yields unknown. Keep type-derived possible targets
   distinct from definite calls. Do not rebuild a type checker or quietly download dependencies during a query.
5. Prioritize gaps that cause actual work: anonymous/default re-exports, nested callbacks and config aliases when observed.
   Defer a full commit/PR graph. A targeted local diff/history lookup is sufficient until history retrieval demonstrates value.

**Exit:** reviewed fixtures cover production/test separation, changed signatures, deletion, dynamic registration, aliases and incomplete compiler
environments. No `safe_to_delete` claims from empty graphs. No new precision regression on existing tests; report broader coverage separately.

### Sprint 6 — local semantics only when necessary

Implementation: [Sprint 6 contracts and limits](SPRINT_6.md). The opt-in local adapter, vector cache and rank fusion are implemented;
[local screening](SPRINT_6_MEASUREMENT.md) subsequently measured quality/resources but failed the proposed vague-query quality gate.
Independent held-out acceptance remains unmeasured. The layer stays off until a repository explicitly opts in.

1. Establish lexical+graph performance first. Offer an opt-in embedding adapter for vague queries that lexical retrieval misses.
2. Embed bounded source/doc chunks and signatures using local weights. Cache by content, model digest, dimensions and preprocessing version.
   On incompatible changes, rebuild a separate generation before switching it into service. A no-op refresh performs zero embedding calls.
3. Fuse lexical and vector ranks, then bounded structural expansion. Evaluate exact-name, synonym, code-behavior and documentation tasks separately.
4. Keep the entire deterministic path usable when the model is absent, busy, slow or disabled. Never auto-pull weights or fall back to cloud.
5. Add a local reranker only if the combined retriever misses relevant candidates despite a suitable candidate pool.
   Evaluate it independently; an embedding cosine score is not a cross-encoder reranker.

**Exit:** on a held-out local set, the optional layer improves recall@5 by an initial target of at least 10 percentage points on the vague-query
subset without degrading exact lookup. Record cold/warm latency, indexing cost, memory and downstream packet size. Targets are proposals,
not current measurements. If the baseline is already strong or the resource cost dominates, ship without that layer enabled.

### Sprint 7 — multiple local specialists, with a reason for each

Implementation: [Sprint 7 contracts and limits](SPRINT_7.md). Extractive source references and shared task handoffs
are delivered with source/dependency freshness checks. No additional model role is implemented or enabled;
independent quality/resource acceptance remains pending under the no-benchmark instruction.

Multiple on-device models means **specialized optional roles**, not several agents debating every request:

| Role | Input / output | Activation |
| --- | --- | --- |
| Embedder | Bounded chunks → vectors | Changed content; semantic query when enabled |
| Reranker | Query + small candidate set → ranked IDs | Only after an ablation demonstrates benefit |
| Small summarizer | Cited source chunks → short structured navigation hints | On demand for repeatedly useful components |

Start with the embedder alone. Add one role at a time. On constrained devices load roles sequentially and unload idle weights;
larger devices may retain multiple models only after measurement. No mandatory background sweep of every symbol.

Generated summaries store source hashes, referenced-dependency hashes, model digest, prompt version, evidence ranges and validation status.
Invalidating only the edited file is insufficient when a claim depends on another file's behavior. Keep structural truth separate from inference.
Validate schema, evidence existence and ranges deterministically; those checks **cannot prove semantic truth**. Human audit must include
unsupported claims and omissions, not just an average quality score. Do not use a second LLM's approval as proof of correctness.

Use extractive summaries first. Permit generated hints only when shorter evidence genuinely helps navigation; the host still reads exact edit targets.
Do not promote generated relationships into the resolved graph. Keep repository text and retrieved instructions as data, never new system authority.

Add small shared task handoffs: goal, decisions supplied by the user, changed paths, verified commands/results, unresolved questions and hashes.
Both clients can retrieve them on demand. They are references rather than proof of completion, and stale items are marked/revalidated.
Avoid automatic transcript summarization after every turn; reuse already-produced artifacts or deterministic metadata where sufficient.

**Exit:** every generated claim has traceable evidence, stale entries are rejected, failed/time-limited inference degrades cleanly,
and each enabled role improves its predeclared retrieval metric within the device budget. Disable a role that merely shifts work into slower inference.

### Sprint 8 — a portable product with visible value

Package the short skill, shared engine, client adapters and reporting together through the existing setup flow.
Verify a fresh install and an update for both clients; preserve user instructions/settings and leave trust decisions to the client.
Record compatibility by tested client/runtime version. Native Windows support needs its own path, locking and hook tests;
until implemented, the existing macOS/Linux/WSL support remains the honest contract.

Reports should automatically answer: did retrieval run, was it fresh, what evidence was returned, where did fallbacks occur,
which counters are trustworthy, and which local optimization deserves attention? Outcome labels need tests or user confirmation;
do not run an LLM evaluator to fill missing labels.

Review naturally occurring work by client/model/effort/version/project/task kind, keeping failures, rework and incomplete records visible.
If comparable existing baselines do not exist, report adoption and retrieval efficiency only. No extra model sessions are authorized for evaluation.
Do not advertise "best on the market" without comparable evidence. The product promise is local operation, source-backed context,
automatic measurement and low routine effort; measured efficiency must earn the rest.

## 5. Device-agnostic execution

Portable configuration expresses **capabilities and resource budgets**, never a required laptop, absolute developer path or single model stack.
One codebase supports these profiles; select by free memory under the user's normal workload, not just installed RAM:

| Profile | Default behavior | Upgrade rule |
| --- | --- | --- |
| Core | SQLite + syntax/lexical/graph retrieval; zero model dependencies | Always available on supported hosts |
| Light local | Small embedder; at most one inference job | Fits with OS, editor, browser and build tools running |
| Balanced local | Embedder plus optional small summarizer/reranker, loaded sequentially | Each role passes quality, memory and latency checks |
| Larger local | May keep useful specialists resident | Measured headroom; never a default requirement |

Probe OS/architecture, available memory, supported runtime/device, installed models/digests and free disk. Unknown capability selects Core.
Use adapters rather than assumptions about CUDA, Apple Metal, unified memory or GPU availability. CPU-only remains valid, possibly slower.
Ollama is an initial portable adapter; llama.cpp or MLX-specific support is deferred until a demonstrated compatibility/performance need.
Model runtimes expose different embedding/generation/reranking capabilities; negotiate them rather than assuming API compatibility.

Example candidates, **not mandatory installs or market-best claims**:
[`qwen3-embedding:0.6b`](https://ollama.com/library/qwen3-embedding:0.6b) is listed at 639 MB;
[`qwen3:4b`](https://ollama.com/library/qwen3:4b) at 2.5 GB is a candidate for bounded summaries.
The original [`qwen3-coder:30b`](https://ollama.com/library/qwen3-coder:30b) is listed at 19 GB and is unsuitable as a universal default.
Download size excludes runtime/KV-cache and application memory. Recheck licenses, digests, fit and task quality when implementing;
reuse already-installed suitable models before suggesting downloads.

Proposed starting controls: one concurrent inference job, bounded 2–4K summarizer inputs, short output limits,
interactive deadlines, and cancellation/backpressure. Pause optional work under memory pressure or while builds need resources.
Use local endpoints and allowlisted local model identifiers; reject cloud identifiers and unconfigured remote endpoints, with no silent fallback.
Enforce this in IBWD even when a runtime also offers cloud services. Runtime residency and context size affect memory;
see [Ollama's runtime guidance](https://docs.ollama.com/faq).

Each device builds its own disposable index, embeddings and reports. Share source/config templates through Git, not `.venv`, SQLite state,
absolute paths or logs. Run setup once per repo on that device. A global server pinned to one repository must never serve all repositories.
Cross-device report export is optional, minimized and deduplicated by session identity; keep raw conversations local.

## 6. Use frontier models efficiently

Keep the user's chosen **GPT-6 Astra** for difficult diagnosis, architecture, ambiguous changes and final integrated reasoning.
Use local retrieval to reduce the material it must rediscover. Do not automatically select maximum reasoning effort for trivial work;
choose effort appropriate to risk, and measure rework before reducing it. The official model page documents supported effort settings
and paid API access; IBWD cannot promise account access or free use. [GPT-6 Astra documentation](https://developers.openai.com/api/docs/models/gpt-6-astra).

For Claude, select from the models actually available in that client's account. Start with Sonnet for routine coding and consider Opus
for difficult reasoning; respect an explicit user choice. IBWD itself stays model-independent and does not switch models mid-task.
[Claude model configuration](https://code.claude.com/docs/en/model-config) documents aliases and effort controls.

Use one primary client for a task. Hand the other a compact task artifact when switching, rather than asking both to rediscover the repository.
Separate sessions at real task boundaries; do not restart every turn and repeatedly rebuild context. Keep project instructions and tool schemas
stable to avoid unnecessary cache churn. Offload deterministic searches and log reduction before considering additional cloud subagents.
Subagents can isolate verbose work, but their usage still counts and must be measured.

A normal prompt should state the task and acceptance criteria, for example:

> Fix the session-refresh bug. Preserve the public API, locate the relevant implementation and tests, make the smallest sufficient change,
> and report the verification result.

No repeated "use IBWD" phrase is required once routing is installed and available. Requiring an IBWD call for a known one-line edit can waste tokens.
Verify behavior through reports and fix selection/availability problems rather than making the user operate the tool manually.

## 7. Measurement and promotion rules

Maintain three separate ledgers:

1. **Retrieval:** actual serialized bytes, locally counted tokens with tokenizer/version where supported, required-evidence recall,
   expansions, duplicate spans, stale results, median/p95 latency and resource use.
2. **Client usage:** provider-recorded input, cache and output counters using that client's accounting conventions. Preserve reasoning
   as a subcount where applicable. Unknown tokenizers use labelled estimates plus byte limits; never promise an exact cross-model token budget.
3. **Work outcomes:** verification evidence, completion/failure/unfinished status, corrections and elapsed work. Reduced output with more rework fails.

Total context processed is not peak context, billed dollars or subscription allowance. Existing accounting conventions in
[USAGE_MEASUREMENT.md](USAGE_MEASUREMENT.md) remain in force. Never sum overlapping cache/reasoning counters twice.
Do not compare raw Codex and Claude totals as a savings experiment; compare each against its own comparable task cohorts.
Current [Claude cost guidance](https://code.claude.com/docs/en/costs) distinguishes plan usage and token estimates and notes that
`/insights` itself uses model tokens. IBWD's automatic analysis should remain deterministic.

Local engineering gates can pass without cloud calls. A deterministic payload comparison cannot establish host-model success or causal savings.
Observe needed normal work only; report missing controls and sample sizes. Public savings claims need appropriate evidence and remain unproven here.
Reject fixes that save payload by omitting necessary evidence, suppressing errors or presenting incomplete search as exhaustive.

## 8. Alternatives and pivot criteria

There is no evidenced universal "best skill" for this workload. These are relevant design references, not a head-to-head benchmark:

| Approach | Borrow / compare | IBWD decision |
| --- | --- | --- |
| [Serena](https://github.com/oraios/serena) | MCP symbol navigation backed by language servers; free/open-source LSP route | Compare supported reference fixtures before building more heuristic resolution; use optional adapters where appropriate |
| [Aider repo map](https://aider.chat/docs/repomap.html) | Ranked, compact repository context fitted to a budget | Pull that principle forward into Sprint 4 |
| [Repomix](https://repomix.com/guide/) | Reproducible repository packaging | Useful explicit export baseline; avoid injecting an entire repo for every task |
| Built-in search and installed compiler tools | Very low setup cost, exact source and current diagnostics | Keep as first-class fallbacks and competent baselines |

Do not install overlapping MCP suites by default. If a free existing analyzer consistently provides better evidence, retain IBWD's
budgeting/reporting interface and use that analyzer underneath rather than duplicating its implementation. Review dependencies/licenses first.
If persistence costs more than it saves for a small or rapidly changing repo, allow an on-demand map/lexical profile.
If local summaries add latency or errors, leave them disabled indefinitely. These are successful simplifications, not failed sprints.

## 9. First implementation sequence

1. Delivered: Sprint 3A attribution and scan-versus-retrieval reporting in the existing usage/server modules.
2. Delivered: Sprint 3B bounded output/computation and generation-safe scan/query behavior with versioned compatibility.
3. Sprint 4 features implemented: lexical packets, source expansion and saved-output reduction. Comparative payload/latency
   acceptance against bounded-search recipes remains pending authorization for benchmarks.
4. Observe the next necessary task in each client; fix the largest evidenced waste before starting Sprint 5.

No paid experiment, model download, new service, automatic model selection, skill installation or runtime behavior was enabled by this document revision.
