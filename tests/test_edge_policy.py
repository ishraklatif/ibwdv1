"""Frozen default edge policy: resolved vs candidate edges (benchmarks/SPRINT3_gate_definition.md section 6)."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from ibwd.export import export_graph
from ibwd.graph.database import connect
from ibwd.graph.queries import resolve_targets
from ibwd.mcp.server import mcp
from ibwd.retrieval.traversal import callers_of, dependents_of
from ibwd.scan import run_scan

POLICY_REPO = {
    "lib.py": "def helper():\n    return 1\n\nclass Base:\n    def step(self):\n        return 1\n",
    "other.py": "class Other:\n    def step(self):\n        return 2\n",     # makes `step` ambiguous by name alone
    "app.py": (
        "from lib import helper, Base\n\n"
        "class Child(Base):\n"
        "    def go(self):\n"
        "        return self.step()\n"                 # inherited          -> resolved
        "\n"
        "def by_import():\n    return helper()\n"     # import-map         -> resolved
        "\n"
        "def by_module():\n    return by_import()\n"  # same-module        -> resolved
        "\n"
        "def by_name(x):\n    return x.only_here()\n"  # unique-name       -> candidate
    ),
    "thing.py": "class Thing:\n    def only_here(self):\n        return 1\n",
    "repos.py": "class UserRepo:\n    def save(self):\n        return 1\n\nclass OrderRepo:\n    def save(self):\n        return 2\n",
    "svc.py": "def by_suffix(user_repo):\n    return user_repo.save()\n",     # suffix -> candidate
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _db(root: Path) -> sqlite3.Connection:
    return connect(root / ".ibwd" / "graph.db")


def _tool(name: str, args: dict):
    result = asyncio.run(mcp.call_tool(name, args))
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    return structured if structured else json.loads(result.content[0].text)


def _in(root: Path, fn):
    old = os.getcwd()
    os.chdir(root)
    try:
        return fn()
    finally:
        os.chdir(old)


def test_each_tier_gets_its_frozen_status(tmp_path: Path):
    _write(tmp_path, POLICY_REPO)
    run_scan(tmp_path)
    rows = {
        (r["s"], r["t"]): (r["resolution_status"], r["resolution_tier"], r["confidence"])
        for r in _db(tmp_path).execute(
            "SELECT s.qualified_name s, t.qualified_name t, e.* FROM edges e "
            "JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id WHERE e.relation = 'CALLS'"
        )
    }
    assert rows[("app.py::Child.go", "lib.py::Base.step")] == ("resolved", "inherited", 0.85)
    assert rows[("app.py::by_import", "lib.py::helper")] == ("resolved", "import_map", 0.95)
    assert rows[("app.py::by_module", "app.py::by_import")] == ("resolved", "same_module", 0.90)
    assert rows[("app.py::by_name", "thing.py::Thing.only_here")] == ("candidate", "unique_name", 0.75)  # heuristic score kept
    assert rows[("svc.py::by_suffix", "repos.py::UserRepo.save")] == ("candidate", "suffix", 0.55)


def test_default_callers_and_dependents_use_resolved_edges_only(tmp_path: Path):
    _write(tmp_path, POLICY_REPO)
    run_scan(tmp_path)
    conn = _db(tmp_path)
    (only_here,) = resolve_targets(conn, "only_here")

    assert callers_of(conn, only_here["id"]) == []                                   # the only caller is a candidate hint
    shown = callers_of(conn, only_here["id"], include_candidates=True)
    assert [(r.name, r.resolution_status) for r in shown] == [("by_name", "candidate")]

    (by_name,) = resolve_targets(conn, "by_name")
    assert dependents_of(conn, by_name["id"]) == []
    assert [(r.name, r.resolution_status) for r in dependents_of(conn, by_name["id"], include_candidates=True)] == [("only_here", "candidate")]


def test_a_candidate_never_contributes_silently_to_a_multi_hop_answer(tmp_path: Path):
    _write(
        tmp_path,
        {
            "chain.py": "def a():\n    return b()\n\ndef b(x=None):\n    return x.only_here()\n",   # a -> b resolved; b -> only_here candidate
            "thing.py": "class Thing:\n    def only_here(self):\n        return 1\n",
        },
    )
    run_scan(tmp_path)
    conn = _db(tmp_path)
    (only_here,) = resolve_targets(conn, "only_here")

    assert callers_of(conn, only_here["id"], depth=3) == []                          # default: no b, and therefore no a either
    shown = {r.name: r.resolution_status for r in callers_of(conn, only_here["id"], depth=3, include_candidates=True)}
    assert shown == {"b": "candidate", "a": "candidate"}                             # a's path passes through a candidate hop


def test_tools_default_to_resolved_and_label_candidates_when_asked(tmp_path: Path):
    _write(tmp_path, POLICY_REPO)
    run_scan(tmp_path)

    def calls():
        default = _tool("ibwd_callers", {"symbol": "only_here"})
        with_cand = _tool("ibwd_callers", {"symbol": "only_here", "include_candidates": True})
        resolved = _tool("ibwd_callers", {"symbol": "helper"})
        return default, with_cand, resolved

    default, with_cand, resolved = _in(tmp_path, calls)
    assert default == []
    assert [(r["name"], r["resolution_status"]) for r in with_cand] == [("by_name", "candidate")]
    assert [(r["name"], r["resolution_status"]) for r in resolved] == [("by_import", "resolved")]


def test_trace_path_is_calls_only_and_resolved_only_by_default(tmp_path: Path):
    _write(
        tmp_path,
        {
            "m.py": (
                "def a():\n    return b()\n\n"
                "def b():\n    return c()\n\n"
                "def c(x=None):\n    return x.only_here()\n"     # c -> only_here is a candidate hint
            ),
            "thing.py": "class Thing:\n    def only_here(self):\n        return 1\n",
        },
    )
    run_scan(tmp_path)

    default, allowed = _in(
        tmp_path,
        lambda: (
            _tool("ibwd_trace_path", {"source": "a", "target": "only_here"}),
            _tool("ibwd_trace_path", {"source": "a", "target": "only_here", "include_candidates": True}),
        ),
    )
    assert default["path"] is None and default["reason"].startswith("no path found")
    assert [h["name"] for h in allowed["path"]] == ["a", "b", "c", "only_here"]
    assert allowed["uses_candidate_edges"] is True
    assert allowed["path"][-1]["resolution_status"] == "candidate" and allowed["path"][1]["resolution_status"] == "resolved"
    assert allowed["cost_kind"].startswith("heuristic cost")                         # described as a heuristic, not a probability


def test_default_path_is_a_call_chain_not_a_mixed_dependency_path(tmp_path: Path):
    _write(tmp_path, {"lib.py": "def f():\n    return 1\n", "app.py": "import lib\n"})   # app.py IMPORTS lib.py: no call anywhere
    run_scan(tmp_path)

    default, mixed = _in(
        tmp_path,
        lambda: (
            _tool("ibwd_trace_path", {"source": "app.py", "target": "lib.py"}),
            _tool("ibwd_trace_path", {"source": "app.py", "target": "lib.py", "edge_types": ["CALLS", "IMPORTS"]}),
        ),
    )
    assert default["path"] is None
    assert [h["name"] for h in mixed["path"]] == ["app.py", "lib.py"] and mixed["path"][1]["edge_type"] == "IMPORTS"


def test_calls_and_references_are_both_preserved_for_a_pair(tmp_path: Path):
    _write(tmp_path, {"m.py": "def g():\n    return 1\n\ndef f():\n    h = g\n    return g() + h()\n"})
    run_scan(tmp_path)

    callers, path = _in(
        tmp_path,
        lambda: (
            _tool("ibwd_callers", {"symbol": "g"}),
            _tool("ibwd_trace_path", {"source": "f", "target": "g", "edge_types": ["CALLS", "REFERENCES"]}),
        ),
    )
    assert callers[0]["name"] == "f" and callers[0]["relations"] == ["CALLS", "REFERENCES"]
    assert path["path"][1]["edge_types"] == ["CALLS", "REFERENCES"]


def test_databases_created_before_resolution_status_are_migrated_in_place(tmp_path: Path):
    old = tmp_path / ".ibwd"
    old.mkdir()
    db = sqlite3.connect(old / "graph.db")
    db.executescript(
        "CREATE TABLE nodes (id INTEGER PRIMARY KEY, node_type TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT, file_path TEXT,"
        " kind TEXT, start_line INTEGER, end_line INTEGER, content_hash TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, target_id INTEGER NOT NULL, relation TEXT NOT NULL,"
        " confidence REAL NOT NULL DEFAULT 1.0, source_type TEXT NOT NULL DEFAULT 'static_analysis', created_at TEXT,"
        " UNIQUE (source_id, target_id, relation));"
        "INSERT INTO nodes (id, node_type, name) VALUES (1, 'Function', 'a'), (2, 'Function', 'b');"
        "INSERT INTO edges (source_id, target_id, relation, confidence) VALUES (1, 2, 'CALLS', 0.9);"
    )
    db.commit()
    db.close()

    conn = connect(old / "graph.db")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(edges)")}
    assert {"resolution_status", "resolution_tier"} <= columns
    assert conn.execute("SELECT resolution_status FROM edges").fetchone()[0] == "resolved"   # old edges default to resolved


def test_export_carries_status_and_the_oracle_denominator_is_not_narrowed(tmp_path: Path):
    _write(tmp_path, POLICY_REPO)
    run_scan(tmp_path)
    data = export_graph(_db(tmp_path), "demo", "abc")
    statuses = {(e["source"], e["target"]): e["resolution_status"] for e in data["edges"] if e["relation"] == "CALLS"}
    assert statuses[("app.py::by_name", "thing.py::Thing.only_here")] == "candidate"
    assert statuses[("app.py::by_import", "lib.py::helper")] == "resolved"

    (tmp_path / "ibwd.json").write_text(json.dumps(data))
    oracle = {
        "symbols": [{"id": s["id"], "file": s["file"], "line": s["line"], "name": s["qualname"] or s["name"], "kind": s["kind"]} for s in data["symbols"]],
        "edges": [{"source": e["source"], "target": e["target"], "relation": e["relation"]} for e in data["edges"] if e["relation"] == "CALLS"],
    }
    (tmp_path / "oracle.json").write_text(json.dumps(oracle))
    script = Path(__file__).resolve().parent.parent / "benchmarks" / "tools" / "compare_pairs.py"

    def run(status: str) -> dict:
        out = subprocess.run([sys.executable, str(script), str(tmp_path / "oracle.json"), str(tmp_path / "ibwd.json"),
                              "--relation", "CALLS", "--status", status], capture_output=True, text=True, check=True).stdout
        return json.loads(out)["overall"]

    everything, resolved_only = run("all"), run("resolved")
    assert everything["expected"] == resolved_only["expected"]        # the oracle denominator is untouched by the policy
    assert resolved_only["actual"] < everything["actual"] and resolved_only["fn"] > everything["fn"]   # recall honestly drops


def test_inherited_dunder_is_only_a_candidate_when_an_external_base_precedes_the_defining_base(tmp_path: Path):
    _write(tmp_path, {
        "mixin.py": "class Mixin:\n    def __init__(self, *a):\n        self.m = 1\n\n    def run(self):\n        return 1\n",
        "app.py": (
            "import external_lib\nfrom mixin import Mixin\n\n"
            "class ExternalFirst(external_lib.Base, Mixin):\n"
            "    def __init__(self, *a):\n        super().__init__(*a)\n\n"
            "    def go(self):\n        return self.run()\n\n"
            "class MixinFirst(Mixin, external_lib.Base):\n"
            "    def __init__(self, *a):\n        super().__init__(*a)\n\n"
            "    def go(self):\n        return self.run()\n"
        ),
    })
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    rows = {
        (r["s"], r["t"]): (r["resolution_status"], r["resolution_tier"])
        for r in conn.execute(
            "SELECT s.qualified_name AS s, t.qualified_name AS t, e.resolution_status, e.resolution_tier FROM edges e "
            "JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id WHERE e.relation = 'CALLS'"
        )
    }
    conn.close()
    assert rows[("app.py::ExternalFirst.go", "mixin.py::Mixin.run")] == ("resolved", "inherited")     # a custom name: not presumed external
    assert rows[("app.py::ExternalFirst.__init__", "mixin.py::Mixin.__init__")] == ("candidate", "inherited_uncertain")
    assert rows[("app.py::MixinFirst.go", "mixin.py::Mixin.run")] == ("resolved", "inherited")
    assert rows[("app.py::MixinFirst.__init__", "mixin.py::Mixin.__init__")] == ("resolved", "inherited")


def test_inherited_lookup_follows_c3_order_in_a_diamond(tmp_path: Path):
    _write(tmp_path, {
        "d.py": (
            "class Base:\n    def m(self):\n        return 1\n\n"
            "class Left(Base):\n    pass\n\n"
            "class Right(Base):\n    def m(self):\n        return 2\n\n"
            "class Leaf(Left, Right):\n    def m(self):\n        return super().m()\n"
        ),
    })
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    targets = {
        r[0]
        for r in conn.execute(
            "SELECT t.qualified_name FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id "
            "WHERE e.relation = 'CALLS' AND s.qualified_name = 'd.py::Leaf.m' AND e.resolution_status = 'resolved'"
        )
    }
    conn.close()
    assert targets == {"d.py::Right.m"}          # MRO is Leaf, Left, Right, Base: Right overrides Base, Left has no m
