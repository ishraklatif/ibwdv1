#!/usr/bin/env python3
"""Mandatory free diagnostics (gate definition section 7): no-static-use and hard-case checks.

Usage: diagnostics.py MANIFEST IBWD_EXPORT.json ORACLE.json OUT.json [--ext .py | --ext .ts --ext .tsx]

no-static-use  What IBWD calls "no callers / not used" is what an agent would act on ("safe to delete"). For every function/method/
               class in the manifest scope with NO resolved incoming CALLS/REFERENCES edge in IBWD, the oracle is asked whether ANY
               static use exists (CALLS or REFERENCES, any basis, from another symbol, production files). A use the oracle finds is
               a false "no static use" claim. Reported with candidate hints excluded and included.
hard-case      Restricts the CALLS comparison to the cases that break name-based resolution: (a) ambiguous names (the same simple name
               is defined more than once in the repository), (b) methods reached through `self`/`this` on a base class, (c) edges whose
               caller is a nested function rolled up to its enclosing symbol. Precision/recall on that subset only.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main() -> int:
    manifest_path, ibwd_path, oracle_path, out_path = sys.argv[1:5]
    exts = tuple(sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--ext")
    manifest = json.loads(Path(manifest_path).read_text())
    ibwd, oracle = json.loads(Path(ibwd_path).read_text()), json.loads(Path(oracle_path).read_text())
    scope = {f for f in manifest["included_files"] if not exts or f.endswith(exts)}
    file_of = {s["id"]: s["file"] for s in ibwd["symbols"]}
    kind_of = {s["id"]: s["kind"] for s in ibwd["symbols"]}
    syms = [i for i, k in kind_of.items() if k in ("Function", "Method", "Class") and file_of[i] in scope]

    def name_of(sid: str) -> str:
        return sid.split("::", 1)[1].split(".")[-1]

    resolved_in, candidate_in, oracle_in = defaultdict(set), defaultdict(set), defaultdict(set)
    for e in ibwd["edges"]:
        if e["relation"] in ("CALLS", "REFERENCES") and e["source"] != e["target"] and file_of.get(e["source"]) in scope:
            (resolved_in if e["resolution_status"] == "resolved" else candidate_in)[e["target"]].add(e["source"])
    for e in oracle["edges"]:
        if e["relation"] in ("CALLS", "REFERENCES") and e["source"] != e["target"] and e["source"].split("::")[0] in scope:
            oracle_in[e["target"]].add((e["source"], e["relation"], e["basis"]))

    def no_static_use(with_candidates: bool) -> dict:
        claims = [s for s in syms if not resolved_in.get(s) and not (with_candidates and candidate_in.get(s))]
        wrong = [s for s in claims if oracle_in.get(s)]
        definite = [s for s in wrong if any(b == "binding" for _, _, b in oracle_in[s])]
        return {"symbols_claimed_unused": len(claims), "oracle_finds_a_use": len(wrong), "of_which_lexically_bound": len(definite),
                "false_claim_rate": round(len(wrong) / len(claims), 4) if claims else None,
                "false_claims_bound_lexically": [{"symbol": s, "uses": sorted({f"{u}:{r}" for u, r, b in oracle_in[s] if b == "binding"})[:4]} for s in definite],
                "false_claims_type_declared_only": len(wrong) - len(definite)}

    # ---- hard cases (CALLS only, over the same edge sets the relation report uses)
    O = {(e["source"], e["target"]): e["basis"] for e in oracle["edges"] if e["relation"] == "CALLS" and e["source"].split("::")[0] in scope}
    B = {"resolved": {}, "candidate": {}}
    for e in ibwd["edges"]:
        if e["relation"] == "CALLS" and file_of.get(e["source"]) in scope:
            B[e["resolution_status"]][(e["source"], e["target"])] = e.get("tier")
    name_count = Counter(name_of(s) for s in syms)
    ambiguous = {n for n, c in name_count.items() if c >= 2}
    nested_source = {s["id"] for s in oracle["symbols"] if s.get("nested")}
    # (c) an oracle occurrence whose original owner differs from its projected owner
    projected_pairs = {(o["projected_owner_id"], o["target_id"]) for o in oracle.get("occurrences", [])
                       if o["relation"] == "CALLS" and o["original_owner_id"] != o["projected_owner_id"]}

    def slice_(pred, label):
        out = {"label": label}
        for status in ("resolved", "candidate"):
            Br = {k for k in B[status] if pred(k)}
            Or = {k for k in O if pred(k)}
            tp = Br & Or
            out[status] = {"ibwd_edges": len(Br), "TP": len(tp), "FP": len(Br - Or), "raw_precision": round(len(tp) / len(Br), 4) if Br else None}
        Or = {k for k in O if pred(k)}
        Bres, Bcand = {k for k in B["resolved"] if pred(k)}, {k for k in B["candidate"] if pred(k)}
        supported = {k for k in Or if O[k] != "type_declared"}
        out["oracle_edges"], out["oracle_supported_scope"] = len(Or), len(supported)
        out["supported_recall_resolved"] = round(len(supported & Bres) / len(supported), 4) if supported else None
        out["supported_missed_but_present_as_candidate"] = len((supported - Bres) & Bcand)
        out["supported_missed_entirely"] = len(supported - Bres - Bcand)
        return out

    hard = {
        "ambiguous_target_name": slice_(lambda k: name_of(k[1]) in ambiguous, "the callee's simple name is defined more than once in the repository"),
        "self_or_this_inherited": slice_(lambda k: B["resolved"].get(k) == "inherited" or O.get(k) == "binding" and "." in k[1].split("::", 1)[1] and "." in k[0].split("::", 1)[1] and k[0].split("::")[0] != k[1].split("::")[0], "callee is a method in ANOTHER file than the caller (inheritance / imported class)"),
        "nested_caller_rolled_up": slice_(lambda k: k in projected_pairs, "caller is a nested function rolled up to its enclosing symbol"),
    }
    out = {"repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "ibwd": {k: ibwd.get(k) for k in ("ibwd_commit", "ibwd_dirty", "edge_build_version")},
           "scope_symbols": len(syms), "no_static_use": {"resolved_only": no_static_use(False), "with_candidate_hints": no_static_use(True)},
           "hard_cases": hard,
           "note": "the oracle cannot see dynamic dispatch, decorators/frameworks that register handlers by name, or reflection: a symbol with no oracle use may still be used at runtime"}
    Path(out_path).write_text(json.dumps(out, indent=1) + "\n")
    r, c = out["no_static_use"]["resolved_only"], out["no_static_use"]["with_candidate_hints"]
    print(f"{manifest['repo']:18} no-static-use claims {r['symbols_claimed_unused']:4} (wrong: {r['oracle_finds_a_use']:3}, lexically bound {r['of_which_lexically_bound']:3}) "
          f"| with candidates {c['symbols_claimed_unused']:4} (wrong {c['oracle_finds_a_use']:3}, lexical {c['of_which_lexically_bound']:3}) "
          f"| hard-case ambiguous P={hard['ambiguous_target_name']['resolved']['raw_precision']} R={hard['ambiguous_target_name']['supported_recall_resolved']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
