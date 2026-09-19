"""Graph traversal over CALLS / IMPORTS / INHERITS edges.

callers_of / dependents_of use recursive CTEs (unweighted BFS-style expansion
with a depth cap); find_path builds the relevant subgraph in memory with
networkx, because a *weighted* shortest path has no native SQL equivalent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import networkx as nx

TRAVERSAL_RELATIONS = ("CALLS", "IMPORTS", "INHERITS")
DEFAULT_PATH_EDGE_TYPES = ("CALLS", "IMPORTS")
# Path enumeration inside the CTE grows with depth on dense graphs; 5 hops is
# already far beyond what a caller/dependent listing is useful for.
MAX_DEPTH = 5


@dataclass
class Reach:
    node_id: int
    distance: int
    confidence: float  # product of edge confidences along the best path found
    relation: str  # relation of the final hop onto this node
    name: str
    kind: str
    file_path: str | None
    start_line: int | None


def _clamp_depth(depth: int) -> int:
    return max(1, min(int(depth), MAX_DEPTH))


def _reach(conn: sqlite3.Connection, node_id: int, depth: int, incoming: bool) -> list[Reach]:
    """Recursive-CTE expansion from node_id along incoming (callers) or outgoing (dependents) edges."""
    start_col, next_col = ("target_id", "source_id") if incoming else ("source_id", "target_id")
    placeholders = ",".join("?" * len(TRAVERSAL_RELATIONS))

    rows = conn.execute(
        f"""
        WITH RECURSIVE reach(id, depth, conf, rel) AS (
            SELECT e.{next_col}, 1, e.confidence, e.relation
            FROM edges e
            WHERE e.{start_col} = ? AND e.relation IN ({placeholders})
          UNION
            SELECT e.{next_col}, r.depth + 1, r.conf * e.confidence, e.relation
            FROM edges e JOIN reach r ON e.{start_col} = r.id
            WHERE e.relation IN ({placeholders}) AND r.depth < ?
        )
        SELECT id, depth, MAX(conf) AS conf, rel FROM reach GROUP BY id, depth
        """,
        (node_id, *TRAVERSAL_RELATIONS, *TRAVERSAL_RELATIONS, _clamp_depth(depth)),
    ).fetchall()

    # A node reachable at several depths is reported once, at its shortest distance.
    best: dict[int, sqlite3.Row] = {}
    for row in rows:
        if row["id"] == node_id:
            continue
        current = best.get(row["id"])
        if current is None or row["depth"] < current["depth"]:
            best[row["id"]] = row

    if not best:
        return []

    id_placeholders = ",".join("?" * len(best))
    nodes = {
        row["id"]: row
        for row in conn.execute(
            f"SELECT id, name, node_type, file_path, start_line FROM nodes WHERE id IN ({id_placeholders})",
            list(best),
        )
    }
    results = [
        Reach(
            node_id=node["id"],
            distance=best[node["id"]]["depth"],
            confidence=round(best[node["id"]]["conf"], 4),
            relation=best[node["id"]]["rel"],
            name=node["name"],
            kind=node["node_type"],
            file_path=node["file_path"],
            start_line=node["start_line"],
        )
        for node in nodes.values()
    ]
    results.sort(key=lambda r: (r.distance, -r.confidence, r.file_path or "", r.start_line or 0, r.name))
    return results


def callers_of(conn: sqlite3.Connection, node_id: int, depth: int = 1) -> list[Reach]:
    """Everything that calls / imports / subclasses node_id, out to `depth` hops."""
    return _reach(conn, node_id, depth, incoming=True)


def dependents_of(conn: sqlite3.Connection, node_id: int, depth: int = 1) -> list[Reach]:
    """Everything node_id calls / imports / inherits from, out to `depth` hops."""
    return _reach(conn, node_id, depth, incoming=False)


def build_call_subgraph(
    conn: sqlite3.Connection,
    edge_types: tuple[str, ...] | list[str] = DEFAULT_PATH_EDGE_TYPES,
) -> nx.DiGraph:
    """Load matching edges into an in-memory DiGraph; edge weight = 1 / confidence.

    A path through two conf=0.95 edges therefore costs less than one through a
    single conf=0.35 fuzzy edge, so shortest-path search prefers verified-looking
    routes. Parallel edges (same endpoints, different relation) keep the cheapest.
    """
    graph = nx.DiGraph()
    placeholders = ",".join("?" * len(edge_types))
    rows = conn.execute(
        f"""
        SELECT e.source_id, e.target_id, e.relation, e.confidence,
               s.name AS s_name, s.node_type AS s_kind, s.file_path AS s_file, s.start_line AS s_line,
               t.name AS t_name, t.node_type AS t_kind, t.file_path AS t_file, t.start_line AS t_line
        FROM edges e
        JOIN nodes s ON s.id = e.source_id
        JOIN nodes t ON t.id = e.target_id
        WHERE e.relation IN ({placeholders})
        """,
        list(edge_types),
    )
    for row in rows:
        graph.add_node(row["source_id"], name=row["s_name"], kind=row["s_kind"], file_path=row["s_file"], line=row["s_line"])
        graph.add_node(row["target_id"], name=row["t_name"], kind=row["t_kind"], file_path=row["t_file"], line=row["t_line"])
        weight = 1.0 / max(row["confidence"], 0.01)
        existing = graph.get_edge_data(row["source_id"], row["target_id"])
        if existing is not None and existing["weight"] <= weight:
            continue
        graph.add_edge(
            row["source_id"],
            row["target_id"],
            weight=weight,
            relation=row["relation"],
            confidence=row["confidence"],
        )
    return graph


def find_path(graph: nx.DiGraph, source_id: int, target_id: int, heuristic=None) -> list[int] | None:
    """Lowest-cost path of node ids from source to target, or None if there isn't one.

    `heuristic(node, target)` defaults to 0, which makes this plain Dijkstra
    behind an A*-shaped interface — a real estimate can be swapped in later
    without touching call sites. "No path" is a valid answer, not an error.
    """
    if source_id not in graph or target_id not in graph:
        return None
    try:
        return nx.astar_path(
            graph,
            source_id,
            target_id,
            heuristic=heuristic or (lambda u, v: 0),
            weight="weight",
        )
    except nx.NetworkXNoPath:
        return None


def path_cost(graph: nx.DiGraph, path: list[int]) -> float:
    return sum(graph[u][v]["weight"] for u, v in zip(path, path[1:]))
