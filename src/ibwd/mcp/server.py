"""IBWD MCP server — exposes the codebase graph as typed tools for Claude Code."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

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

# Ambiguous names (substring fallback) can match many nodes; bound the path search.
MAX_PATH_CANDIDATES = 10

mcp = MCPServer("ibwd")


@mcp.tool()
def ibwd_scan() -> dict:
    """Rescan the repository (incremental) and update the codebase graph.

    Run this if results from other ibwd tools look stale relative to recent
    edits. Returns counts of added/changed/removed/unchanged files.
    """
    return run_scan()


@mcp.tool()
def ibwd_find_files(kind: str | None = None, name_pattern: str | None = None) -> list[dict]:
    """List files in the codebase graph, optionally filtered.

    Args:
        kind: restrict to one of "source", "test", "doc", "config", "vendor", "generated", "other".
        name_pattern: substring to match against the file path.

    Returns a compact list of {path, kind} — prefer this over repeated Glob
    calls for file discovery/categorization questions. Results reflect the
    graph as of the last ibwd_scan; call ibwd_scan first if unsure.
    """
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        rows = find_files(conn, kind=kind, name_pattern=name_pattern)
    finally:
        conn.close()
    return [{"path": row["file_path"], "kind": row["kind"]} for row in rows]


@mcp.tool()
def ibwd_find_symbol(name: str) -> list[dict]:
    """Find where a class/function/method is defined by name.

    Tries an exact (case-sensitive) match first; if none exist, falls back to
    a case-insensitive substring match. Prefer this over Grep for "where is X
    defined" questions — it returns every definition (including same-named
    symbols in different files) with an exact file:line, no false positives
    from comments/strings/usages.

    Args:
        name: the symbol name to look up.

    Returns a list of {name, kind, file, line} — kind is one of
    "Class"/"Function"/"Method". Results reflect the graph as of the last
    ibwd_scan; call ibwd_scan first if unsure. Currently covers Python and
    JS/JSX/TS/TSX only.
    """
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        rows = find_symbol(conn, name)
    finally:
        conn.close()
    return [
        {"name": row["name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
        for row in rows
    ]


@mcp.tool()
def ibwd_list_symbols(file: str) -> list[dict]:
    """List every class/function/method defined in a file, in source order.

    Args:
        file: repo-relative path to the file (as returned by ibwd_find_files).

    Returns a list of {name, kind, file, line}. Results reflect the graph as
    of the last ibwd_scan; call ibwd_scan first if unsure.
    """
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        rows = list_symbols(conn, file)
    finally:
        conn.close()
    return [
        {"name": row["name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
        for row in rows
    ]


def _brief(row) -> dict:
    return {"name": row["name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}


def _reach_tool(direction, symbol: str, depth: int, file: str | None) -> list[dict]:
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        targets = resolve_targets(conn, symbol, file)
        results: list[dict] = []
        for target in targets:
            reached: list[Reach] = direction(conn, target["id"], depth)
            for r in reached:
                item = {
                    "name": r.name,
                    "kind": r.kind,
                    "file": r.file_path,
                    "line": r.start_line,
                    "distance": r.distance,
                    "confidence": r.confidence,
                    "relation": r.relation,
                }
                if len(targets) > 1:
                    item["of"] = f"{target['file_path']}:{target['start_line']}"
                results.append(item)
    finally:
        conn.close()
    return results


@mcp.tool()
def ibwd_callers(symbol: str, depth: int = 1, file: str | None = None) -> list[dict]:
    """What calls / imports / subclasses / references a symbol (or file), out to `depth` hops.

    Prefer this over manually Grep-tracing call sites. `symbol` is a
    class/function/method name (exact match first, then case-insensitive
    substring — same as ibwd_find_symbol) or a repo-relative file path, in which
    case results are the files importing it.

    Args:
        symbol: symbol name or file path.
        depth: how many hops to follow (1 = direct callers only; capped at 5).
        file: optionally restrict a same-named symbol to one file.

    Returns a list of {name, kind, file, line, distance, confidence, relation}
    sorted by distance then confidence. `confidence` is the product of the
    resolution confidence of each edge on the path (0.95 import-resolved ...
    0.35 fuzzy): treat low values as leads to verify in source, not facts.
    `relation` is CALLS/IMPORTS/INHERITS/REFERENCES. Module-level calls show up
    as kind "File". Rendering a React component (`<Card />`) counts as CALLS; a
    function passed as a value (`useReducer(fn)`, `component={Screen}`) is
    REFERENCES. Empty results do NOT prove a function is safe to delete: dynamic
    dispatch and framework entry points have no static caller (see
    KNOWN_LIMITATIONS.md). If several definitions match `symbol`, each result carries an `of`
    ("file:line") naming which one it reaches. Results reflect the graph as of
    the last ibwd_scan; call ibwd_scan first if unsure. Python and JS/TS only.
    """
    return _reach_tool(callers_of, symbol, depth, file)


@mcp.tool()
def ibwd_dependents(symbol: str, depth: int = 1, file: str | None = None) -> list[dict]:
    """What a symbol (or file) calls / imports / inherits from, out to `depth` hops.

    The mirror of ibwd_callers: use it for "what does X depend on?" instead of
    reading X and chasing each callee by hand. Arguments and result shape are
    identical to ibwd_callers (results are the things X reaches, not the things
    reaching X). Calls into external packages are not listed — only symbols and
    files inside this repo.
    """
    return _reach_tool(dependents_of, symbol, depth, file)


@mcp.tool()
def ibwd_trace_path(source: str, target: str, edge_types: list[str] | None = None) -> dict:
    """Find how `source` reaches `target` through the call/import graph, if it does.

    Prefer this over calling ibwd_callers/ibwd_dependents at increasing depth
    for "how does A reach B" / "is A connected to B" questions. The search
    prefers high-confidence hops (edge cost = 1/confidence), so a route through
    verified edges beats a shorter one through fuzzy guesses.

    Args:
        source: symbol name or file path to start from.
        target: symbol name or file path to reach.
        edge_types: subset of CALLS/IMPORTS/INHERITS/REFERENCES (default CALLS + IMPORTS).

    Returns {"path": [{name, kind, file, line, edge_type, confidence}, ...],
    "cost": float, "hops": int}; each hop after the first names the edge that
    reached it. "No path" is a valid, final answer: {"path": null, "reason": ...,
    "source_resolved": [...], "target_resolved": [...]} — the resolved lists show
    what each name matched, so there is no need to re-check that the symbols
    exist. Direction matters: a path from A to B does not imply one from B to A.
    """
    edge_types = list(edge_types) if edge_types else list(DEFAULT_PATH_EDGE_TYPES)
    unknown = [t for t in edge_types if t not in TRAVERSAL_RELATIONS]
    if unknown:
        return {"path": None, "reason": f"unsupported edge_types {unknown}; use {list(TRAVERSAL_RELATIONS)}"}

    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        sources = resolve_targets(conn, source)[:MAX_PATH_CANDIDATES]
        targets = resolve_targets(conn, target)[:MAX_PATH_CANDIDATES]
        if not sources:
            return {"path": None, "reason": f"source not found: {source}"}
        if not targets:
            return {"path": None, "reason": f"target not found: {target}"}

        graph = build_call_subgraph(conn, edge_types)
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
            "reason": "no path found: both endpoints exist in the graph but no edge chain connects them",
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
            "confidence": None,
        }
        if index > 0:
            edge = graph[node_ids[index - 1]][node_id]
            hop["edge_type"] = edge["relation"]
            hop["confidence"] = edge["confidence"]
        hops.append(hop)
    return {"path": hops, "cost": round(cost, 4), "hops": len(node_ids) - 1}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
