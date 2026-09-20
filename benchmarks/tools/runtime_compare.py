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

    raw_counts, observed, procs, traced_procs = Counter(), set(), set(), set()
    raw_records = 0
    tdir = run / "traced" / "traces"
    for f in glob.glob(str(tdir / "start-*")):
        procs.add(os.path.basename(f)[6:])
    for f in glob.glob(str(tdir / "trace-*.json")):
        d = json.load(open(f))
        traced_procs.add(str(d["pid"]))
        observed |= set(d["observed_symbols"])
        for e in d["edges"]:
            raw_records += 1
            raw_counts[(e["source"], e["target"])] += e["count"]
    prod_syms = {i for i, k in kind_of.items() if k in ("Function", "Method", "Class") and file_of[i] in scope}
    known_ids = set(kind_of) | set(scope)                       # symbols plus file ids (module-level code is the file)

    # ---- normalise: one canonical pair per (caller, callee); keep what cannot be compared, with the reason
    excluded, unmapped = [], []
    runtime_edges = set()
    for (a, b), n in raw_counts.items():
        if a == b:
            excluded.append({"source": a, "target": b, "count": n, "reason": "self edge: recursion, or a nested function projected onto its own enclosing symbol"})
        elif a not in known_ids or b not in known_ids:
            unmapped.append({"source": a, "target": b, "count": n, "reason": "endpoint is not a symbol/file of the static graph (" + ("caller" if a not in known_ids else "callee") + ")"})
        else:
            runtime_edges.add((a, b))

    def static_pairs(status):
        return {(e["source"], e["target"]) for e in ibwd["edges"]
                if e["relation"] in ("CALLS", "REFERENCES", "INHERITS") and e["resolution_status"] == status}

    resolved_edges, candidate_edges = static_pairs("resolved"), static_pairs("candidate")

    def hit(edge, S):
        a, b = edge
        if edge in S:
            return True
        if b.endswith((".__init__", ".__new__", ".__post_init__", ".constructor")):     # class instantiation: static edges point at the class
            return (a, b.rsplit(".", 1)[0]) in S
        return False

    resolved_matches = {e for e in runtime_edges if hit(e, resolved_edges)}
    candidate_only = {e for e in runtime_edges if hit(e, candidate_edges)} - resolved_matches
    absent = runtime_edges - resolved_matches - candidate_only
    assert not (resolved_matches & candidate_only)
    assert not (resolved_matches & absent)
    assert not (candidate_only & absent)
    assert len(resolved_matches) + len(candidate_only) + len(absent) == len(runtime_edges)
    assert len(runtime_edges) + len(excluded) + len(unmapped) == len(raw_counts)

    static_names = {(x, y.split("::", 1)[-1].split(".")[-1]) for (x, y) in resolved_edges | candidate_edges}

    def why(e):
        a, b = e
        name = b.split("::", 1)[-1].split(".")[-1]
        if "::" not in a:
            return "caller_is_module_level_code"
        if (a, name) in static_names:
            return "dispatch_to_override_or_same_name_target_present"
        if name.startswith("__"):
            return "implicit_protocol_call_dunder"
        if e in orc:
            return "in_oracle_graph_only"
        return "other"

    absent_rows = sorted(({"source": a, "target": b, "count": raw_counts[(a, b)], "oracle_basis": orc.get((a, b)), "why": why((a, b))} for a, b in absent),
                         key=lambda r: -r["count"])
    n = len(runtime_edges)
    result = {
        "repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "ibwd": {k: ibwd.get(k) for k in ("ibwd_commit", "ibwd_dirty", "edge_build_version")},
        "tests_without_tracing": junit(run / "plain" / "junit.xml"), "tests_with_tracing": junit(run / "traced" / "junit.xml"),
        "test_outcome_note": "Matching outcomes with and without tracing show outcome agreement only: neither complete instrumentation nor fully passing suites.",
        "status": {m: (run / m / "status.txt").read_text().strip() if (run / m / "status.txt").exists() else None for m in ("plain", "traced")},
        "production_symbols_in_scope": len(prod_syms), "production_symbols_observed": len(observed & prod_syms),
        "accounting": {
            "raw_trace_records": raw_records, "raw_observed_calls": sum(raw_counts.values()), "raw_distinct_pairs": len(raw_counts),
            "excluded_observations": len(excluded), "unmapped_observations": len(unmapped),
            "normalized_unique_edges": n,
            "resolved_matches": len(resolved_matches), "candidate_only": len(candidate_only), "absent": len(absent),
            "check": "resolved_matches + candidate_only + absent == normalized_unique_edges; normalized + excluded + unmapped == raw_distinct_pairs",
        },
        "percentages": {
            "missing_from_default_resolved_graph": round((len(candidate_only) + len(absent)) / n, 4) if n else None,
            "missing_from_resolved_plus_candidate_hints": round(len(absent) / n, 4) if n else None,
        },
        "absent_but_in_oracle_graph": sum(1 for r in absent_rows if r["oracle_basis"]),
        "processes_started": len(procs), "processes_with_trace": len(traced_procs & procs),
        "uninstrumented_processes": sorted(procs - traced_procs)[:20], "uninstrumented_process_count": len(procs - traced_procs),
        "absent_edge_reasons": dict(Counter(r["why"] for r in absent_rows)),
        "excluded_observations_list": excluded[:200], "unmapped_observations_list": unmapped[:200],
        "top_absent_edges": absent_rows[:40],
    }
    Path(out_path).write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"repo": result["repo"], "accounting": result["accounting"], "percentages": result["percentages"], "uninstrumented": f"{result['uninstrumented_process_count']}/{result['processes_started']}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
