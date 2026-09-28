# Addendum — Path Finding (`ibwd_trace_path`)

> **Historical design note.** Current path semantics and limitations are in [KNOWN_LIMITATIONS.md](../../reference/KNOWN_LIMITATIONS.md);
> future work follows [the revised roadmap](../../development/ROADMAP.md).
> Empty graph results never establish deletion safety. The cosine-A* proposal below is not an accepted relevance guarantee;
> an admissible heuristic preserves the existing objective, while an arbitrary one may lose optimality.

> **Integration note for Claude Code:** this is not a new sprint number. It
> fills a gap inside **Sprint 3 — Call Graph & Dependencies**: that sprint's
> own demo test #3 ("Trace the call chain from `<function A>` to
> `<function B>` if one exists") is never actually built — the Sprint 3
> build task list only ships `ibwd_callers`/`ibwd_dependents` (one-hop
> queries), not a path-finder between two arbitrary nodes. Merge the
> **Build tasks** and **Claude Code prompt** sections below directly into
> Sprint 3's own sections in `IBWD_v1_EXECUTION_PLAN.md`, after
> `ibwd_dependents`. The **Sprint 5 stretch** section at the bottom is
> separate — merge it into Sprint 5 once that sprint is reached, not before.

## Why Dijkstra/A*, not a raw recursive CTE

Sprint 3 already uses recursive CTEs (Appendix B) for `callers_of`/
`dependents_of` — plain unweighted BFS expansion, which SQL recursive CTEs
handle well. A *weighted shortest path* (needed here, since `CALLS` edges
carry `confidence` 0.35–0.95 from the resolution cascade and a path should
prefer high-confidence hops) is awkward to express as a recursive CTE —
SQL has no native priority queue. Building the relevant subgraph in memory
with `networkx` and calling its shortest-path functions is simpler and
well-tested, at a repo-sized graph (thousands, not millions, of nodes) this
is effectively instant either way. **This does mean adding `networkx` to
`pyproject.toml` now, rather than waiting for Sprint 7** where it was
originally planned for PageRank — a small, low-risk dependency pull-forward.

## Design

- Edge weight = `1 / confidence` — a path through two `conf=0.95` edges
  costs less than one through a single `conf=0.35` fuzzy-matched edge, so
  the search naturally prefers verified-looking routes over guessed ones.
- Default heuristic is `0` (i.e., this is plain Dijkstra to start, wrapped
  in `nx.astar_path` so the call site never has to change when a real
  heuristic exists) — see the Sprint 5 stretch section for the upgrade.
- No new node or edge types, no schema changes — this only reads `CALLS`/
  `IMPORTS` edges Sprint 3 already writes.
- Explicit "no path exists" is a valid, useful answer, not an error — the
  existing Sprint 4 demo task 3 already establishes that a true negative
  ("this is safe to delete, nothing calls it") is as valuable as a hit;
  same principle applies here.

## Build tasks (add to Sprint 3's list)

- [ ] `retrieval/traversal.py`: extend with `build_call_subgraph(conn, edge_types=("CALLS","IMPORTS"))` — loads matching edges into an in-memory `networkx.DiGraph`, node attrs `{name, file_path, kind}`, edge weight `1/confidence`
- [ ] `find_path(graph, source_id, target_id, heuristic=None)` — wraps `nx.astar_path(graph, source_id, target_id, heuristic=heuristic or (lambda u, v: 0), weight="weight")`; catches `nx.NetworkXNoPath` and returns `None` rather than raising
- [ ] New MCP tool: `ibwd_trace_path(source: str, target: str, edge_types=["CALLS","IMPORTS"])` — resolves both symbol names via the existing `find_symbol` (reusing Sprint 2's exact-then-fallback matching, so an ambiguous name here behaves consistently with every other tool), builds the subgraph, runs `find_path`, returns an ordered list of `{name, file, line, edge_type, confidence}` hops plus total path cost, or `{"path": null, "reason": "no path found"}`
- [ ] Extend `CLAUDE.md`: "for 'how does A reach B' / 'is A connected to B' questions, prefer `ibwd_trace_path` over manually calling `ibwd_callers`/`ibwd_dependents` at increasing depth"

## Claude Code prompt

```
Add path-finding to Sprint 3. In retrieval/traversal.py, add
build_call_subgraph(conn, edge_types=("CALLS","IMPORTS")) loading matching
edges into a networkx.DiGraph with edge weight = 1/confidence, and
find_path(graph, source_id, target_id, heuristic=None) wrapping
nx.astar_path with heuristic defaulting to a zero function (plain Dijkstra
for now), catching nx.NetworkXNoPath and returning None. Add
ibwd_trace_path(source, target, edge_types=["CALLS","IMPORTS"]) to
mcp/server.py: resolve both names via the existing find_symbol (reuse its
exact-then-substring matching and ambiguity handling), build the subgraph,
run find_path, return an ordered list of {name, file, line, edge_type,
confidence} per hop plus total cost, or {"path": null, "reason": "no path
found"} if none exists. Add networkx to pyproject.toml. Test: on a fixture
with a known 3-hop call chain A->B->C->D, confirm ibwd_trace_path(A, D)
returns exactly that path; on a fixture with no connecting path, confirm it
returns null rather than raising; on a fixture where a low-confidence
shortcut edge and a high-confidence longer route both exist, confirm the
search prefers the higher-confidence route where hop count is comparable.
```

## Sprint demo — value test

Reuses Sprint 3's existing demo task 3 rather than adding a new one:
1. "Trace the call chain from `<function A>` to `<function B>` if one exists."
2. A pair with **no** connecting path — confirms `ibwd_trace_path` correctly reports "no path" rather than timing out or erroring.
3. A pair with **multiple** valid paths of different confidence — confirms the tool surfaces the higher-confidence route, and that the response makes each hop's confidence visible rather than hiding it.

Baseline: manually calling `ibwd_callers`/`ibwd_dependents` at `depth=1,2,3...` and reasoning in natural language about whether the target appears yet. **Target: 1 tool call instead of 3+ manual depth-increasing calls, correctness confirmed on both the has-a-path and no-path cases.**

## Definition of done

- [ ] `ibwd_trace_path` returns the correct path on a known 3-hop fixture
- [ ] Returns `null`/`"no path found"` cleanly on a genuinely disconnected pair — no exception surfaced to the caller
- [ ] Given two paths of comparable hop count but different confidence, returns the higher-confidence one
- [ ] Logged to `benchmarks/sprint_3_results.csv` alongside the existing `ibwd_callers`/`ibwd_dependents` rows, not a separate file

---

## Sprint 5 stretch — upgrade to a real A* heuristic (optional, do not build before Sprint 5 ships)

Once `ai/embeddings.py` exists, `find_path`'s `heuristic` argument can become a genuine estimate instead of the placeholder zero function:

```python
def embedding_heuristic(target_id, embeddings):
    target_vec = embeddings[target_id]
    def h(node_id):
        return 1 - cosine_similarity(embeddings[node_id], target_vec)
    return h
```

This changes *which* path gets returned when several structurally-equal
routes exist, preferring the one passing through semantically closer
intermediate nodes — a **correctness/relevance** change, not a speed one
(see the performance note below for why). Test this specifically, not just
"does it run": on a fixture with two hop-count-equal paths between A and B,
where one passes through nodes conceptually related to the query and the
other doesn't, confirm the heuristic-guided search prefers the relevant
one. Grade a handful by hand, same discipline as Sprint 6's manual summary
audit — an "it ran without crashing" test alone won't tell you if the
heuristic is actually good.

---

## Performance — the honest expectation

**This will likely help your actual success metric (tokens/tool-calls),
but for a narrower reason than "A* is fast":**

- The win comes from having *any* dedicated path tool at all — it replaces
  the same multi-call, reason-between-each-call pattern that Sprint 1's
  benchmark already proved is expensive (Q1: 5 calls → 1). `ibwd_trace_path`
  applies that exact same fix to one specific, currently-unserved query
  shape: "is A connected to B, and how."
- **It's narrow, not general.** It only helps "is A connected to B"
  questions — it does nothing for file discovery, plain symbol lookup, or
  "what calls X" without a specific target. Don't expect it to move your
  aggregate benchmark numbers the way Sprint 1–2's indexing did across
  nearly every query type.
- **A* specifically, versus plain Dijkstra or BFS, will not show up in your
  token/tool-call metric at all.** That metric counts tool calls and output
  tokens from Claude Code's perspective — one call either way. The
  algorithm choice only affects how fast the tool executes *inside* the
  MCP server, and at a single-repo graph's size that's already
  sub-millisecond regardless of which of the three you pick. Don't expect
  a benchmark line item for "A* vs Dijkstra speed" to mean anything here.
- **Where A* actually earns its name over Dijkstra** is the Sprint 5
  stretch above — once there's a real heuristic, it can change *which*
  path gets returned, which is a quality/relevance improvement worth
  benchmarking on its own terms, separately from the base tool's win.
  Until then, what you're building is Dijkstra wearing an A*-shaped
  interface, which is a perfectly reasonable and honest way to build it —
  just don't market it internally as "faster" when what it actually is,
  for now, is "correct and ready for the heuristic later."

**Bottom line:** build the base tool now — it's a real, provable win on
your own metric. Treat the A* heuristic itself as a Sprint 5 quality
experiment, benchmarked and reported the same honest way your other
sprints already are, not assumed to help just because A* is a fancier name
than Dijkstra.
