from __future__ import annotations

import sqlite3
from pathlib import Path

from ibwd.graph.database import Node, connect, upsert_edge, upsert_node, upsert_symbol_node
from ibwd.graph.queries import resolve_targets
from ibwd.retrieval.traversal import build_call_subgraph, callers_of, dependents_of, find_path, path_cost
from ibwd.scan import run_scan

CHAIN = (
    "def a():\n    return b()\n\n"
    "def b():\n    return c()\n\n"
    "def c():\n    return d()\n\n"
    "def d():\n    return 1\n\n"
    "def solo():\n    return 0\n"
)


def _scan_chain(tmp_path: Path) -> sqlite3.Connection:
    (tmp_path / "chain.py").write_text(CHAIN)
    run_scan(tmp_path)
    return connect(tmp_path / ".ibwd" / "graph.db")


def _id(conn: sqlite3.Connection, name: str) -> int:
    (row,) = resolve_targets(conn, name)
    return row["id"]


def test_callers_depth_1_vs_depth_2_vs_depth_3(tmp_path: Path):
    conn = _scan_chain(tmp_path)
    d = _id(conn, "d")

    depth1 = callers_of(conn, d, depth=1)
    assert [(r.name, r.distance) for r in depth1] == [("c", 1)]

    depth2 = callers_of(conn, d, depth=2)
    assert [(r.name, r.distance) for r in depth2] == [("c", 1), ("b", 2)]

    depth3 = callers_of(conn, d, depth=3)
    assert [(r.name, r.distance) for r in depth3] == [("c", 1), ("b", 2), ("a", 3)]
    conn.close()


def test_confidence_multiplies_along_the_path(tmp_path: Path):
    conn = _scan_chain(tmp_path)
    by_name = {r.name: r for r in callers_of(conn, _id(conn, "d"), depth=3)}

    assert by_name["c"].confidence == 0.9  # one same-module hop
    assert by_name["b"].confidence == 0.81  # 0.9 * 0.9
    assert by_name["a"].confidence == 0.729
    conn.close()


def test_dependents_mirror_callers(tmp_path: Path):
    conn = _scan_chain(tmp_path)
    a = _id(conn, "a")

    assert [r.name for r in dependents_of(conn, a, depth=1)] == ["b"]
    assert [(r.name, r.distance) for r in dependents_of(conn, a, depth=3)] == [("b", 1), ("c", 2), ("d", 3)]
    assert dependents_of(conn, _id(conn, "solo"), depth=3) == []
    conn.close()


def test_depth_is_clamped_and_cycles_terminate(tmp_path: Path):
    (tmp_path / "loop.py").write_text("def ping():\n    return pong()\n\ndef pong():\n    return ping()\n")
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")

    reached = callers_of(conn, _id(conn, "ping"), depth=999)
    assert [r.name for r in reached] == ["pong"]  # ping itself is never reported as its own caller
    assert callers_of(conn, _id(conn, "ping"), depth=0)[0].distance == 1  # depth < 1 behaves as 1
    conn.close()


def test_file_targets_report_importers(tmp_path: Path):
    (tmp_path / "lib.py").write_text("def f():\n    return 1\n")
    (tmp_path / "app.py").write_text("import lib\n\ndef g():\n    return lib.f()\n")
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")

    (target,) = resolve_targets(conn, "lib.py")
    reached = callers_of(conn, target["id"], depth=1)
    assert [(r.name, r.kind, r.relation) for r in reached] == [("app.py", "File", "IMPORTS")]
    conn.close()


# -- path finding on hand-built graphs ------------------------------------


def _graph_db(tmp_path: Path, names: list[str], edges: list[tuple[str, str, float]]):
    conn = connect(tmp_path / "graph.db")
    ids = {}
    for name in names:
        ids[name] = upsert_symbol_node(
            conn,
            Node(node_type="Function", name=name, qualified_name=f"m.py::{name}", file_path="m.py", start_line=1, end_line=2),
        )
    for source, target, confidence in edges:
        upsert_edge(conn, ids[source], ids[target], "CALLS", confidence)
    conn.commit()
    return conn, ids


def test_find_path_returns_the_known_three_hop_chain(tmp_path: Path):
    conn, ids = _graph_db(
        tmp_path,
        ["A", "B", "C", "D"],
        [("A", "B", 0.9), ("B", "C", 0.9), ("C", "D", 0.9)],
    )
    graph = build_call_subgraph(conn)

    path = find_path(graph, ids["A"], ids["D"])
    assert path == [ids["A"], ids["B"], ids["C"], ids["D"]]
    assert round(path_cost(graph, path), 4) == round(3 / 0.9, 4)


def test_find_path_returns_none_when_disconnected(tmp_path: Path):
    conn, ids = _graph_db(tmp_path, ["A", "B", "X"], [("A", "B", 0.9)])
    graph = build_call_subgraph(conn)

    assert find_path(graph, ids["A"], ids["X"]) is None
    assert find_path(graph, ids["B"], ids["A"]) is None  # edges are directed
    assert find_path(graph, ids["A"], 999_999) is None  # unknown node is a clean "no path"


def test_find_path_prefers_the_higher_confidence_route(tmp_path: Path):
    # Equal hop count: S->P->T (0.95, 0.95) vs S->Q->T (0.35, 0.95)
    conn, ids = _graph_db(
        tmp_path,
        ["S", "P", "Q", "T"],
        [("S", "P", 0.95), ("P", "T", 0.95), ("S", "Q", 0.35), ("Q", "T", 0.95)],
    )
    path = find_path(build_call_subgraph(conn), ids["S"], ids["T"])
    assert path == [ids["S"], ids["P"], ids["T"]]


def test_find_path_prefers_a_confident_detour_over_a_fuzzy_shortcut(tmp_path: Path):
    # direct S->T costs 1/0.35 = 2.86; S->M->T costs 2/0.95 = 2.11
    conn, ids = _graph_db(
        tmp_path,
        ["S", "M", "T"],
        [("S", "T", 0.35), ("S", "M", 0.95), ("M", "T", 0.95)],
    )
    path = find_path(build_call_subgraph(conn), ids["S"], ids["T"])
    assert path == [ids["S"], ids["M"], ids["T"]]


def test_find_path_accepts_a_custom_heuristic(tmp_path: Path):
    conn, ids = _graph_db(tmp_path, ["A", "B", "C"], [("A", "B", 0.9), ("B", "C", 0.9)])
    graph = build_call_subgraph(conn)

    assert find_path(graph, ids["A"], ids["C"], heuristic=lambda u, v: 0.0) == [ids["A"], ids["B"], ids["C"]]


def test_edge_type_filter_excludes_other_relations(tmp_path: Path):
    conn, ids = _graph_db(tmp_path, ["A", "B"], [])
    upsert_edge(conn, ids["A"], ids["B"], "INHERITS", 0.9)
    conn.commit()

    assert find_path(build_call_subgraph(conn), ids["A"], ids["B"]) is None  # default = CALLS + IMPORTS
    assert find_path(build_call_subgraph(conn, ("INHERITS",)), ids["A"], ids["B"]) == [ids["A"], ids["B"]]


def test_resolve_targets_prefers_exact_file_path(tmp_path: Path):
    (tmp_path / "lib.py").write_text("def lib():\n    return 1\n")
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")

    assert [(r["kind"], r["file_path"]) for r in resolve_targets(conn, "lib.py")] == [("File", "lib.py")]
    assert [(r["kind"], r["name"]) for r in resolve_targets(conn, "lib")] == [("Function", "lib")]
    assert resolve_targets(conn, "lib", file="other.py") == []
    conn.close()


def test_file_nodes_are_used_for_module_level_paths(tmp_path: Path):
    conn = connect(tmp_path / "g.db")
    a = upsert_node(conn, Node(node_type="File", name="a.py", file_path="a.py", kind="source"))
    b = upsert_node(conn, Node(node_type="File", name="b.py", file_path="b.py", kind="source"))
    upsert_edge(conn, a, b, "IMPORTS", 1.0)
    conn.commit()

    # a mixed (IMPORTS) dependency path must be requested explicitly; the default call-chain graph is CALLS-only
    assert find_path(build_call_subgraph(conn), a, b) is None
    assert find_path(build_call_subgraph(conn, ("IMPORTS",)), a, b) == [a, b]
