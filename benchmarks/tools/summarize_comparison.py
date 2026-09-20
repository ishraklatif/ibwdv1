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

cmp_, orc_, repo = json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3])
site = {(e["source"], e["target"], e["relation"]): (e["file"], e["line"]) for e in orc_["edges"]}

def category(edge):
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

o = cmp_["overall"]
missing = [tuple(m) for m in cmp_["missing"] if m[2] == "CALLS" and tuple(m) in site]
cats = collections.Counter(category(m) for m in missing)
unsupported = sum(v for k, v in cats.items() if "type-resolved" in k)
tp, fn = o["tp"], o["fn"]
print(f"CALLS  expected={o['expected']} actual={o['actual']} tp={tp} fp={o['fp']} fn={fn}")
print(f"  precision vs oracle (raw, unadjudicated): {o['precision']}")
print(f"  broader semantic coverage (recall over ALL oracle edges): {tp}/{tp+fn} = {tp/(tp+fn):.3f}")
print(f"  supported-scope recall (declared type/data-flow category excluded): {tp}/{tp+fn-unsupported} = {tp/(tp+fn-unsupported):.3f}")
print("  missing by category:", dict(cats.most_common()))
print("  tier precision:", {k: (v["predictions"], v["precision"]) for k, v in cmp_["by_tier_precision"].items() if k != "None"})
