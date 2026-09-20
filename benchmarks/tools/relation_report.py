#!/usr/bin/env python3
"""Relation-level comparison of an IBWD export against an oracle graph, with resolved edges and candidate hints separate.

Usage: relation_report.py MANIFEST.json ORACLE.json IBWD_EXPORT.json OUT.json [--adjudications ADJ.json]

Both graphs are filtered by the same manifest. For each relation (CALLS, IMPORTS, INHERITS, REFERENCES) it reports, for the
resolved edges and for the candidate hints separately: TP, FP, FN, raw precision. The oracle denominator is identical for both
(candidates leaving the default graph never narrow it). Recall is reported three ways:
  supported-scope   oracle edges whose resolution_basis is binding/path (statically bound)
  broader           all oracle edges except individually adjudicated oracle errors
  with_candidates   resolved + candidate edges together
An adjudication record's verdict is one of ibwd_defect | oracle_error | legitimate_out_of_scope | unresolved_disagreement.
unresolved_disagreement is never counted as proven correctness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

RELATIONS = ("CALLS", "IMPORTS", "INHERITS", "REFERENCES")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest"); ap.add_argument("oracle"); ap.add_argument("ibwd"); ap.add_argument("out")
    ap.add_argument("--adjudications")
    ap.add_argument("--ext", action="append", help="restrict to files with these extensions (oracle covers one language)")
    args = ap.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())
    oracle_raw, ibwd_raw = Path(args.oracle).read_bytes(), Path(args.ibwd).read_bytes()
    oracle, ibwd = json.loads(oracle_raw), json.loads(ibwd_raw)
    included = set(manifest["included_files"])
    exts = tuple(args.ext) if args.ext else None
    file_of = {s["id"]: s["file"] for s in ibwd["symbols"]}
    o_file = {s["id"]: s["file"] for s in oracle["symbols"]}

    def ok(sid, table):
        f = table.get(sid)
        return f in included and (not exts or f.endswith(exts))

    adj = {}
    if args.adjudications:
        for rec in json.loads(Path(args.adjudications).read_text())["records"]:
            if rec["repo_sha"] == manifest["repo_sha"]:
                adj[(rec["source"], rec["target"], rec["relation"])] = rec

    O = {}          # (s, t, r) -> basis
    unmapped = 0
    unmapped_edges: set = set()
    for e in oracle["edges"]:
        s, t = e["source"], e["target"]
        if not (ok(s, o_file) and ok(t, o_file)):
            continue
        if s not in file_of or t not in file_of:
            unmapped += 1          # IBWD has no such symbol (e.g. a function inside an IIFE): the edge stays in the denominator as a miss
            unmapped_edges.add((s, t, e["relation"]))
        O[(s, t, e["relation"])] = e["basis"]
    B = {"resolved": {}, "candidate": {}}
    for e in ibwd["edges"]:
        s, t = e["source"], e["target"]
        if ok(s, file_of) and ok(t, file_of):
            B[e["resolution_status"]][(s, t, e["relation"])] = e.get("tier")

    result = {
        "repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "manifest_sha256": manifest["manifest_sha256"],
        "ibwd": {k: ibwd.get(k) for k in ("ibwd_commit", "ibwd_dirty", "edge_build_version")},
        "oracle": {"name": oracle.get("oracle"), "adapter": oracle.get("adapter"), "text_encoding": oracle.get("text_encoding")},
        "input_sha256": {"ibwd_export": hashlib.sha256(ibwd_raw).hexdigest(), "oracle_export": hashlib.sha256(oracle_raw).hexdigest()},
        "oracle_edges_whose_symbol_ibwd_does_not_index": unmapped, "relations": {},
    }
    disagreements = []
    for rel in RELATIONS:
        Or = {k: v for k, v in O.items() if k[2] == rel}
        oracle_errors = {k for k in Or if adj.get(k, {}).get("verdict") == "oracle_error"}
        supported = {k for k, b in Or.items() if b in ("binding", "path")}
        block = {"oracle_edges": len(Or), "oracle_supported_scope": len(supported), "oracle_declared_type_declared": len(Or) - len(supported),
                 "oracle_errors_removed": len(oracle_errors)}
        for status in ("resolved", "candidate"):
            Br = {k for k in B[status] if k[2] == rel}
            tp, fp, fn = Or.keys() & Br, Br - Or.keys(), Or.keys() - Br
            fp_v = Counter(adj.get(k, {}).get("verdict", "UNADJUDICATED") for k in fp)
            corrected_tp = len(tp) + fp_v.get("oracle_error", 0)
            out_of_scope = fp_v.get("legitimate_out_of_scope", 0)
            den = len(Br) - out_of_scope
            block[status] = {
                "ibwd_edges": len(Br), "TP": len(tp), "FP": len(fp), "FN": len(fn),
                "raw_precision": round(len(tp) / len(Br), 4) if Br else None,
                "adjudicated_precision": round(corrected_tp / den, 4) if den else None,
                "fp_verdicts": dict(fp_v),
                "unresolved_disagreements": sum(1 for k in fp if adj.get(k, {}).get("verdict") == "unresolved_disagreement"),
                "unadjudicated_fp": fp_v.get("UNADJUDICATED", 0),
            }
            tiers = defaultdict(lambda: [0, 0])
            for k in Br:
                tiers[B[status][k]][0 if k in Or.keys() else 1] += 1
            block[status]["by_tier"] = {
                t: {"ibwd_edges": tp_ + fp_, "TP": tp_, "FP": fp_, "raw_precision": round(tp_ / (tp_ + fp_), 4)}
                for t, (tp_, fp_) in sorted(tiers.items())
            }
            if status == "resolved":
                fn_supported = fn & supported
                fn_v = Counter(adj.get(k, {}).get("verdict", "UNADJUDICATED") for k in fn_supported)
                broader_den = len(Or) - len(oracle_errors)
                block[status].update({
                    "supported_scope_TP": len(tp & supported), "supported_scope_FN": len(fn_supported),
                    "supported_scope_recall": round(len(tp & supported) / len(supported), 4) if supported else None,
                    "broader_semantic_coverage": round(len(tp) / broader_den, 4) if broader_den else None, "broader_denominator": broader_den,
                    "supported_FN_verdicts": dict(fn_v), "unadjudicated_supported_FN": fn_v.get("UNADJUDICATED", 0),
                })
                for k in fn:
                    disagreements.append({"kind": "FN", "basis": Or[k], "source": k[0], "target": k[1], "relation": k[2], "in_supported_scope": k in supported,
                                          "symbol_not_indexed": k in unmapped_edges})
            else:
                withc = len(Or.keys() & (Br | {k for k in B["resolved"] if k[2] == rel}))
                block[status]["recall_resolved_plus_candidates"] = round(withc / len(Or), 4) if Or else None
            for k in fp:
                disagreements.append({"kind": f"FP_{status}", "tier": B[status][k], "source": k[0], "target": k[1], "relation": k[2]})
        result["relations"][rel] = block
    result["disagreements"] = disagreements
    Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    for rel, b in result["relations"].items():
        r, c = b["resolved"], b["candidate"]
        print(f"{result['repo']:9} {rel:10} oracle {b['oracle_edges']:>5} | resolved TP {r['TP']:>5} FP {r['FP']:>4} FN {r['FN']:>4} rawP {r['raw_precision']} "
              f"suppRecall {r.get('supported_scope_recall')} broader {r.get('broader_semantic_coverage')} | candidates {c['ibwd_edges']:>4} (TP {c['TP']}, rawP {c['raw_precision']})")
        if rel == "CALLS":
            print("          tiers:", {t: f"{v['TP']}/{v['ibwd_edges']}={v['raw_precision']}" for t, v in r["by_tier"].items()},
                  "| candidates:", {t: f"{v['TP']}/{v['ibwd_edges']}={v['raw_precision']}" for t, v in c["by_tier"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
