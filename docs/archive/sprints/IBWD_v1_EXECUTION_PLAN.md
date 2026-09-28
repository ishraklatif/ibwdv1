# IBWD v1 — Agile Execution Plan (Sprint-Based)

> **Historical plan; future sequencing superseded 2026-09-24.** Use
> [the active device-agnostic roadmap](../../development/ROADMAP.md) for Sprint 3 hardening and later work.
> Its no-spend, dual-client direction overrides paid demo instructions and mandatory model choices below.
> This body is preserved as design history; the old token gate remains untested, not passed.

**Persistent structural + semantic codebase memory for AI coding agents.**
Drop this file in the repo root as `EXECUTION_PLAN.md`, keep `CLAUDE.md` (Appendix C) alongside it, and work through the sprints in order with Claude Code.

## Why sprints instead of linear phases
The original plan built the MCP server last (after scanning, parsing, git, and embeddings were all done) — so you couldn't tell whether any of it actually helped Claude Code until everything was finished. This revision inverts that: **the MCP server ships in Sprint 1** with one working tool, and every sprint after that adds exactly one new capability to it. Each sprint ends with a **Sprint Demo** — a real A/B test where you ask Claude Code the same question with and without that sprint's new tool, and measure whether it actually helped. That's your go/no-go signal at every step, not just at the end.

> **Core hypothesis under test:** a persistent structural + semantic graph lets an AI coding agent solve repository tasks using substantially less context than repeatedly exploring the repository, while maintaining or improving task success.
> **Golden rule:** deterministic facts (tree-sitter) get `confidence=1.0, source_type=static_analysis`. LLM-inferred facts get `confidence<1, source_type=llm_inference`. Claude must verify graph claims against source before editing anything — the graph is a map, never the territory.

## Sprint board

| Sprint | Ships | New MCP tool(s) | Demo question type | Go/no-go signal |
|---|---|---|---|---|
| 1 | File/dir index | `ibwd_scan`, `ibwd_find_files` | "which files are tests / configs / X" | fewer tool calls than Glob |
| 2 | Symbol index | `ibwd_find_symbol`, `ibwd_list_symbols` | "where is X defined" | fewer tokens than Grep, esp. common names |
| 3 | Call graph | `ibwd_callers`, `ibwd_dependents`, `ibwd_trace_path` | "what calls / imports X" | ≥3x fewer tokens than manual grep-trace |
| 4 | Impact analysis | `ibwd_impact`, `ibwd_tests_for` | "what breaks if I change X" | ≥3–5x fewer tokens, equal/better correctness |
| 5 | Semantic search | `ibwd_search` | "where is `<vague concept>` implemented" | higher hit-rate than grep on non-exact queries |
| 6 | Semantic understanding | `ibwd_explain` | "what is X responsible for" | matches manual-read accuracy, far fewer tokens |
| 7 | Context optimization | upgrades default output of `ibwd_search`/`ibwd_impact` | complex multi-hop task | equal/better success at a fixed, smaller token budget |
| 8 | Full agent integration | slash commands + `CLAUDE.md` final | full regression suite (all of the above) | structural ≥5x tokens saved; source-level within ~10–20% |

---

## 0. Prerequisites & environment setup

Do this once, before Sprint 1.

```bash
# Python 3.11+ and a fast package manager
python3 --version          # need 3.11+
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv: fast venv/deps

# Ollama for local models
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &

# Pull the local model stack (adjust sizes to your hardware; only needed from
# Sprint 5 onward — you can skip this until then)
ollama pull qwen3-embedding:0.6b       # embeddings, start small (~640MB)
ollama pull qwen3-coder:30b            # summarizer (~19GB, needs 24GB+ VRAM/unified mem)

# Claude Code CLI
npm install -g @anthropic-ai/claude-code
claude --version
```

Hardware note: if `qwen3-coder:30b` doesn't fit, substitute `devstral-small:24b` (68.0% SWE-Bench Verified, runs on a single RTX 4090 / 32GB Mac) or `gpt-oss:20b` (16GB RAM) in Sprint 6 — architecture doesn't change, only the model tag.

```bash
mkdir ibwd && cd ibwd
git init
uv init --python 3.11
```

---

## Sprint 1 — Foundation: File & Directory Index

**Sprint goal (user story):** *As Claude Code, I can ask IBWD which files exist and how they're categorized, so I don't burn multiple `Glob` calls exploring the tree by hand.*

### Build tasks
- [x] Scaffold the repo layout (Appendix A) with `pyproject.toml` (deps: `click`, `gitpython`, `pathspec`, `xxhash`, `mcp`, `httpx`, `pytest` — add `tree-sitter*`/`sqlite-vec` in later sprints as needed)
- [x] `graph/schema.sql` + `graph/database.py`: create `.ibwd/graph.db` with the `nodes`/`edges` tables (Appendix B)
- [x] `scanner/filesystem.py`: walk the repo respecting `.gitignore` (via `pathspec`), classify each file (source / test / doc / config) by path heuristics, hash contents with `xxhash.xxh3_64`
- [x] Insert `File`/`Directory` nodes + `CONTAINS` edges (`confidence=1.0`, `source_type=static_analysis`)
- [x] `.ibwd/manifest.json`: `path -> content_hash`, so re-scans are incremental
- [x] `cli.py`: `ibwd scan` — incremental scan, prints a summary
- [x] **`mcp/server.py` — ship this now, not later.** Expose two tools: `ibwd_scan()` (triggers a rescan, returns the summary) and `ibwd_find_files(kind: str | None, name_pattern: str | None)` (returns categorized file list with path + kind, filtered)
- [x] Register the server: `claude mcp add ibwd -- uv run python -m ibwd.mcp.server`
- [x] Add the first block to `CLAUDE.md` (Appendix C): "for file discovery/categorization, prefer `ibwd_find_files` over repeated `Glob` calls"

### Claude Code prompt
```
Implement Sprint 1 of IBWD. Scaffold src/ibwd/{cli,scanner,graph,mcp} per
EXECUTION_PLAN.md Appendix A. Build graph/database.py loading schema.sql
(Appendix B) into .ibwd/graph.db. Build scanner/filesystem.py: walk the repo
respecting .gitignore via pathspec, classify files as source/test/doc/config
by path pattern, hash with xxhash.xxh3_64. Insert File/Directory nodes and
CONTAINS edges (confidence=1.0, source_type=static_analysis). Persist
.ibwd/manifest.json (path->hash) and make `ibwd scan` incremental against it.
Build mcp/server.py with the official Python MCP SDK exposing ibwd_scan()
and ibwd_find_files(kind, name_pattern) returning a compact JSON list of
{path, kind}. Write pytest tests using a temp git repo fixture.
```

### Sprint demo — value test
Run the generic protocol (Appendix D) with these 3 tasks:
1. "List all the test files in this repo."
2. "Which files are configuration (not source code)?"
3. "How many Python source files are under `src/`?"

Compare: baseline (`Read`,`Glob`,`Grep` only) vs. with `ibwd_find_files` available. Log tool-call count and tokens to `benchmarks/sprint_1_results.csv`.

### Definition of done
- [x] `claude mcp list` shows `ibwd` connected
- [x] `ibwd scan` on this repo completes with correct counts; a second run with no changes reports 0 changed
- [x] All 3 demo tasks answered correctly via `ibwd_find_files` with fewer tool calls than the Glob-only baseline
- [x] `benchmarks/sprint_1_results.csv` exists with the logged comparison

---

## Sprint 2 — Symbol Index

**Sprint goal:** *As Claude Code, I can ask exactly where a class/function/method is defined, so I don't grep for a common name and wade through false positives.*

### Build tasks
- [x] Add `tree-sitter`, `tree-sitter-python`, `tree-sitter-javascript`, `tree-sitter-typescript` deps
- [x] `scanner/python.py`, `scanner/javascript.py`: tree-sitter tag queries (Aider's Apache-2.0 `.scm` queries are a good starting point) extracting `Class`/`Function`/`Method` nodes with exact `start_line`/`end_line`, and `DEFINES` edges
- [x] Only reparse files whose content hash changed since the last scan
- [x] New MCP tools: `ibwd_find_symbol(name)` → exact/fuzzy matches with `file:line`; `ibwd_list_symbols(file)` → all symbols defined in a file
- [x] Extend `CLAUDE.md`: "for 'where is X defined', prefer `ibwd_find_symbol` over `Grep` for a symbol name"

### Claude Code prompt
```
Implement Sprint 2. Add tree-sitter-based Class/Function/Method extraction
for Python and JS/TS in scanner/python.py and scanner/javascript.py,
producing DEFINES edges (confidence=1.0, source_type=static_analysis) with
exact start_line/end_line. Only reparse files whose content_hash changed.
Add ibwd_find_symbol(name) and ibwd_list_symbols(file) to mcp/server.py.
find_symbol should do exact match first, then case-insensitive substring
match, returning {name, kind, file, line}. Write tests with fixture files
covering a class, a function, and a method with the same name in two files
(to confirm find_symbol returns both, not just one).
```

### Sprint demo — value test
3 tasks, using a symbol name that's common enough to produce noisy grep results (pick a real one from this repo, e.g. a method named `run` or `get` if one exists, or your `scan`/`build` functions):
1. "Where is the `X` class/function defined?"
2. "List every function defined in `<a specific file>`."
3. "Find a function named `<common name>` — how many are there and where?"

Baseline: `Grep` only. Compare: token count (grep on a common name returns many hits Claude has to read through) and tool calls.

### Definition of done
- [x] Symbol extraction produces correct node/edge counts on a small known fixture
- [x] Every symbol node has a verifiable `file:line`
- [x] All 3 demo tasks: `ibwd_find_symbol` wins on tokens and/or precision vs. Grep baseline, logged to `benchmarks/sprint_2_results.csv` *(2/3 clean, 1 partial — see SPRINT_2.md)*

---

## Sprint 3 — Call Graph & Dependencies

**Sprint goal:** *As Claude Code, I can ask what calls or imports a given symbol, so I don't manually trace call chains through Grep.*

### Build tasks
- [x] Extend tree-sitter extraction: `IMPORTS` edges (file→file, resolved where possible) and `INHERITS` edges (class→class)
- [x] `CALLS` edge resolution cascade (try in order, stop at first match): (1) import-map match `conf=0.95`, (2) same-module match `conf=0.90`, (3) unique-name-in-repo match `conf=0.75`, (4) suffix match `conf=0.55`, (5) fuzzy match `conf=0.35`
- [x] New MCP tools: `ibwd_callers(symbol, depth=1)`, `ibwd_dependents(symbol, depth=1)` (a "dependent" = something this symbol imports/calls) — use recursive CTEs (Appendix B) for depth > 1
- [x] Extend `CLAUDE.md`: "for call-graph / dependency questions, prefer `ibwd_callers`/`ibwd_dependents` over manual Grep-tracing"

### Claude Code prompt
```
Implement Sprint 3. Extend scanner/python.py and scanner/javascript.py with
IMPORTS (file->file) and INHERITS (class->class) edges. Implement the
5-tier CALLS resolution cascade described in EXECUTION_PLAN.md Sprint 3,
storing the tier's confidence and source_type=static_analysis (a resolved
call is still a deterministic fact even though resolution used heuristics
-- keep confidence to reflect resolution certainty, not authorship). Add
retrieval/traversal.py with recursive-CTE callers_of(node_id, depth) and
dependents_of(node_id, depth). Add ibwd_callers and ibwd_dependents to
mcp/server.py, both accepting a symbol name and a depth parameter, returning
{name, file, line, distance, confidence} per result. Test depth=1 vs
depth=2 on a fixture with a 3-hop call chain.
```

### Sprint demo — value test
3 tasks, this is the classic impact-analysis question type:
1. "What calls `<a real function in this repo>`, directly?"
2. "What does `<a real function>` depend on / call?"
3. "Trace the call chain from `<function A>` to `<function B>` if one exists."

Baseline: `Grep` only (manually tracing calls is exactly what grep-heavy agents do today — this is where the token savings should be largest). **Target ≥3x fewer tokens.**

### Definition of done
> **Status (Sept 2026):** built; gate item below is *open*. See `SPRINT_3.md` (write-up, deviations) and `KNOWN_LIMITATIONS.md`.

- [x] `CALLS` edges always populated with `confidence` + `source_type`
- [x] Depth-2 traversal returns correct indirect callers on a known fixture
- [ ] All 3 demo tasks: ≥3x token reduction vs. baseline, logged to `benchmarks/sprint_3_results.csv` — **if this doesn't hit ≥3x, stop and debug the resolution cascade before Sprint 4** **— NOT MET: q2 measured 2.3x (5 repeats: 2.07–2.49x); see SPRINT_3.md. Multi-repo validation pending.**

---

## Sprint 4 — Impact & Change Analysis

**Sprint goal:** *As Claude Code, I can ask "what could break if I change X", including tests and recent git history, so I get a real pre-edit risk check in one call instead of five.*

### Build tasks
- [ ] `git/history.py` (GitPython): last N commits (default 200) → `Commit` nodes + `TOUCHED` edges (commit→file)
- [ ] Simple test-linkage heuristic: match `test_*`/`*_test.py`/`*.test.ts` files that import or reference the target symbol → `TESTED_BY` edges
- [ ] New MCP tools: `ibwd_tests_for(symbol)`; `ibwd_impact(symbol)` — combines direct+indirect callers, direct+indirect dependents, and tests into one labeled report
- [ ] Extend `ibwd scan` to ingest new commits incrementally (record last-ingested SHA in the manifest)
- [ ] Extend `CLAUDE.md`: "before editing a shared symbol, run `ibwd_impact` first"

### Claude Code prompt
```
Implement Sprint 4. Add git/history.py using GitPython for the last 200
commits -> Commit nodes + TOUCHED edges. Add a TESTED_BY heuristic matching
test files that reference a symbol. Add ibwd_tests_for(symbol) and
ibwd_impact(symbol) to mcp/server.py -- impact should call the Sprint 3
traversal functions for direct+indirect callers/dependents plus
ibwd_tests_for, and return one structured report with each item labeled
direct/indirect and its confidence. Extend `ibwd scan` to record the last
ingested commit SHA and only pull newer commits on rescans. Add
benchmarks/timing.py: full scan vs. touch-one-file-and-rescan, print both
times to confirm incremental stays fast as the graph grows.
```

### Sprint demo — value test
3 tasks:
1. "If I change the signature of `<a widely-used function>`, what breaks?"
2. "What tests cover `<a specific class/function>`?"
3. "Is it safe to delete `<a rarely-used function>`?" (tests whether IBWD correctly reports *no* callers/dependents — a true negative is as valuable as a true positive here)

Baseline: manual exploration (Grep for callers + Glob for test files + Read to confirm). **Target ≥3–5x fewer tokens, equal or better correctness** (verify by hand that IBWD's answer is actually complete, not just fast).

### Definition of done
- [ ] Incremental rescan after touching 1 file is ≥3–4x faster than a fresh full scan
- [ ] All 3 demo tasks pass correctness check and hit the token target, logged to `benchmarks/sprint_4_results.csv`
- [ ] **Checkpoint:** this is the deepest test of the pure structural layer. If Sprints 1–4 aren't clearly winning on tokens by now, do not proceed to the semantic layers (5–7) until you've fixed the structural retrieval — adding LLM summaries on a weak foundation won't rescue the numbers.

---

## Sprint 5 — Semantic Search

**Sprint goal:** *As Claude Code, I can ask a natural-language question ("where's the caching logic?") without knowing the exact symbol name, and get relevant results ranked by meaning, not just text match.*

### Build tasks
- [ ] `ai/client.py`: thin Ollama REST wrapper (`/api/embed`, `/api/generate`)
- [ ] `ai/embeddings.py`: embed `File`/`Class`/`Function`/`Method` nodes via `qwen3-embedding:0.6b` — embed `"{qualified_name}\n{signature}\n{leading_comment_or_docstring}"`, not full source; only re-embed nodes whose content hash changed
- [ ] `sqlite-vec` virtual table `(node_id, embedding)`
- [ ] New MCP tool: `ibwd_search(query: str, top_k=10)` — vector similarity search over entity embeddings
- [ ] Extend `CLAUDE.md`: "for vague/conceptual questions where you don't know the exact symbol name, use `ibwd_search` before Grep"

### Claude Code prompt
```
Implement Sprint 5. Add ai/client.py wrapping Ollama's /api/embed. Add
ai/embeddings.py embedding each File/Class/Function/Method node with
qwen3-embedding:0.6b using "{qualified_name}\n{signature}\n
{leading_comment_or_docstring}" as input text, only re-embedding nodes whose
content_hash changed. Add a sqlite-vec virtual table keyed by node_id. Add
ibwd_search(query, top_k=10) to mcp/server.py doing cosine similarity search,
returning {name, kind, file, line, score}. Write an integration test:
embed this repo, search for a natural-language description of a known
function's purpose, assert it's in the top 5 results (not necessarily top 1
-- that's fine for a pure-vector baseline before Sprint 7's ranking).
```

### Sprint demo — value test
3 tasks using **vague, non-exact-name** queries (this is the point — exact-name queries should still go through Sprint 2/3 tools):
1. "Where is `<some vague functional area, e.g. 'caching' or 'authentication' or 'the incremental scan logic'>` implemented?"
2. "What part of the code handles `<another vague concept>`?"
3. A query using a synonym for a real symbol's actual name (e.g. if the function is `fetch_recent_commits`, ask "where do we pull the latest git history from?")

Baseline: `Grep` with best-guess keywords (this is what a grep-only agent actually does for vague questions — often multiple guesses). **Target: higher first-try hit rate**, not necessarily fewer tokens per call (vector search may cost more per call but should need fewer *guessing* rounds).

### Definition of done
- [ ] All 3 demo tasks: `ibwd_search` finds the right entity in the top 5 on the first call; baseline requires ≥2 grep attempts or misses entirely
- [ ] Re-embedding after a no-op scan re-embeds 0 nodes
- [ ] Logged to `benchmarks/sprint_5_results.csv`

---

## Sprint 6 — Semantic Understanding

**Sprint goal:** *As Claude Code, I can ask what a component is responsible for and get a trustworthy answer without reading the full file — and I can tell the difference between a stated fact and an inferred guess.*

### Build tasks
- [ ] `ai/summarizer.py`: `qwen3-coder:30b` via `/api/generate` with `format: json`, one entity at a time, producing `{summary, responsibilities[], architectural_role, concepts[]}`; only summarize entities whose content hash changed
- [ ] Store as `summaries` rows linked to source node, stamped with the `content_hash` at generation time (auto-invalidates on change)
- [ ] `RELATES_TO`/`RESPONSIBLE_FOR`/`SIMILAR_TO` edges with `confidence<1.0`, `source_type=llm_inference`
- [ ] `ai/grader.py`: verify each inferred edge's referenced nodes exist in the structural graph and flags/downgrades ones that contradict a `static_analysis` fact; log what got dropped/downgraded
- [ ] New MCP tool: `ibwd_explain(symbol)` → stored summary + responsibility + confidence/source_type on every claim
- [ ] Extend `CLAUDE.md`: "`ibwd_explain` results tagged `llm_inference` are hypotheses — verify against source before relying on them for an edit"

### Claude Code prompt
```
Implement Sprint 6. Add ai/summarizer.py calling qwen3-coder:30b via
/api/generate with format=json, one entity at a time, only for entities
whose content_hash changed since last summarized. Store results linked to
their source node, stamped with derived_from_hash. Add semantic edges
(RELATES_TO, RESPONSIBLE_FOR, SIMILAR_TO) confidence<1.0,
source_type=llm_inference. Add ai/grader.py checking each inferred edge's
referenced nodes exist and flagging/downgrading edges contradicting a
static_analysis edge -- log every drop/downgrade to a queryable table for
audit. Add ibwd_explain(symbol) to mcp/server.py returning the summary with
confidence and source_type visible on every field. Test: inject one
obviously-wrong inferred edge and confirm the grader catches it; confirm
re-running with zero code changes triggers zero new LLM calls.
```

### Sprint demo — value test
3 tasks:
1. "What is `<a real class>` responsible for?"
2. "What architectural role does `<a real module>` play (controller/service/repository/etc.)?"
3. Manually audit 10 random summaries against the actual source — grade each 1–5 for accuracy.

Baseline: Claude reads the full file and summarizes it live. Compare tokens (should be far lower via `ibwd_explain`) and compare the *quality* of the two summaries side by side (this is the one place where "cheaper" isn't automatically "as good" — check it honestly).

### Definition of done
- [ ] Manual audit of 10 summaries averages ≥4/5 accuracy
- [ ] Grader demonstrably catches the injected bad edge in the test
- [ ] Editing one file invalidates only that file's summary
- [ ] Logged to `benchmarks/sprint_6_results.csv`, including the honest quality comparison, not just token counts

---

## Sprint 7 — Context Optimization

**Sprint goal:** *As Claude Code, when I ask a broad question, I get a small, budget-fitted, priority-ranked context blob instead of an unranked dump of everything that matched — so complex multi-hop questions don't blow the context window.*

### Build tasks
- [ ] `retrieval/ranking.py`: PageRank (via `networkx`) over `CALLS`/`IMPORTS` edges, optionally personalized toward recently-touched files (from Sprint 4's git data) — like Aider's repo-map ranking
- [ ] Fuse: `score = w1*vector_similarity + w2*centrality + w3*(1/graph_distance)` (equal weights to start)
- [ ] `retrieval/context_builder.py`: given ranked candidates + a token budget, binary-search the cutoff; emit signatures + one-line summaries for most items, full source only for the top 1–3
- [ ] Wire this into `ibwd_search` and `ibwd_impact` as the **default** output mode (raw/unranked available via a flag, for the benchmark harness to compare against)
- [ ] (Optional, if GPU headroom allows) add `dengcao/Qwen3-Reranker-0.6B` as a final cross-encoder pass over the top-N

### Claude Code prompt
```
Implement Sprint 7. Add retrieval/ranking.py: PageRank over CALLS/IMPORTS
via networkx, personalization vector weighted toward files touched in
recent commits. Fuse vector_similarity + centrality + inverse graph-distance
into one score (equal weights, exposed as tunable constants). Add
retrieval/context_builder.py: given ranked candidates and a token budget,
binary-search how many fit, emit signature+one-line-summary for all but the
top 1-3 (full source for those). Make this the default output of
ibwd_search and ibwd_impact, with a raw=True flag to bypass it. Test that
output never exceeds the requested budget across 1k/5k/20k token budgets,
and that a known "hub" symbol (many callers) ranks above a rarely-used
private helper for the same query.
```

### Sprint demo — value test
1 complex, realistic multi-hop task (deliberately harder than earlier sprints): "Add caching to `<a real service in this repo>`" or "I want to refactor `<a widely-used module>` — what do I need to understand first?"

Run three ways: (a) baseline glob/grep/read, (b) Sprint 6 unranked `ibwd_impact`+`ibwd_search` raw dump, (c) Sprint 7 ranked+budgeted output. **Target: (c) achieves equal-or-better task quality than (b) at a meaningfully smaller token budget, and beats (a) on both tokens and quality.**

### Definition of done
- [ ] Context builder output never exceeds the requested budget (tested at 3 budget sizes)
- [ ] Hub-symbol ranking check passes
- [ ] The 3-way demo comparison logged to `benchmarks/sprint_7_results.csv` with a written note on task quality, not just tokens

---

## Sprint 8 — Full Agent Integration & Regression Benchmark

**Sprint goal:** *As Claude Code, I have a finished, documented integration (CLAUDE.md + slash commands), and there's a real, rigorous benchmark proving whether the whole system was worth building.*

### Build tasks
- [ ] Finalize `CLAUDE.md` (Appendix C) — keep it short (~200 lines), explicit about which tool to use for which question type (this has been built incrementally since Sprint 1; now consolidate and trim)
- [ ] Thin slash-command wrappers in `.claude/commands/`: `graphify.md` → `ibwd_scan`, `graph.md` → `ibwd_search`, `impact.md` → `ibwd_impact` (UX convenience only — MCP tools remain primary)
- [ ] `benchmarks/harness.py`: replay **every sprint's demo tasks** (they're already logged sprint-by-sprint — this sprint aggregates them) plus a fresh held-out task set, across three conditions: (a) vanilla Claude Code (glob/grep/read only), (b) embedding-only baseline (`ibwd_search` with graph traversal/ranking disabled), (c) full IBWD
- [ ] Split every task by **structural** (Sprints 1–4 style) vs **source-level** (needs full file bodies, exhaustive text search) — report separately, never blend into one number
- [ ] `benchmarks/RESULTS.md`: the three-condition comparison table, split by task type, plus honest caveats (you're likely the only grader, small task set, single repo)

### Claude Code prompt
```
Implement Sprint 8. Finalize CLAUDE.md per Appendix C, consolidating the
guidance accumulated across all 7 previous sprints into one coherent, short
document. Add .claude/commands/{graphify,graph,impact}.md as thin wrappers
around the ibwd_scan/ibwd_search/ibwd_impact MCP tools. Build
benchmarks/harness.py that loads all sprint_*_results.csv files plus a new
benchmarks/tasks.yaml of held-out tasks (question, task_type:
structural|source_level, success condition), runs each under the three
conditions described in EXECUTION_PLAN.md Sprint 8, and logs
tokens/tool-calls/files-read/latency/pass-fail. Generate
benchmarks/RESULTS.md with the comparison table split by task_type and an
honest limitations section.
```

### Sprint demo — value test (final go/no-go)
- [ ] **Structural tasks:** IBWD uses ≥5x fewer tokens than vanilla at equal-or-better pass rate, and beats the embedding-only baseline (proof the *graph*, not just embeddings, is doing the work)
- [ ] **Source-level tasks:** IBWD is within ~10–20% of vanilla pass rate (fine to not win, must not badly regress)

### Definition of done
- [ ] All 8 sprints' `benchmarks/sprint_*_results.csv` files exist and are aggregated
- [ ] `RESULTS.md` states the structural-vs-source-level split explicitly, never a single blended headline number
- [ ] `claude mcp list` shows IBWD connected in a fresh clone after `uv sync` + one `ibwd scan`
- [ ] README states the hypothesis, links `RESULTS.md`, and gives the honest verdict

---

## What NOT to build in v1 (revisit only after Sprint 8 passes)
Neo4j / any graph DB beyond SQLite · multi-agent orchestration · 20+ language support · autonomous code modification · full GitHub PR graph · a web frontend · fine-tuning a model · a distributed database.

---

## Appendix A — Repo layout

```
ibwd/
├── README.md
├── EXECUTION_PLAN.md
├── CLAUDE.md
├── pyproject.toml
├── LICENSE
├── .gitignore
├── .claude/
│   └── commands/
│       ├── graphify.md
│       ├── graph.md
│       └── impact.md
├── src/ibwd/
│   ├── __init__.py
│   ├── cli.py
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── filesystem.py
│   │   ├── python.py
│   │   └── javascript.py
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── schema.sql
│   │   ├── database.py
│   │   └── queries.py
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── embeddings.py
│   │   ├── summarizer.py
│   │   └── grader.py
│   ├── git/
│   │   ├── __init__.py
│   │   └── history.py
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── traversal.py
│   │   ├── ranking.py
│   │   └── context_builder.py
│   └── mcp/
│       ├── __init__.py
│       └── server.py      # shipped Sprint 1, extended every sprint after
├── tests/
└── benchmarks/
    ├── harness.py
    ├── timing.py
    ├── tasks.yaml
    ├── sprint_1_results.csv
    ├── sprint_2_results.csv
    ├── ...                 # one per sprint, aggregated in Sprint 8
    └── RESULTS.md
```

## Appendix B — Starter SQLite schema

```sql
-- graph/schema.sql
CREATE TABLE IF NOT EXISTS nodes (
    id            INTEGER PRIMARY KEY,
    node_type     TEXT NOT NULL,   -- File, Directory, Class, Function, Method, Commit, ...
    name          TEXT NOT NULL,
    qualified_name TEXT,
    file_path     TEXT,
    start_line    INTEGER,
    end_line      INTEGER,
    content_hash  TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_hash ON nodes(content_hash);

CREATE TABLE IF NOT EXISTS edges (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES nodes(id),
    target_id     INTEGER NOT NULL REFERENCES nodes(id),
    relation      TEXT NOT NULL,   -- CONTAINS, DEFINES, IMPORTS, CALLS, INHERITS, REFERENCES, TOUCHED, TESTED_BY, RELATES_TO, ...
    confidence    REAL NOT NULL DEFAULT 1.0,
    source_type   TEXT NOT NULL DEFAULT 'static_analysis',  -- static_analysis | llm_inference
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);

-- Added Sprint 6:
CREATE TABLE IF NOT EXISTS summaries (
    node_id       INTEGER PRIMARY KEY REFERENCES nodes(id),
    summary       TEXT,
    responsibilities TEXT,
    architectural_role TEXT,
    concepts      TEXT,
    derived_from_hash TEXT,
    grader_status TEXT DEFAULT 'unchecked'  -- unchecked | passed | downgraded | dropped
);

-- Added Sprint 5 (via the sqlite-vec extension):
-- CREATE VIRTUAL TABLE vec_nodes USING vec0(node_id INTEGER PRIMARY KEY, embedding FLOAT[1024]);

-- Recursive CTE example, added Sprint 3, "callers of node N up to depth 3":
-- WITH RECURSIVE callers(id, depth) AS (
--   SELECT source_id, 1 FROM edges WHERE target_id = :node_id AND relation = 'CALLS'
--   UNION
--   SELECT e.source_id, c.depth + 1 FROM edges e
--   JOIN callers c ON e.target_id = c.id
--   WHERE e.relation = 'CALLS' AND c.depth < 3
-- )
-- SELECT DISTINCT id, depth FROM callers ORDER BY depth;
```

> **Added after Sprint 3:** a `file_refs(file_path, content_hash, version, refs_json)` cache table and a `REFERENCES` edge relation; File `kind` also takes `vendor` / `generated`. See `SCHEMA.md`.

## Appendix C — CLAUDE.md (built incrementally, shown as the Sprint 8 final version)

```markdown
## IBWD — codebase memory

This repo has an IBWD MCP server providing structural + semantic queries
over the codebase (tree-sitter-derived facts + local-LLM-inferred summaries).

**Use IBWD tools first for:**
- File discovery/categorization -> ibwd_find_files          (Sprint 1)
- "Where is X defined?" -> ibwd_find_symbol, ibwd_list_symbols (Sprint 2)
- "What calls/imports X?" -> ibwd_callers, ibwd_dependents    (Sprint 3)
- "What breaks if I change X?" / "what tests cover X?" -> ibwd_impact, ibwd_tests_for (Sprint 4)
- Vague/conceptual questions (no exact name) -> ibwd_search   (Sprint 5)
- "What is X responsible for?" -> ibwd_explain                (Sprint 6)
- Broad/complex multi-hop questions -> ibwd_search / ibwd_impact
  already return ranked, budgeted context by default          (Sprint 7)

**Fall back to Glob/Grep/Read for:**
- Reading full file contents or exact source needed to make an edit
- Exhaustive text search (e.g. every place a string literal appears)
- Anything IBWD returns with low confidence or source_type=llm_inference —
  treat those as hypotheses, not facts

**Always:**
- Verify anything IBWD reports (especially llm_inference edges) against the
  actual source at the given file:line before relying on it for an edit
- Run `ibwd_scan` (or `/graphify`) if results look stale relative to recent changes
- Never treat the graph as ground truth — it is a map to speed up navigation,
  not a replacement for reading the code you're about to change
```

## Appendix D — Generic Sprint Demo Protocol

Repeat this at the end of every sprint; it's what makes the plan "agile-testable" instead of just phased.

1. Write the sprint's demo questions (given in each sprint section) into `benchmarks/sprint_N_tasks.yaml`, with a note on the expected/ground-truth answer since you already know this repo.
2. Run each question in a **fresh Claude Code session**, twice:
   - **Baseline:** `claude --allowedTools "Read,Glob,Grep"` (or the current equivalent flag) — no IBWD tools.
   - **With IBWD:** allow the tool(s) shipped this sprint in addition.
3. Record per run: input+output tokens (Claude Code reports this, or use `/cost`), number of tool calls, and correctness (grade by hand against the ground truth you noted in step 1).
4. Append to `benchmarks/sprint_N_results.csv`: `task, condition, tokens_in, tokens_out, tool_calls, correct(y/n)`.
5. Check the sprint's go/no-go threshold (see the Sprint board table). If it fails, treat it as a retro item — fix the current sprint's retrieval quality before building the next layer on top of it.
6. These CSVs accumulate for free across sprints and become Sprint 8's regression suite — you never have to write the benchmark from scratch at the end.
