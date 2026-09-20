"""runtime_compare: categories reconcile exactly and every observation is accounted for."""
import json
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "benchmarks" / "tools" / "runtime_compare.py"


def test_partition_and_exclusions(tmp_path: Path):
    syms = [{"id": i, "file": i.split("::")[0], "kind": "Function"} for i in ("a.py::f", "a.py::g", "b.py::h", "b.py::C.__init__")]
    syms.append({"id": "b.py::C", "file": "b.py", "kind": "Class"})
    edges = [{"source": "a.py::f", "target": "a.py::g", "relation": "CALLS", "resolution_status": "resolved"},
             {"source": "a.py::g", "target": "b.py::h", "relation": "CALLS", "resolution_status": "candidate"},
             {"source": "a.py::f", "target": "b.py::C", "relation": "CALLS", "resolution_status": "resolved"}]
    (tmp_path / "ibwd.json").write_text(json.dumps({"symbols": syms, "edges": edges, "ibwd_commit": "x", "ibwd_dirty": False, "edge_build_version": 1}))
    (tmp_path / "oracle.json").write_text(json.dumps({"edges": []}))
    (tmp_path / "m.json").write_text(json.dumps({"repo": "t", "repo_sha": "s", "included_files": ["a.py", "b.py"]}))
    tr = tmp_path / "run" / "traced" / "traces"
    tr.mkdir(parents=True)
    obs = [("a.py::f", "a.py::g", 3), ("a.py::g", "b.py::h", 1), ("a.py::f", "b.py::C.__init__", 2), ("b.py::h", "a.py::f", 5),
           ("a.py::f", "a.py::f", 9), ("zzz.py::q", "a.py::f", 1)]
    # the same pair appears in two processes: it must be deduplicated
    for pid, rows in (("1", obs[:4] + obs[4:]), ("2", obs[:1])):
        (tr / f"trace-{pid}.json").write_text(json.dumps({"pid": int(pid), "observed_symbols": ["a.py::f"], "edges": [
            {"source": a, "target": b, "count": n, "original": None} for a, b, n in rows]}))
        (tr / f"start-{pid}").write_text("")
    out = tmp_path / "out.json"
    subprocess.run([sys.executable, str(TOOL), "t", str(tmp_path / "m.json"), str(tmp_path / "ibwd.json"), str(tmp_path / "oracle.json"), str(tmp_path / "run"), str(out)],
                   check=True, capture_output=True)
    r = json.loads(out.read_text())["accounting"]
    assert (r["raw_distinct_pairs"], r["excluded_observations"], r["unmapped_observations"], r["normalized_unique_edges"]) == (6, 1, 1, 4)
    assert (r["resolved_matches"], r["candidate_only"], r["absent"]) == (2, 1, 1)      # f->g, f->C.__init__ (instantiation) / g->h / h->f
    p = json.loads(out.read_text())["percentages"]
    assert p["missing_from_default_resolved_graph"] == 0.5 and p["missing_from_resolved_plus_candidate_hints"] == 0.25
