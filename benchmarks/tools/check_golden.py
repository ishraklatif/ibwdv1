#!/usr/bin/env python3
"""Check IBWD's answers to the qualified golden tasks (AFTER qualification; the tasks were chosen from the oracle graph alone).

Usage: check_golden.py GROUND_TRUTH.yaml SNAPSHOT_DIR OUT.json
SNAPSHOT_DIR holds the scanned `.ibwd/graph.db` of the pinned commit. Answers use the frozen default policy (resolved edges only,
CALLS, depth 1; Q3 = CALLS-only shortest path) and, separately, with candidate hints included. A task passes when IBWD's answer set
equals the golden set exactly (Q1/Q2/small) or IBWD returns the golden three-hop path (Q3). The YAML is the subset benchmarks/tools/
qualify_tasks.py writes (JSON-quoted scalars), parsed here without a YAML library and round-tripped to prove the parse is exact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qualify_tasks import yaml_dump  # noqa: E402

from ibwd.graph.database import connect  # noqa: E402
from ibwd.retrieval.traversal import build_call_subgraph, callers_of, dependents_of, find_path  # noqa: E402


def parse_yaml(text: str):
    lines = [[len(l) - len(l.lstrip(" ")), l.strip()] for l in text.splitlines() if l.strip()]

    def scalar(v: str):
        if v[:1] == '"':
            return json.loads(v)
        if v in ("null", "true", "false"):
            return {"null": None, "true": True, "false": False}[v]
        if v in ("[]", "{}"):
            return [] if v == "[]" else {}
        return int(v) if v.lstrip("-").isdigit() else float(v)

    def block(i: int, indent: int):
        if lines[i][1].startswith("- "):
            out = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                content = lines[i][1][2:]
                if content.endswith(":") or (": " in content and content[:1] != '"'):
                    lines[i] = [indent + 2, content]          # the dict's first key sits after the dash
                    val, i = block(i, indent + 2)
                    out.append(val)
                else:
                    out.append(scalar(content)); i += 1
            return out, i
        d = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            k, _, v = lines[i][1].partition(":")
            v = v.strip()
            i += 1
            if v == "":
                d[k], i = block(i, lines[i][0])
            else:
                d[k] = scalar(v)
        return d, i

    return block(0, 0)[0]


def main() -> int:
    gt_path, snap, out_path = sys.argv[1:4]
    text = Path(gt_path).read_text()
    gt = parse_yaml(text)
    assert yaml_dump(gt) + "\n" == text, "YAML parse did not round-trip exactly"
    conn = connect(Path(snap) / ".ibwd" / "graph.db")

    def node(qual_id: str):
        f, _, q = qual_id.partition("::")
        row = conn.execute("SELECT id FROM nodes WHERE file_path = ? AND qualified_name = ?", (f, qual_id)).fetchone()
        return row["id"] if row else None

    def qid(nid: int) -> str:
        return conn.execute("SELECT qualified_name FROM nodes WHERE id = ?", (nid,)).fetchone()[0]

    results = []
    for t in gt["tasks"]:
        r = {"id": t["id"], "stratum": t["stratum"]}
        for label, cand in (("default_resolved_only", False), ("with_candidate_hints", True)):
            if t["stratum"] == "Q1":
                nid = node(t["target"]["id"])
                got = {qid(x.node_id) for x in callers_of(conn, nid, 1, cand) if "CALLS" in x.relations} if nid else None
                want = {e["caller"] for e in t["expected"]}
            elif t["stratum"] in ("Q2", "small"):
                nid = node(t["source"]["id"])
                got = {qid(x.node_id) for x in dependents_of(conn, nid, 1, cand) if "CALLS" in x.relations} if nid else None
                want = {e["callee"] for e in t["expected"]}
            else:                                   # Q3
                a, b = node(t["source"]["id"]), node(t["target"]["id"])
                graph = build_call_subgraph(conn, ("CALLS",), cand)
                path = find_path(graph, a, b) if a and b else None
                got = [qid(x) for x in path] if path else None
                want = t["expected"]["path"]
            ok = got == want if isinstance(want, list) else got == want
            r[label] = {"correct": bool(ok), "expected": sorted(want) if isinstance(want, set) else want,
                        "got": sorted(got) if isinstance(got, set) else got,
                        "missing": sorted(want - got) if isinstance(want, set) and isinstance(got, set) else None,
                        "extra": sorted(got - want) if isinstance(want, set) and isinstance(got, set) else None}
        results.append(r)
    Path(out_path).write_text(json.dumps({"ground_truth": gt_path, "results": results}, indent=1) + "\n")
    for r in results:
        d, c = r["default_resolved_only"], r["with_candidate_hints"]
        print(f"{r['id']:26} default: {'OK ' if d['correct'] else 'WRONG'} (missing {d['missing']}, extra {d['extra']}) | with candidates: {'OK' if c['correct'] else 'WRONG'} (extra {c['extra']})")
    return 0 if all(r["default_resolved_only"]["correct"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
