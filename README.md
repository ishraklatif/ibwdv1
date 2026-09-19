# IBWD — Persistent Codebase Memory for AI Coding Agents

**IBWD** builds a persistent, deterministically-derived structural + semantic graph of a codebase and exposes it to Claude Code as an MCP server — so the agent can answer "where is X defined," "what calls X," and "what breaks if I change X" from a sub-millisecond graph query instead of repeated `Glob`/`Grep`/`Read` exploration.

> **Status:** pre-implementation. The repo currently contains only the research/plan documents and a bare scaffold (`src/ibwd/`, `pyproject.toml`). Nothing has been built yet — this README describes the target design from those planning documents.

## Core hypothesis

> A persistent structural + semantic graph lets an AI coding agent solve repository tasks using substantially less context than repeatedly exploring the repository, while maintaining or improving task success.

This is a hypothesis under active test, not an assumed fact. Every sprint in the build plan ends with an A/B benchmark (IBWD-assisted vs. vanilla `Glob`/`Grep`/`Read`) that must clear a go/no-go token/quality threshold before the next layer gets built. If the structural layer (Sprints 1–4) doesn't clearly win on tokens, the plan calls for stopping and fixing retrieval before adding any LLM semantics.

## Golden rule

Deterministic, tree-sitter-derived facts are tagged `confidence=1.0, source_type=static_analysis`. LLM-inferred facts (summaries, responsibility claims, semantic relations) are tagged `confidence<1.0, source_type=llm_inference`. **Claude must verify graph claims against actual source before editing anything.** The graph is a map, never the territory — this mirrors Anthropic's own caution that Claude Code's glob/grep approach deliberately avoids stale-index risk, so IBWD has to earn its keep with measured wins, not by being trusted blindly.

## Why this approach (research grounding)

The design is grounded in a survey of prior art (`compass_artifact_wf-986c57d9-dd01-5ddb-b7d2-5e0ca7444c11_text_markdown.md`) rather than invented from scratch:

- **Codebase-Memory** (arXiv:2603.27277) — the closest published prior art, essentially IBWD's structural layer as an MCP server. Across 31 repos it reports ~10× fewer tokens, 2.1× fewer tool calls, and sub-millisecond queries vs. a file-explorer agent, at ~90% of its answer quality — but it *loses* on tasks needing full source or exhaustive text search. Its own conclusion: **the right architecture is hybrid** — graph-first for structural queries, file-fallback for source-level tasks. IBWD adopts this hybrid stance directly.
- **Aider's repo map** — the precedent for tree-sitter parsing + graph-ranking (personalized PageRank, signature-only elided views fitted to a token budget). IBWD borrows the ranking approach but adds persistence, since Aider rebuilds its map in-memory on every run.
- **Cursor / Continue.dev** — validate content-hash incremental indexing (Merkle-tree-style) and confirm that pure embedding search degrades as codebases grow ("embedding search becomes unreliable as a retrieval heuristic as the size of the codebase grows" — Windsurf's Varun). This is why IBWD layers graph traversal on top of embeddings rather than relying on embeddings alone.
- **Anthropic's context-engineering guidance** — the philosophical backbone: minimize tokens spent per unit of usefulness, treat context as a scarce resource, and note that Claude Code already does hybrid upfront (CLAUDE.md) + just-in-time (glob/grep) retrieval. Anthropic explicitly names stale indexing as a reason it *avoids* structural graphs by default — which is the bar IBWD has to clear with evidence, not assumption.

Full findings, citations, and the corrected tech-stack table (vs. an earlier draft plan) live in `compass_artifact_wf-986c57d9-dd01-5ddb-b7d2-5e0ca7444c11_text_markdown.md`.

## Architecture

```
                        ┌─────────────────────────────────────────────┐
                        │                 IBWD engine                  │
                        │                                              │
  ┌──────────┐  scan    │  ┌────────────┐   deterministic (conf=1.0)  │
  │  Git repo │─────────▶│  │ tree-sitter │──▶ imports / calls /        │
  │ (Py,JS/TS)│          │  │  parser     │    classes / inheritance   │
  └──────────┘  SHA256/  │  └────────────┘         │                   │
        │       XXH3     │                         ▼                   │
        │  (changed      │   ┌───────────────────────────────────┐    │
        │   files only)  │   │        SQLite graph store          │    │
        │                │   │  nodes / edges (+conf, +src_type,  │    │
        │                │   │   +file:line, +content_hash)       │    │
        │                │   │  + sqlite-vec vector index         │    │
        │                │   └───────────────────────────────────┘    │
        │                │        ▲                    │               │
        │  qwen3-coder:30b│  summaries (conf<1,         │ recursive CTE │
        │  (Ollama)  ─────┼──▶ src_type=llm_inference)  │ traversal     │
        │                │        │   ▲ hallucination   ▼               │
        │  qwen3-embedding│        │   │ grader   ┌──────────────┐      │
        │  (Ollama)  ─────┼──▶ embeddings ────────▶│ retrieval:   │      │
        │                │                         │ vector →     │      │
        │                │                         │ graph-hops → │      │
        │                │                         │ rerank →     │      │
        │                │                         │ ctx builder  │      │
        │                │                         └──────┬───────┘      │
        └────────────────┴────────────────────────────────┼──────────────┘
                                                           │ MCP tools
                                    ┌──────────────────────▼───────────────┐
                                    │  Claude Code (final reasoning/coding) │
                                    │  ibwd_impact / ibwd_callers / ...     │
                                    │  → verify graph claims vs source      │
                                    │  → fall back to glob/grep for source  │
                                    └───────────────────────────────────────┘
```

**Pipeline:** a repo scan hashes files (XXH3/SHA256) and only reprocesses what changed → tree-sitter extracts structural facts (imports, calls, classes, inheritance) as confidence-1.0 edges → optional local-LLM passes (Ollama) generate embeddings and summaries as confidence-<1.0, `llm_inference`-tagged nodes/edges, gated by a hallucination grader → everything lands in a single SQLite file (graph tables + a `sqlite-vec` vector index) → a retrieval layer (vector search → graph traversal → ranking/reranking → token-budgeted context builder) serves results → Claude Code consumes it all through typed MCP tools, and is instructed to verify anything graph-derived against real source before editing.

### Why MCP, not just slash commands

Every comparable 2025–2026 code-graph project (Codebase-Memory, CodeGraphContext, code-graph-mcp, Code-Graph-RAG) ships as an MCP server, and slash commands have effectively been superseded by the skills model in Claude Code. IBWD follows suit: the **MCP server is the primary interface**, shipped starting in Sprint 1 with a single tool and extended every sprint after. Thin slash-command wrappers (`/graphify`, `/graph`, `/impact`) exist only as ergonomic convenience on top of the same MCP tools.

## Data model

**Node types:** `Project`, `Package`, `Folder`, `File`, `Module`, `Class`, `Function`, `Method`, `Interface`/`Type`, plus semantic nodes (`Summary`, `Responsibility`, `Component`/`ADR`) and historical nodes (`Commit`, `PR`).

**Edge types:**
- Structural, `confidence=1.0`, `source_type=static_analysis`: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `INHERITS`, `IMPLEMENTS`, `DECORATES`
- Semantic, `confidence<1.0`, `source_type=llm_inference`: `RELATES_TO`, `RESPONSIBLE_FOR`, `SIMILAR_TO`
- Historical: `TOUCHED` (commit→file), `TESTED_BY` (test→symbol)

Every node stores an exact `file:line` and the `content_hash` it was derived from. Every edge stores `confidence` + `source_type`, so results can be filtered by authority and invalidated automatically when source changes. `CALLS` edges specifically go through a 5-tier resolution cascade (import-map match → same-module → unique-name-in-repo → suffix match → fuzzy match), with confidence reflecting resolution certainty (0.95 down to 0.35), not authorship — the fact itself is still static analysis.

See `IBWD_v1_EXECUTION_PLAN.md` Appendix B for the concrete SQLite schema (`nodes`, `edges`, `summaries` tables, plus the `sqlite-vec` virtual table and a worked recursive-CTE example for "callers of node N up to depth 3").

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Structural parsing | **tree-sitter** (Python + JS/TS in v1) | Single consistent CST pipeline, exact source positions, incremental, 130+ languages available if extended later. Used by Aider/Cursor/Continue. |
| Graph storage | **SQLite** | Recursive CTEs do graph traversal (BFS callers/dependents) in sub-millisecond time; zero server; single-file; portable. Kuzu was considered and rejected — its repo was archived Oct 2025 after Apple's acquisition of Kùzu Inc. |
| Vector store | **sqlite-vec** (same SQLite file) | No separate server; embeddings live alongside the graph; "deploying a new index is copying a file." LanceDB is the fallback if this is outgrown. |
| Embeddings | **`qwen3-embedding:0.6b`** (Ollama), scalable to 4b/8b | Official Ollama model; the 8B variant ranks #1 on MTEB multilingual and leads MTEB-Code; Apache 2.0; Matryoshka dimensions let you scale precision to hardware. |
| Summarization LLM | **`qwen3-coder:30b`** (Ollama) | 30.5B total / 3.3B active MoE, ~19GB at Q4_K_M, 256K context. Falls back to `devstral-small:24b` or `gpt-oss:20b` on lighter hardware. |
| Reranking | **`dengcao/Qwen3-Reranker-0.6B`** (community GGUF) or deferred | No first-class official Ollama reranker yet; v1 can fuse vector-score + graph-distance instead of a dedicated reranker pass. |
| Agent integration | **MCP server**, Python (official Anthropic MCP SDK) | Idiomatic 2026 Claude Code integration; typed, token-efficient, self-contained tools per Anthropic's tool-design guidance. |
| Incremental updates | **XXH3/SHA256 content hashing** + manifest | Only changed files get reprocessed; mirrors Cursor's Merkle-diff approach and Codebase-Memory's ~1.2s incremental re-index. |

Rejected/deferred: Neo4j and other graph DBs (SQLite CTEs suffice at this scale), Kuzu (archived), ChromaDB (HNSW corruption on abrupt exit), multi-agent orchestration, 20+ language support, autonomous code modification, fine-tuning.

## MCP tools (by sprint)

| Tool | Ships in | Answers |
|---|---|---|
| `ibwd_scan`, `ibwd_find_files` | Sprint 1 | "Which files are tests/configs/X?" |
| `ibwd_find_symbol`, `ibwd_list_symbols` | Sprint 2 | "Where is X defined?" |
| `ibwd_callers`, `ibwd_dependents`, `ibwd_trace_path` | Sprint 3 | "What calls/imports X? What does X depend on? How does A reach B?" |
| `ibwd_impact`, `ibwd_tests_for` | Sprint 4 | "What breaks if I change X? What tests cover it?" |
| `ibwd_search` | Sprint 5 | Vague/conceptual queries without an exact symbol name |
| `ibwd_explain` | Sprint 6 | "What is X responsible for?" (with confidence/source_type on every claim) |
| *(upgrade)* ranked + budgeted output | Sprint 7 | Complex multi-hop questions, fit to a token budget |

Every tool response includes exact `file:line`, `confidence`, and `source_type` so Claude can decide how much to trust a result and where to go verify it.

## Build plan: 8 sprints, not phases

The plan is agile rather than linear on purpose: the MCP server ships in **Sprint 1** with one working tool, and every sprint after adds exactly one capability. Each sprint ends with a real **A/B demo** — the same question asked with and without that sprint's new tool, tokens and tool-calls logged to `benchmarks/sprint_N_results.csv` — so there's a go/no-go signal at every step instead of only at the end.

| Sprint | Ships | Go/no-go signal |
|---|---|---|
| 1 | File/dir index | Fewer tool calls than `Glob` |
| 2 | Symbol index | Fewer tokens than `Grep`, especially on common names |
| 3 | Call graph | ≥3× fewer tokens than manual grep-tracing |
| 4 | Impact analysis | ≥3–5× fewer tokens, equal/better correctness |
| 5 | Semantic search | Higher first-try hit rate than grep on vague queries |
| 6 | Semantic understanding | Matches manual-read accuracy, far fewer tokens |
| 7 | Context optimization | Equal/better task success at a smaller fixed token budget |
| 8 | Full agent integration | Structural ≥5× tokens saved; source-level within ~10–20% of baseline |

**Checkpoint at Sprint 4:** this is the deepest test of the pure structural layer (no LLM semantics yet). If Sprints 1–4 aren't clearly winning on tokens by then, the plan says stop and fix structural retrieval — layering summaries on a weak foundation won't rescue the numbers.

Full sprint-by-sprint build tasks, exact Claude Code prompts, demo protocols, and definitions of done are in `IBWD_v1_EXECUTION_PLAN.md`.

## Evaluation approach

Every benchmark separates tasks into **structural** (impact analysis, "what calls X", dependency chains) vs. **source-level** (full-file understanding, exhaustive text search) and reports them separately rather than as one blended number — this is the key lesson from Codebase-Memory, which wins big on the former and roughly ties/loses on the latter. Sprint 8 aggregates three conditions across the whole task set: (a) vanilla Claude Code (`Glob`/`Grep`/`Read` only), (b) embedding-only retrieval (graph disabled), and (c) full IBWD — specifically to prove the *graph*, not just the embeddings, is doing the work.

## Repo layout (target)

```
ibwd/
├── README.md
├── IBWD_v1_EXECUTION_PLAN.md
├── compass_artifact_..._markdown.md   # research findings behind the design
├── CLAUDE.md                          # built incrementally across sprints
├── pyproject.toml
├── .claude/commands/                  # thin slash-command wrappers (graphify, graph, impact)
├── src/ibwd/
│   ├── cli.py
│   ├── scanner/        # filesystem walk, tree-sitter extraction (python.py, javascript.py)
│   ├── graph/           # schema.sql, database.py, queries.py
│   ├── ai/              # ollama client, embeddings, summarizer, hallucination grader
│   ├── git/             # commit/PR history ingestion
│   ├── retrieval/       # traversal, ranking (PageRank), context builder
│   └── mcp/             # server.py — the MCP tool surface, shipped Sprint 1
├── tests/
└── benchmarks/          # harness.py, tasks.yaml, per-sprint results, RESULTS.md
```

## Getting started (once implementation begins)

```bash
python3 --version          # need 3.11+
curl -LsSf https://astral.sh/uv/install.sh | sh

# Ollama, for embeddings/summarization (needed from Sprint 5 onward)
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &
ollama pull qwen3-embedding:0.6b
ollama pull qwen3-coder:30b

# Claude Code CLI
npm install -g @anthropic-ai/claude-code

uv init --python 3.11
uv sync
ibwd scan
claude mcp add ibwd -- uv run python -m ibwd.mcp.server
```

### Using IBWD with Codex CLI

The server is a standard stdio MCP server, so any MCP-capable client can use it. For Codex:

```bash
codex mcp add ibwd -- uv run python -m ibwd.mcp.server
```

or add to `~/.codex/config.toml`:

```toml
[mcp_servers.ibwd]
command = "uv"            # use the absolute path (see `which uv`) if Codex can't find it
args = ["run", "python", "-m", "ibwd.mcp.server"]
cwd = "/absolute/path/to/the/repo/to/index"
```

The server indexes whatever directory it is launched from (`.ibwd/graph.db` under the working directory), so `cwd` must be the repo you want indexed. Codex reads `AGENTS.md` rather than `CLAUDE.md`; `AGENTS.md` is a symlink to `CLAUDE.md` so the tool-routing guidance stays in one place. The `.claude/commands/` slash-command wrappers are Claude Code-specific; in Codex, call the MCP tools directly.

## Documents in this repo

- **`IBWD_v1_EXECUTION_PLAN.md`** — the authoritative sprint-by-sprint build plan: build tasks, exact Claude Code prompts, demo protocols, go/no-go thresholds, and appendices (repo layout, SQLite schema, `CLAUDE.md` template, benchmark protocol).
- **`compass_artifact_wf-986c57d9-dd01-5ddb-b7d2-5e0ca7444c11_text_markdown.md`** — the research report grounding every architectural decision above in prior art (Codebase-Memory, Aider, Cursor, Continue.dev, Anthropic's context-engineering guidance) and correcting an earlier draft plan's model names/tags.
