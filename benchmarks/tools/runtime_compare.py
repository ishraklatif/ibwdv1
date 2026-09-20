#!/usr/bin/env python3
"""Compare observed runtime call edges with the static graph.

Usage: runtime_compare.py REPO MANIFEST IBWD_EXPORT.json ORACLE.json RUN_DIR OUT.json
RUN_DIR holds plain/ and traced/ (each with junit.xml, status.txt, pytest.log; traced/ also traces/trace-<pid>.json + start-<pid>).

Reports: tests collected/passed/failed/skipped with and without tracing (they must agree), production symbols observed, observed
production->production edges, observed edges ABSENT from the static graph (and whether the oracle has them), and uninstrumented
processes (a start marker with no trace file). A runtime edge A->B is `present` if the static graph has CALLS or REFERENCES A->B, or
B is `X.__init__`/`X.__new__` (a class instantiation) and A->X exists, or A is the module/file and any static edge leaves the file.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def junit(path: Path) -> dict:
    if not path.exists():
        return {"available": False}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    tot = Counter()
    for s in suites:
        tot["collected"] += int(s.get("tests", 0))
        tot["failed"] += int(s.get("failures", 0))
        tot["errors"] += int(s.get("errors", 0))
        tot["skipped"] += int(s.get("skipped", 0))
    tot["passed"] = tot["collected"] - tot["failed"] - tot["errors"] - tot["skipped"]
    return {"available": True, **tot}


def main() -> int:
    repo, manifest_path, ibwd_path, oracle_path, run_dir, out_path = sys.argv[1:7]
    run = Path(run_dir)
    manifest = json.loads(Path(manifest_path).read_text())
    ibwd, oracle = json.loads(Path(ibwd_path).read_text()), json.loads(Path(oracle_path).read_text())
    scope = set(manifest["included_files"])
    file_of = {s["id"]: s["file"] for s in ibwd["symbols"]}
    kind_of = {s["id"]: s["kind"] for s in ibwd["symbols"]}
    static = {"resolved": set(), "candidate": set()}
    for e in ibwd["edges"]:
        if e["relation"] in ("CALLS", "REFERENCES", "INHERITS"):
            static[e["resolution_status"]].add((e["source"], e["target"]))
    orc = {(e["source"], e["target"]): e["basis"] for e in oracle["edges"] if e["relation"] in ("CALLS", "REFERENCES")}
    out_edges_of_file = {s: True for (s, _t) in static["resolved"] | static["candidate"]}

    edges, observed, procs, traced_procs = Counter(), set(), set(), set()
    tdir = run / "traced" / "traces"
    for f in glob.glob(str(tdir / "start-*")):
        procs.add(os.path.basename(f)[6:])
    for f in glob.glob(str(tdir / "trace-*.json")):
        d = json.load(open(f))
        traced_procs.add(str(d["pid"]))
        observed |= set(d["observed_symbols"])
        for e in d["edges"]:
            edges[(e["source"], e["target"])] += e["count"]
    # production symbols = functions/methods/classes of the manifest scope in the static graph
    prod_syms = {i for i, k in kind_of.items() if k in ("Function", "Method", "Class") and file_of[i] in scope}

    def present(a: str, b: str) -> str | None:
        for status in ("resolved", "candidate"):
            if (a, b) in static[status]:
                return status
        if b.endswith((".__init__", ".__new__", ".__post_init__", ".constructor")):
            cls = b.rsplit(".", 1)[0]
            for status in ("resolved", "candidate"):
                if (a, cls) in static[status]:
                    return status
        return None

    stat = Counter()
    absent = []
    for (a, b), n in edges.items():
        if a == b or a.split("::")[0] == a and False:
            continue
        p = present(a, b)
        stat[p or "absent"] += 1
        if p is None:
            absent.append({"source": a, "target": b, "count": n, "oracle_basis": orc.get((a, b))})
    absent.sort(key=lambda r: -r["count"])
    # classify why an observed edge is not a static edge (heuristic, for reading the result; not a verdict)
    static_targets_by_name: dict = {}
    for status in ("resolved", "candidate"):
        for (a, b) in static[status]:
            static_targets_by_name.setdefault((a, b.split("::", 1)[-1].split(".")[-1]), True)
    callee_dunder = lambda b: b.split("::", 1)[-1].split(".")[-1].startswith("__")
    for r in absent:
        a, b = r["source"], r["target"]
        name = b.split("::", 1)[-1].split(".")[-1]
        if "::" not in a:
            r["why"] = "caller_is_module_level_code"
        elif (a, name) in static_targets_by_name:
            r["why"] = "dispatch_to_override_or_same_name_target_present"
        elif callee_dunder(b):
            r["why"] = "implicit_protocol_call_dunder"
        elif r["oracle_basis"]:
            r["why"] = "in_oracle_graph_only"
        else:
            r["why"] = "other"
    why = Counter(r["why"] for r in absent)
    result = {
        "repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "ibwd": {k: ibwd.get(k) for k in ("ibwd_commit", "ibwd_dirty", "edge_build_version")},
        "tests_without_tracing": junit(run / "plain" / "junit.xml"), "tests_with_tracing": junit(run / "traced" / "junit.xml"),
        "status": {m: (run / m / "status.txt").read_text().strip() if (run / m / "status.txt").exists() else None for m in ("plain", "traced")},
        "production_symbols_in_scope": len(prod_syms), "production_symbols_observed": len(observed & prod_syms),
        "observed_production_to_production_edges": len(edges), "of_which_in_static_resolved": stat["resolved"], "of_which_only_static_candidate": stat["candidate"],
        "observed_edges_absent_from_static_graph": stat["absent"],
        "absent_but_in_oracle_graph": sum(1 for r in absent if r["oracle_basis"]),
        "absent_and_not_in_oracle_graph": sum(1 for r in absent if not r["oracle_basis"]),
        "processes_started": len(procs), "processes_with_trace": len(traced_procs & procs),
        "uninstrumented_processes": sorted(procs - traced_procs)[:20], "uninstrumented_process_count": len(procs - traced_procs),
        "absent_edge_reasons": dict(why),
        "top_absent_edges": absent[:40],
    }
    Path(out_path).write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("top_absent_edges", "uninstrumented_processes", "ibwd")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
