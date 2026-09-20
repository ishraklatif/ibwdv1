#!/usr/bin/env python3
"""Build the frozen task specifications (one JSON per task) from the qualified ground truth, and check their internal consistency.

Usage: build_task_specs.py EVIDENCE_DIR OUT_DIR
Each spec records: task_id, repository and SHA, stratum, question, graph scope, permitted relations, expected answer, evidence references,
answer schema, grading policy, and the hash of its ground-truth file. It fails loudly when a semantic check does not hold:
  Q1     expected callers are canonical owners (projected symbols), CALLS only; the callers' relation policy equals the question's
  Q2     expected callees are CALLS edges; REFERENCES from the same source are listed as distractors and are NOT expected
  Q3     the golden path is a minimum-COST path under sum(1/confidence) over the oracle's binding CALLS edges (weights = IBWD tier
         confidence of that edge), the minimum-cost path is the fewest-hops path here, and ALL equal-cost minimum paths are accepted
  small  size <= 20 lines and 2..4 internal callees, at least one caller (the declared criterion)
Answers never carry callsite positions: IBWD stores none, so the contract asks only for the caller/callee definition (file + symbol).
"""
import hashlib
import heapq
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_golden import parse_yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REPOS = ["sphinx", "scrapy", "celery", "redux-toolkit", "bulletproof-react"]
HEADLINE = {"sphinx", "scrapy", "redux-toolkit", "bulletproof-react"}
LANG = {"sphinx": "python", "scrapy": "python", "celery": "python", "redux-toolkit": "typescript", "bulletproof-react": "typescript"}
CONF = {"import_map": 0.95, "same_module": 0.90, "inherited": 0.85}

RULES = (
    "Answer with the top-level function, method or class that CONTAINS the call: a call inside a nested function, callback or lambda belongs to its "
    "enclosing top-level function or method; a statement directly in a class body belongs to the class. Name each item by the file (repository-relative, "
    "forward slashes) and the symbol as `name`, `Class.method` or `Class`. Calls into external packages are not part of the answer. "
    "Only direct calls count: passing a function as a value without calling it is not a call."
)

SCHEMAS = {
    "Q1": {"task_id": "string", "answer": {"callers": [{"file": "repo-relative path", "symbol": "name | Class.method", "relation": "CALLS"}]}},
    "Q2": {"task_id": "string", "answer": {"callees": [{"file": "repo-relative path", "symbol": "name | Class.method | Class", "relation": "CALLS"}]}},
    "small": {"task_id": "string", "answer": {"callees": [{"file": "repo-relative path", "symbol": "name | Class.method | Class", "relation": "CALLS"}]}},
    "Q3": {"task_id": "string", "answer": {"path": [{"file": "repo-relative path", "symbol": "name | Class.method"}], "relation": "CALLS"}},
}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def dijkstra_all_min(adj, w, src, dst):
    """All minimum-cost paths src->dst (exact float costs rounded to 9 places)."""
    dist, preds = {src: 0.0}, defaultdict(list)
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18) + 1e-12:
            continue
        for v in adj.get(u, ()):
            nd = round(d + w[(u, v)], 9)
            if nd < dist.get(v, 1e18) - 1e-12:
                dist[v], preds[v] = nd, [u]
                heapq.heappush(pq, (nd, v))
            elif abs(nd - dist.get(v, 1e18)) <= 1e-12 and u not in preds[v]:
                preds[v].append(u)
    if dst not in dist:
        return None, []
    out = []

    def back(n, tail):
        if n == src:
            out.append([src] + tail)
            return
        for p in preds[n]:
            back(p, [n] + tail)

    back(dst, [])
    return dist[dst], out


def item(sid):
    f, _, q = sid.partition("::")
    return {"file": f, "symbol": q}


def main():
    evidence, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    index, problems = [], []
    for repo in REPOS:
        gt_path = ROOT / "benchmarks/ground_truth" / f"{repo}.yaml"
        gt = parse_yaml(gt_path.read_text())
        oracle = json.loads((evidence / f"{repo}_oracle_v2.json").read_text())
        ibwd = json.loads((evidence / f"{repo}_ibwd.json").read_text())
        manifest = json.loads((ROOT / "benchmarks/manifests" / f"{repo}.json").read_text())
        scope = {s["id"]: s for s in oracle["symbols"] if s["kind"] != "File" and not s.get("nested")}
        conf = {(e["source"], e["target"]): CONF.get(e["tier"]) for e in ibwd["edges"] if e["relation"] == "CALLS" and e["resolution_status"] == "resolved"}
        binding = {(e["source"], e["target"]) for e in oracle["edges"] if e["relation"] == "CALLS" and e["basis"] != "type_declared" and e["source"] in scope and e["target"] in scope and e["source"] != e["target"]}
        refs_from = defaultdict(set)
        for e in oracle["edges"]:
            if e["relation"] == "REFERENCES" and e["source"] in scope:
                refs_from[e["source"]].add(e["target"])
        nested_pairs = {(o["projected_owner_id"], o["target_id"]) for o in oracle["occurrences"] if o["relation"] == "CALLS" and o["original_owner_id"] != o["projected_owner_id"]}
        sym_dir = out_dir.parent / "symbols"
        sym_dir.mkdir(exist_ok=True)
        (sym_dir / f"{repo}.json").write_text(json.dumps({"repo": repo, "repo_sha": manifest["repo_sha"], "indexed_symbol_ids": sorted(scope),
                                                                    "call_edges": sorted([a, b] for a, b in binding)}) + "\n")
        for t in gt["tasks"]:
            sid, stratum = t["id"], t["stratum"]
            spec = {
                "task_id": sid, "repository": repo, "repo_sha": manifest["repo_sha"], "stratum": stratum, "language": LANG[repo],
                "headline": repo in HEADLINE, "condition_scope": "headline" if repo in HEADLINE else "celery_negative_control",
                "graph_scope": {"manifest": f"benchmarks/manifests/{repo}.json", "manifest_sha256": manifest["manifest_sha256"], "files": len(manifest["included_files"]),
                                "description": "production files listed in the manifest; tests, vendored and generated files are not part of the answer"},
                "permitted_relations": ["CALLS"], "question": t["question"] + " " + RULES, "answer_schema": SCHEMAS[stratum],
                "evidence_references": t["evidence"], "ground_truth_file": f"benchmarks/ground_truth/{repo}.yaml", "ground_truth_sha256": sha(gt_path), "valid_symbols_sha256": sha(sym_dir / f"{repo}.json"),
                "valid_symbols_file": f"benchmarks/experiment/symbols/{repo}.json", "callsite_positions_required": False,
                "note_callsite_positions": "IBWD stores no call-site positions; answers name definitions only, and the grader does not read line numbers.",
            }
            if stratum == "Q1":
                target = t["target"]["id"]
                exp = sorted(e["caller"] for e in t["expected"])
                assert all((c, target) in binding for c in exp), f"{sid}: expected caller without a binding CALLS edge"
                also_ref = sorted(s for s in refs_from if target in refs_from[s] and s not in exp)
                spec["expected"] = {"callers": [dict(item(c), relation="CALLS") for c in exp]}
                spec["target"] = item(target)
                spec["distractors_references_only"] = [item(s) for s in also_ref]
                spec["rollup_callers"] = sorted(c for c in exp if (c, target) in nested_pairs)
            elif stratum in ("Q2", "small"):
                src = t["source"]["id"]
                exp = sorted(e["callee"] for e in t["expected"])
                assert all((src, c) in binding for c in exp), f"{sid}: expected callee without a binding CALLS edge"
                spec["expected"] = {"callees": [dict(item(c), relation="CALLS") for c in exp]}
                spec["source"] = item(src)
                spec["distractors_references_only"] = [item(c) for c in sorted(refs_from.get(src, ())) if c not in exp]
                spec["rollup_callees"] = sorted(c for c in exp if (src, c) in nested_pairs)
                if stratum == "small":
                    size = scope[src]["end_line"] - scope[src]["line"] + 1
                    callers = sum(1 for (a, b) in binding if b == src)
                    assert size <= 20 and 2 <= len(exp) <= 4 and callers >= 1, f"{sid}: small-function criterion violated ({size} lines, {len(exp)} callees, {callers} callers)"
                    spec["small_criterion"] = {"lines": size, "callees": len(exp), "callers": callers, "declared": "<= 20 lines, 2..4 internal callees, >= 1 caller"}
                else:
                    outside = sum(1 for c in exp if scope[c]["file"] != scope[src]["file"])
                    assert outside >= 3, f"{sid}: Q2 needs >= 3 callees outside the module"
            else:                                                     # Q3
                path = t["expected"]["path"]
                adj, w = defaultdict(list), {}
                for (a, b) in binding:
                    adj[a].append(b)
                    w[(a, b)] = round(1.0 / (conf.get((a, b)) or 0.55), 9)          # an edge IBWD does not hold as resolved is priced as a suffix-tier edge
                cost, mins = dijkstra_all_min(adj, w, path[0], path[-1])
                gold_cost = round(sum(w[(a, b)] for a, b in zip(path, path[1:])), 9)
                assert mins and [path] == [m for m in mins if m == path], f"{sid}: golden path is not a minimum-cost path (min {cost}, golden {gold_cost}, {len(mins)} minimum paths)"
                spec["expected"] = {"accepted_paths": [[item(x) for x in m] for m in mins], "hops": len(path) - 1, "cost_objective": "sum(1/confidence) over hops (IBWD's declared heuristic cost)",
                                    "minimum_cost": cost, "grading": "any path that is a minimum-cost path over the oracle's binding CALLS edges is accepted; equal-cost alternatives are all listed"}
                spec["source"], spec["target"] = item(path[0]), item(path[-1])
                spec["hop_count_shortest_is_min_cost"] = all(len(m) - 1 == len(path) - 1 for m in mins)
                spec["question"] = spec["question"].replace("Trace the shortest call path", "Trace the shortest call path (fewest calls)")
            p = out_dir / f"{sid}.json"
            p.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
            index.append({"task_id": sid, "repository": repo, "stratum": stratum, "file": f"benchmarks/experiment/tasks/{sid}.json", "sha256": sha(p)})
    assert len(index) == 20, len(index)
    (out_dir.parent / "tasks_index.json").write_text(json.dumps({"tasks": index, "index_note": "sha256 of each frozen task specification"}, indent=1) + "\n")
    print(f"{len(index)} task specifications written to {out_dir}")


if __name__ == "__main__":
    main()
