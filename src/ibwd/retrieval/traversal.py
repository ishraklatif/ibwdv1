"""Graph traversal over CALLS / IMPORTS / INHERITS / REFERENCES edges.

callers_of / dependents_of use recursive CTEs (unweighted BFS-style expansion
with a depth cap); find_path builds the relevant subgraph in memory with
networkx, because a *weighted* shortest path has no native SQL equivalent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import networkx as nx

TRAVERSAL_RELATIONS = ("CALLS", "IMPORTS", "INHERITS", "REFERENCES")
# A "call chain" is CALLS-only by default; mixed dependency paths (CALLS + IMPORTS, ...) must be requested explicitly.
DEFAULT_PATH_EDGE_TYPES = ("CALLS",)
# Path enumeration inside the CTE grows with depth on dense graphs; 5 hops is
# already far beyond what a caller/dependent listing is useful for.
MAX_DEPTH = 5


@dataclass
class Reach:
    node_id: int
    distance: int
    confidence: float  # product of edge heuristic scores along the best path found (a heuristic, not a probability)
    relation: str  # primary relation of the final hop onto this node
    relations: list[str]  # every relation linking the pair at this distance (e.g. CALLS and REFERENCES both preserved)
    resolution_status: str  # "candidate" if the best path uses any candidate edge, else "resolved"
    name: str
    kind: str
    file_path: str | None
    start_line: int | None


def _clamp_depth(depth: int) -> int:
    return max(1, min(int(depth), MAX_DEPTH))


def _reach(conn: sqlite3.Connection, node_id: int, depth: int, incoming: bool, include_candidates: bool = False) -> list[Reach]:
    """Recursive-CTE expansion from node_id along incoming (callers) or outgoing (dependents) edges.

    Only `resolved` edges are followed unless include_candidates is set, in which case candidate hints are followed too and
    every result says whether its best path used one.
    """
    start_col, next_col = ("target_id", "source_id") if incoming else ("source_id", "target_id")
    placeholders = ",".join("?" * len(TRAVERSAL_RELATIONS))
    status_filter = "" if include_candidates else "AND e.resolution_status = 'resolved'"

    rows = conn.execute(
        f"""
        WITH RECURSIVE reach(id, depth, conf, rel, cand) AS (
            SELECT e.{next_col}, 1, e.confidence, e.relation, (e.resolution_status = 'candidate')
            FROM edges e
            WHERE e.{start_col} = ? AND e.relation IN ({placeholders}) {status_filter}
          UNION
            SELECT e.{next_col}, r.depth + 1, r.conf * e.confidence, e.relation, (r.cand OR e.resolution_status = 'candidate')
            FROM edges e JOIN reach r ON e.{start_col} = r.id
            WHERE e.relation IN ({placeholders}) AND r.depth < ? {status_filter}
        )
        SELECT id, depth, conf, rel, cand FROM reach
        """,
        (node_id, *TRAVERSAL_RELATIONS, *TRAVERSAL_RELATIONS, _clamp_depth(depth)),
    ).fetchall()

    # A node reachable at several depths is reported once, at its shortest distance; among that distance's rows the
    # highest-confidence one sets the score/status, and *every* relation seen there is kept (CALLS and REFERENCES both).
    by_node: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        if row["id"] != node_id:
            by_node.setdefault(row["id"], []).append(row)
    best: dict[int, sqlite3.Row] = {}
    relations: dict[int, list[str]] = {}
    for nid, node_rows in by_node.items():
        shortest = min(r["depth"] for r in node_rows)
        at_depth = [r for r in node_rows if r["depth"] == shortest]
        best[nid] = max(at_depth, key=lambda r: r["conf"])
        relations[nid] = sorted({r["rel"] for r in at_depth})

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
            relations=relations[node["id"]],
            resolution_status="candidate" if best[node["id"]]["cand"] else "resolved",
            name=node["name"],
            kind=node["node_type"],
            file_path=node["file_path"],
            start_line=node["start_line"],
        )
        for node in nodes.values()
    ]
    results.sort(key=lambda r: (r.distance, -r.confidence, r.file_path or "", r.start_line or 0, r.name))
    return results


def callers_of(conn: sqlite3.Connection, node_id: int, depth: int = 1, include_candidates: bool = False) -> list[Reach]:
    """Everything that calls / imports / subclasses / references node_id, out to `depth` hops (resolved edges by default)."""
    return _reach(conn, node_id, depth, incoming=True, include_candidates=include_candidates)


def dependents_of(conn: sqlite3.Connection, node_id: int, depth: int = 1, include_candidates: bool = False) -> list[Reach]:
    """Everything node_id calls / imports / inherits from / references, out to `depth` hops (resolved edges by default)."""
    return _reach(conn, node_id, depth, incoming=False, include_candidates=include_candidates)


def build_call_subgraph(
    conn: sqlite3.Connection,
    edge_types: tuple[str, ...] | list[str] = DEFAULT_PATH_EDGE_TYPES,
    include_candidates: bool = False,
) -> nx.DiGraph:
    """Load matching edges into an in-memory DiGraph; edge weight = 1 / confidence (a *heuristic cost*).

    A path through two conf=0.95 edges costs less than one through a single conf=0.35 edge, so shortest-path search prefers
    higher-scored routes; the cost is not a probability and not a maximum-product score. Only `resolved` edges are loaded
    unless include_candidates is set, so a candidate hint never silently contributes to an ordinary chain.

    Every relation linking a pair is kept in the edge's `relations` mapping; the cheapest one supplies weight, relation,
    confidence and status.
    """
    graph = nx.DiGraph()
    placeholders = ",".join("?" * len(edge_types))
    status_filter = "" if include_candidates else "AND e.resolution_status = 'resolved'"
    rows = conn.execute(
        f"""
        SELECT e.source_id, e.target_id, e.relation, e.confidence, e.resolution_status,
               s.name AS s_name, s.node_type AS s_kind, s.file_path AS s_file, s.start_line AS s_line,
               t.name AS t_name, t.node_type AS t_kind, t.file_path AS t_file, t.start_line AS t_line
        FROM edges e
        JOIN nodes s ON s.id = e.source_id
        JOIN nodes t ON t.id = e.target_id
        WHERE e.relation IN ({placeholders}) {status_filter}
        """,
        list(edge_types),
    )
    for row in rows:
        graph.add_node(row["source_id"], name=row["s_name"], kind=row["s_kind"], file_path=row["s_file"], line=row["s_line"])
        graph.add_node(row["target_id"], name=row["t_name"], kind=row["t_kind"], file_path=row["t_file"], line=row["t_line"])
        weight = 1.0 / max(row["confidence"], 0.01)
        info = {"confidence": row["confidence"], "status": row["resolution_status"], "weight": weight}
        existing = graph.get_edge_data(row["source_id"], row["target_id"])
        if existing is None:
            graph.add_edge(row["source_id"], row["target_id"], weight=weight, relation=row["relation"],
                           confidence=row["confidence"], status=row["resolution_status"], relations={row["relation"]: info})
            continue
        existing["relations"][row["relation"]] = info
        if weight < existing["weight"]:
            existing.update(weight=weight, relation=row["relation"], confidence=row["confidence"], status=row["resolution_status"])
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
