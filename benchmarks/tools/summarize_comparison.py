#!/usr/bin/env python3
"""Summarize a compare_pairs.py result: precision, broader semantic coverage, supported-scope recall, tier precision.

Usage: summarize_comparison.py COMPARISON.json ORACLE.json REPO_ROOT

Missing oracle CALLS edges are categorized from the callsite's source text. Categories that need type or data-flow
inference (a receiver that is an attribute chain, a typed local/parameter, or a call result) are the frozen
"unsupported resolution" category (benchmarks/SPRINT3_gate_definition.md section 7); everything else is supported scope.
Both figures are printed; nothing is removed from the broader denominator.
"""
import collections, json, re, sys
from pathlib import Path

def category(edge, site, repo):
    file, line = site[edge]
    lines = (repo / file).read_text(errors="ignore").splitlines()
    text = lines[line - 1] if line - 1 < len(lines) else ""
    name = edge[1].split("::", 1)[-1].rsplit(".", 1)[-1]
    m = re.search(r"([\w\.\)\]\$?!]*?)\.?\b" + re.escape(name) + r"\s*[(<]", text)
    receiver = (m.group(1) if m else "") or ""
    if not receiver: return "bare call"
    if receiver in ("self", "cls", "this"): return "self/cls/this.method"
    if receiver.startswith(("self.", "this.")): return "attribute-chain receiver (type-resolved)"
    if receiver.endswith(")"): return "call-result receiver (type-resolved)"
    return "variable receiver (type-resolved local/param)"


def summarize(cmp_, orc_, repo, adjudicated=None):
    """Return the recall bookkeeping. `adjudicated` maps (source, target) -> verdict for individually reviewed misses."""
    adjudicated = adjudicated or {}
    site = {(e["source"], e["target"], e["relation"]): (e["file"], e["line"]) for e in orc_["edges"]}
    o = cmp_["overall"]
    missing = [tuple(m) for m in cmp_["missing"] if m[2] == "CALLS" and tuple(m) in site]
    cats = collections.Counter(category(m, site, repo) for m in missing)
    declared_unsupported = sum(v for k, v in cats.items() if "type-resolved" in k)
    supported_cat_misses = [m for m in missing if "type-resolved" not in category(m, site, repo)]
    verdicts = collections.Counter(adjudicated.get((m[0], m[1]), "UNADJUDICATED") for m in supported_cat_misses)
    oracle_errors = verdicts.get("oracle_error", 0)
    adj_unsupported = sum(v for k, v in verdicts.items() if k.startswith("unsupported"))
    tp, fn = o["tp"], o["fn"]
    broader_den = o["expected"] - oracle_errors
    supported_den = broader_den - declared_unsupported - adj_unsupported
    return {
        "expected_raw": o["expected"], "tp": tp, "fp": o["fp"], "fn": fn, "actual": o["actual"], "precision_raw": o["precision"],
        "missing_by_category": dict(cats.most_common()),
        "declared_unsupported_type_resolved": declared_unsupported,
        "supported_category_misses": len(supported_cat_misses), "supported_category_miss_verdicts": dict(verdicts),
        "oracle_errors_removed": oracle_errors,
        "broader_denominator": broader_den, "broader_recall": round(tp / broader_den, 4),
        "unsupported_adjudicated": adj_unsupported,
        "supported_denominator": supported_den, "supported_recall": round(tp / supported_den, 4) if supported_den else None,
        "unadjudicated_supported_misses": verdicts.get("UNADJUDICATED", 0),
        "tier_precision": {k: v for k, v in cmp_["by_tier_precision"].items() if k != "None"},
    }


if __name__ == "__main__":
    r = summarize(json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3]))
    print(json.dumps(r, indent=1))
