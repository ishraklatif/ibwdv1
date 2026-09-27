"""IBWD MCP server — local codebase tools for Codex and Claude Code."""

from __future__ import annotations

from pathlib import Path
import argparse
import os

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except ModuleNotFoundError:  # Stable MCP SDK 1.x calls this class FastMCP.
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp.exceptions import ToolError

from ibwd.graph.database import connect
from ibwd.graph.queries import find_files, find_symbol, list_symbols, resolve_targets
from ibwd.retrieval.traversal import (
    DEFAULT_PATH_EDGE_TYPES,
    TRAVERSAL_RELATIONS,
    Reach,
    build_call_subgraph,
    callers_of,
    dependents_of,
    find_path,
    path_cost,
)
from ibwd.scan import run_scan
from ibwd.setup import ROUTING
from ibwd.telemetry import ObservedServerMixin
from ibwd.retrieval.service import retrieval, readonly

# Ambiguous names (substring fallback) can match many nodes; bound the path search.
MAX_PATH_CANDIDATES = 10

class ObservedMCPServer(ObservedServerMixin, MCPServer):
    pass


mcp = ObservedMCPServer("ibwd", instructions=ROUTING)


@mcp.tool()
def ibwd_context(task: str, targets: list[str] | None = None, budget_tokens: int = 2000,
                 detail: str = 'outline', cursor: str | None = None,
                 scopes: list[str] | None = None, max_bytes: int = 16384, semantic: bool | None = None) -> dict:
    """Find task evidence using lexical search and exact targets, with resolved graph links.

    scopes selects source/test/doc/config (all by default). detail is outline or source.
    Follow next_cursor with the same task/targets/scopes/detail. Candidate caps require
    narrowing or source search. budget_tokens is a four-byte estimate, not provider tokens.
    Source omitted to fit a packet remains available via ibwd_read and its exact hash/range.
    Test matches are verification pointers, not proof of coverage. Freshness is automatic.
    semantic=True opts into a separately built local vector index, with lexical fallback
    on absent/stale/busy/slow models. Exact targets stay first. No downloads or cloud calls.
    Keep semantic unchanged across pages. Omitted semantic uses the repository opt-in
    setting (off until enabled); semantic=False always uses deterministic retrieval.
    """
    from ibwd.retrieval.context import context
    return context(Path.cwd(), task, targets, budget_tokens, detail, cursor, scopes, max_bytes, semantic)


@mcp.tool()
def ibwd_read(symbol_id_or_path: str, expected_hash: str, range: list[int] | None = None,
              budget_tokens: int = 1000, max_bytes: int = 16384) -> dict:
    """Read exact UTF-8 source using a current hash from discovery/context.

    range is optional [start_line, end_line], inclusive and one-based. Without it,
    return the complete symbol or file. Stale hashes and excluded paths fail explicitly.
    Source spans are never silently shortened; request a smaller explicit range if needed.
    budget_tokens is a four-byte estimate of serialized MCP output, not provider tokens.
    """
    from ibwd.retrieval.context import read
    return read(Path.cwd(), symbol_id_or_path, expected_hash, range, budget_tokens, max_bytes)


@mcp.tool()
def ibwd_impact(targets: list[str] | None = None, direction: str = 'incoming',
                relations: list[str] | None = None, depth: int = 2, scopes: list[str] | None = None,
                diff: bool = False, heuristics: bool = True, limit: int = 50,
                max_bytes: int = 16384, cursor: str | None = None) -> dict:
    """Bounded source/test impact paths. Incoming is exposure; outgoing is dependencies.

    References and filename heuristics are not verified coverage or proof of breakage.
    diff compares the previous indexed snapshot with the working tree, preserving old
    and deleted identities. Scan a baseline before editing. Follow generation-bound
    next_cursor with the same query. Empty results never establish safe deletion.
    """
    from ibwd.retrieval.impact import impact
    return impact(Path.cwd(), targets, direction, relations, depth, scopes, diff, heuristics, limit, max_bytes, cursor)


@mcp.tool()
def ibwd_compiler_evidence(file: str, line: int, column: int, project: str = 'tsconfig.json',
                           compiler: str | None = None, max_bytes: int = 16384) -> dict:
    """Opt-in installed TypeScript checker references at a one-based UTF-16 position.

    Uses repository node_modules/typescript or an explicit installed compiler path.
    No downloads. Reports compiler version, project, diagnostics and possible targets
    separately from syntax edges. Missing/incomplete environments remain unknown.
    """
    from ibwd.retrieval.compiler import compiler_evidence
    return compiler_evidence(Path.cwd(), file, line, column, project, compiler, max_bytes)


def _connect_index():
    root = Path.cwd()
    if not (root / ".ibwd" / "graph.db").is_file() or not (root / ".ibwd" / "manifest.json").is_file():
        raise ToolError("Repository is not indexed. Run ibwd_scan first; an absent index is not an empty graph.")
    return readonly(root.resolve())


@mcp.tool()
def ibwd_scan() -> dict:
    """Rescan the repository (incremental) and update the codebase graph.

    Run this if results from other ibwd tools look stale relative to recent
    edits. Returns counts of added/changed/removed/unchanged files.
    """
    return run_scan()


@mcp.tool()
@retrieval('find_files')
def ibwd_find_files(kind: str | None = None, name_pattern: str | None = None) -> list[dict]:
    """List files in the codebase graph, optionally filtered.

    Args:
        kind: restrict to one of "source", "test", "doc", "config", "vendor", "generated", "other".
        name_pattern: substring to match against the file path.

    Returns a compact list of {path, kind} — prefer this over repeated Glob
    calls for file discovery/categorization questions. Freshness is checked automatically.
    """
    conn = _connect_index()
    try:
        rows = find_files(conn, kind=kind, name_pattern=name_pattern)
    finally:
        conn.close()
    return [{"path": row["file_path"], "kind": row["kind"]} for row in rows]


@mcp.tool()
@retrieval('find_symbol')
def ibwd_find_symbol(name: str) -> list[dict]:
    """Find where a class/function/method is defined by name.

    Tries an exact (case-sensitive) match first; if none exist, falls back to
    a case-insensitive substring match. Prefer this over Grep for "where is X
    defined" questions — it returns every definition (including same-named
    symbols in different files) with an exact file:line, no false positives
    from comments/strings/usages.

    Args:
        name: the symbol name or exact file::symbol identity to look up.

    Returns a list of {name, symbol_id, kind, file, line} — kind is one of
    "Class"/"Function"/"Method". Freshness is checked automatically. Currently covers Python and
    JS/JSX/TS/TSX only.
    """
    conn = _connect_index()
    try:
        rows = find_symbol(conn, name)
    finally:
        conn.close()
    return [
        {"name": row["name"], "symbol_id": row["qualified_name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
        for row in rows
    ]


@mcp.tool()
@retrieval('list_symbols')
def ibwd_list_symbols(file: str) -> list[dict]:
    """List every class/function/method defined in a file, in source order.

    Args:
        file: repo-relative path to the file (as returned by ibwd_find_files).

    Returns a list of {name, symbol_id, kind, file, line}. Freshness is checked automatically.
    """
    conn = _connect_index()
    try:
        rows = list_symbols(conn, file)
    finally:
        conn.close()
    return [
        {"name": row["name"], "symbol_id": row["qualified_name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
        for row in rows
    ]


def _brief(row) -> dict:
    result = {"name": row["name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
    if "qualified_name" in row.keys():
        result["symbol_id"] = row["qualified_name"]
    return result


GRAPH_SCOPE = (
    "the indexed production scope: Python and JS/JSX/TS/TSX source files only (test, vendored, minified and generated files are not "
    "indexed); calls into external packages are not represented"
)


def _empty_result(kind: str, symbol: str, targets: list, relations: list[str], include_candidates: bool) -> list[dict]:
    """An empty answer states what it covers. It supports only 'no matching edges in this graph', never 'no possible uses'."""
    direction = "incoming" if kind == "callers" else "outgoing"
    rel = " or ".join(relations)
    if not targets:
        statement = f"No symbol or file matching {symbol!r} was found in the graph. This says nothing about whether it exists elsewhere."
    else:
        kinds = "resolved" if not include_candidates else "resolved and candidate"
        statement = (f"No {kinds} {direction} {rel} edges were found within {GRAPH_SCOPE}. Other uses may exist "
                     "(dynamic dispatch, framework registration, type-inferred receivers, unindexed files); this is not evidence that the symbol is unused or safe to delete.")
    return [{
        "empty_result": True, "statement": statement, "supported_claim": "no matching edges in this graph",
        "unsupported_claim": "no possible uses in the program", "scope": GRAPH_SCOPE,
        "relations_checked": relations, "candidate_hints_included": include_candidates,
        "matched_targets": [_brief(t) for t in targets][:5],
    }]


def _reach_tool(direction, symbol: str, depth: int, file: str | None, include_candidates: bool = False) -> list[dict]:
    conn = _connect_index()
    try:
        targets = resolve_targets(conn, symbol, file)
        results: list[dict] = []
        for target in targets:
            reached: list[Reach] = direction(conn, target["id"], depth, include_candidates)
            for r in reached:
                item = {
                    "name": r.name,
                    "kind": r.kind,
                    "file": r.file_path,
                    "line": r.start_line,
                    "distance": r.distance,
                    "confidence": r.confidence,
                    "relation": r.relation,
                    "relations": r.relations,
                    "resolution_status": r.resolution_status,
                }
                if len(targets) > 1:
                    item["of"] = f"{target['file_path']}:{target['start_line']}"
                results.append(item)
    finally:
        conn.close()
    if not results:
        kind = "callers" if direction is callers_of else "dependents"
        return _empty_result(kind, symbol, targets, ["CALLS", "IMPORTS", "INHERITS", "REFERENCES"], include_candidates)
    return results


@mcp.tool()
@retrieval('callers')
def ibwd_callers(symbol: str, depth: int = 1, file: str | None = None, include_candidates: bool = False) -> list[dict]:
    """What calls / imports / subclasses / references a symbol (or file), out to `depth` hops.

    Prefer this over manually Grep-tracing call sites. `symbol` is a
    class/function/method name (exact match first, then case-insensitive
    substring — same as ibwd_find_symbol) or a repo-relative file path, in which
    case results are the files importing it.

    Args:
        symbol: symbol name, exact file::symbol identity, or file path.
        depth: how many hops to follow (1 = direct callers only; capped at 5).
        file: optionally restrict a same-named symbol to one file.
        include_candidates: also follow *candidate* hints (unique-name / suffix
            matches). Default false: only resolved edges (import-map, same-module,
            inherited) are used, so a guess never appears in an ordinary answer.

    Returns a list of {name, kind, file, line, distance, confidence, relation,
    relations, resolution_status} sorted by distance then confidence. When a pair
    is linked by both CALLS and REFERENCES, `relations` lists both.
    `resolution_status` is "resolved" or (only with include_candidates) "candidate". `confidence` is the product of the
    resolution confidence of each edge on the path (0.95 import-resolved ...
    0.35 fuzzy): treat low values as leads to verify in source, not facts.
    `relation` is CALLS/IMPORTS/INHERITS/REFERENCES. Module-level calls show up
    as kind "File". Rendering a React component (`<Card />`) counts as CALLS; a
    function passed as a value (`useReducer(fn)`, `component={Screen}`) is
    REFERENCES. An empty result returns one record with `empty_result: true`, its scope and the
    statement "No resolved incoming ... edges were found within the indexed production scope. Other
    uses may exist." That supports only "no matching edges in this graph", never "no possible uses"
    or "safe to delete": dynamic dispatch, framework registration and type-inferred receivers have no
    static edge (see KNOWN_LIMITATIONS.md). If several definitions match `symbol`, each result carries an `of`
    ("file:line") naming which one it reaches. Freshness is checked automatically. Python and JS/TS only.
    """
    return _reach_tool(callers_of, symbol, depth, file, include_candidates)


@mcp.tool()
@retrieval('dependents')
def ibwd_dependents(symbol: str, depth: int = 1, file: str | None = None, include_candidates: bool = False) -> list[dict]:
    """What a symbol (or file) calls / imports / inherits from, out to `depth` hops.

    The mirror of ibwd_callers: use it for "what does X depend on?" instead of
    reading X and chasing each callee by hand. Arguments and result shape are
    identical to ibwd_callers (results are the things X reaches, not the things
    reaching X). Calls into external packages are not listed — only symbols and
    files inside this repo.
    """
    return _reach_tool(dependents_of, symbol, depth, file, include_candidates)


@mcp.tool()
@retrieval('trace_path')
def ibwd_trace_path(source: str, target: str, edge_types: list[str] | None = None, include_candidates: bool = False) -> dict:
    """Find how `source` reaches `target` through the call/import graph, if it does.

    Prefer this over calling ibwd_callers/ibwd_dependents at increasing depth
    for "how does A reach B" / "is A connected to B" questions. The path cost is
    a *heuristic cost* (the sum of 1/heuristic-score per hop): it prefers
    higher-scored hops and fewer of them. It is not a probability.

    Args:
        source: symbol name, exact file::symbol identity, or file path to start from.
        target: symbol name, exact file::symbol identity, or file path to reach.
        edge_types: subset of CALLS/IMPORTS/INHERITS/REFERENCES. Default CALLS
            only, i.e. a call chain; other relations make it a mixed
            dependency path and must be requested explicitly.
        include_candidates: also allow candidate hints (default false: resolved
            edges only, so a chain is never built from unique-name/suffix guesses).

    Returns {"path": [{name, kind, file, line, edge_type, confidence}, ...],
    "cost": float, "hops": int}; each hop after the first names the edge that
    reached it. "No path" is a valid, final answer: {"path": null, "reason": ...,
    "source_resolved": [...], "target_resolved": [...]} — the resolved lists show
    what each name matched, so there is no need to re-check that the symbols
    exist. Direction matters: a path from A to B does not imply one from B to A.
    More than ten matches at either endpoint returns ambiguous=true without
    searching; use a symbol_id from discovery to disambiguate.
    """
    edge_types = list(edge_types) if edge_types else list(DEFAULT_PATH_EDGE_TYPES)
    unknown = [t for t in edge_types if t not in TRAVERSAL_RELATIONS]
    if unknown:
        return {"path": None, "reason": f"unsupported edge_types {unknown}; use {list(TRAVERSAL_RELATIONS)}"}

    conn = _connect_index()
    try:
        sources = resolve_targets(conn, source)
        targets = resolve_targets(conn, target)
        if len(sources) > MAX_PATH_CANDIDATES or len(targets) > MAX_PATH_CANDIDATES:
            return {
                "path": None, "ambiguous": True,
                "reason": "Too many matching endpoints; no path search performed. Use an exact file::symbol identity from ibwd_find_symbol.",
                "source_matches": len(sources), "target_matches": len(targets),
                "source_examples": [_brief(r) for r in sources[:MAX_PATH_CANDIDATES]],
                "target_examples": [_brief(r) for r in targets[:MAX_PATH_CANDIDATES]],
                "examples_truncated": True,
            }
        if not sources:
            return {"path": None, "reason": f"source not found: {source}"}
        if not targets:
            return {"path": None, "reason": f"target not found: {target}"}

        graph = build_call_subgraph(conn, edge_types, include_candidates)
        best: tuple[float, list[int]] | None = None
        for s in sources:
            for t in targets:
                path = find_path(graph, s["id"], t["id"])
                if path is None:
                    continue
                cost = path_cost(graph, path)
                if best is None or cost < best[0]:
                    best = (cost, path)
    finally:
        conn.close()

    if best is None:
        # Echo what each name resolved to, so a "no path" answer is self-verifying:
        # the caller can see both endpoints exist without extra find_symbol calls.
        return {
            "path": None,
            "reason": "no path found: both endpoints exist in the graph but no edge chain of the requested relations connects them "
                      "(resolved edges only unless include_candidates; within the indexed production scope). This is not proof that no runtime path exists.",
            "scope": GRAPH_SCOPE, "relations_checked": edge_types, "candidate_hints_included": include_candidates,
            "source_resolved": [_brief(row) for row in sources],
            "target_resolved": [_brief(row) for row in targets],
        }

    cost, node_ids = best
    hops = []
    for index, node_id in enumerate(node_ids):
        attrs = graph.nodes[node_id]
        hop = {
            "name": attrs["name"],
            "kind": attrs["kind"],
            "file": attrs["file_path"],
            "line": attrs["line"],
            "edge_type": None,
            "edge_types": None,
            "confidence": None,
            "resolution_status": None,
        }
        if index > 0:
            edge = graph[node_ids[index - 1]][node_id]
            hop["edge_type"] = edge["relation"]
            hop["edge_types"] = sorted(edge["relations"])  # every selected relation linking this pair is preserved
            hop["confidence"] = edge["confidence"]
            hop["resolution_status"] = edge["status"]
        hops.append(hop)
    return {
        "path": hops,
        "cost": round(cost, 4),
        "cost_kind": "heuristic cost (sum of 1/score per hop), not a probability",
        "hops": len(node_ids) - 1,
        "uses_candidate_edges": any(h["resolution_status"] == "candidate" for h in hops),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository to index (default: current directory).")
    args = parser.parse_args(argv)
    root = args.repo.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"repository is not a directory: {root}")
    os.chdir(root)
    mcp.run()


if __name__ == "__main__":
    main()
