#!/usr/bin/env python3
"""Compare an IBWD export against an oracle graph at (caller symbol -> target symbol, relation) granularity.

Adapted from the Sprint 3 verification kit's compare_graphs.py, which matched edges on callsite
file:line — IBWD edges carry no callsite positions, so this compares pairs. Consequences:
  * it can show that a relation is missing or spurious, not that a particular callsite was missed;
  * keep the oracle's callsite evidence separately and state that limit in the verdict.

Usage:
  compare_pairs.py ORACLE.json IBWD.json [--scope AUDIT.json] [--relation CALLS] [--max-list 50]

ORACLE.json: {symbols:[{id,file,line,name}], edges:[{source,target,relation,...}]} (kit format).
Oracle symbol ids ("file:line:name") are mapped onto IBWD ids ("file::Outer.inner") by (file, qualified
name), falling back to (file, last name segment) when that is unique. Oracle symbols that cannot be
mapped are reported, never silently dropped. --scope restricts both graphs to an audit's production files.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


def build_mapping(oracle_symbols: list[dict], ibwd_symbols: list[dict]):
    exact = {(s["file"], s["qualname"]): s["id"] for s in ibwd_symbols if s.get("qualname")}
    by_last: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for s in ibwd_symbols:
        if s.get("qualname"):
            by_last[(s["file"], s["qualname"].rsplit(".", 1)[-1])].append(s["id"])
    ibwd_ids = {s["id"] for s in ibwd_symbols}
    mapping: dict[str, str | None] = {}
    for s in oracle_symbols:
        if s.get("kind") == "File":  # file nodes (module-level callers, IMPORTS) share the path as their id
            mapping[s["id"]] = s["id"] if s["id"] in ibwd_ids else None
            continue
        name = s["name"]
        target = exact.get((s["file"], name))
        if target is None:
            candidates = by_last.get((s["file"], name.rsplit(".", 1)[-1]), [])
            target = candidates[0] if len(candidates) == 1 else None
        mapping[s["id"]] = target
    return mapping


def scores(expected: set, actual: set) -> dict:
    tp = len(expected & actual)
    return {
        "expected": len(expected), "actual": len(actual), "tp": tp,
        "fp": len(actual - expected), "fn": len(expected - actual),
        "precision": round(tp / len(actual), 4) if actual else None,
        "recall": round(tp / len(expected), 4) if expected else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("oracle"); ap.add_argument("ibwd")
    ap.add_argument("--scope", help="audit JSON whose production_files bound the comparison")
    ap.add_argument("--manifest", help="benchmark manifest whose included_files bound the comparison (preferred over --scope)")
    ap.add_argument("--status", choices=["resolved", "candidate", "all"], default="all", help="only compare IBWD edges with this resolution_status")
    ap.add_argument("--relation", action="append", help="restrict to these relations (default: all shared)")
    ap.add_argument("--ext", action="append", help="only compare files with these extensions (e.g. .py): the oracle may cover one language")
    ap.add_argument("--max-list", type=int, default=50)
    args = ap.parse_args()

    oracle, ibwd = load(args.oracle), load(args.ibwd)
    manifest = load(args.manifest) if args.manifest else None
    scope = set(manifest["included_files"]) if manifest else (set(load(args.scope)["production_files"]) if args.scope else None)
    mapping = build_mapping(oracle["symbols"], ibwd["symbols"])
    ibwd_file = {s["id"]: s["file"] for s in ibwd["symbols"]}

    exts = tuple(args.ext) if args.ext else None

    def in_scope(symbol_id: str) -> bool:
        f = ibwd_file.get(symbol_id)
        if exts and not (f or "").endswith(exts):
            return False
        return scope is None or f in scope

    unmapped = sorted(s["id"] for s in oracle["symbols"] if mapping.get(s["id"]) is None and (scope is None or s["file"] in scope))
    O: set[tuple] = set(); dropped = 0
    for e in oracle["edges"]:
        a, b = mapping.get(e["source"]), mapping.get(e["target"])
        if a is None or b is None:
            dropped += 1
            continue
        if in_scope(a) and in_scope(b):
            O.add((a, b, e["relation"]))
    ibwd_edges = [e for e in ibwd["edges"] if args.status == "all" or e.get("resolution_status", "resolved") == args.status]
    B = {(e["source"], e["target"], e["relation"]) for e in ibwd_edges if in_scope(e["source"]) and in_scope(e["target"])}
    tier_of = {(e["source"], e["target"], e["relation"]): e.get("tier") for e in ibwd_edges}

    relations = sorted(args.relation or ({r for *_, r in O} | {r for *_, r in B}))
    O = {x for x in O if x[2] in relations}; B = {x for x in B if x[2] in relations}
    # only compare on sources the oracle could see: oracle edge sets are partial by construction (see `complete`)
    tiers = {}
    for tier in sorted({str(v) for v in tier_of.values()}):
        actual = {k for k in B if str(tier_of.get(k)) == tier}
        tiers[tier] = {"predictions": len(actual), "precision": round(len(actual & O) / len(actual), 4) if actual else None}

    uses = {"CALLS", "REFERENCES"}
    oracle_used = {t for (_, t, r) in O if r in uses}; ibwd_used = {t for (_, t, r) in B if r in uses}
    out = {
        "manifest_sha256": manifest["manifest_sha256"] if manifest else None,
        "ibwd_status_filter": args.status,
        "oracle_complete": oracle.get("complete", False),
        "oracle_status": oracle.get("status"),
        "granularity": "caller symbol -> target symbol per relation (no callsite positions in IBWD edges)",
        "overall": scores(O, B),
        "by_relation": {r: scores({x for x in O if x[2] == r}, {x for x in B if x[2] == r}) for r in relations},
        "by_tier_precision": tiers,
        "symbol_mapping": {"oracle_symbols_unmapped": len(unmapped), "oracle_edges_dropped_unmapped": dropped, "unmapped_sample": unmapped[:10]},
        "false_no_static_use": sorted(oracle_used - ibwd_used)[: args.max_list],
        "missing": sorted(O - B)[: args.max_list],
        "spurious": sorted(B - O)[: args.max_list],
    }
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
