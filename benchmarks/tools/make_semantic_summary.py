#!/usr/bin/env python3
"""Build the Sprint 3 semantic-comparison summary from the exact input files, with full provenance, and verify it.

Usage:
  make_semantic_summary.py build  EVIDENCE_DIR OUT.json REPO [REPO ...]
  make_semantic_summary.py verify EVIDENCE_DIR SUMMARY.json

Per repository the summary records: full_ibwd_commit, dirty, edge_build_version, repo_sha, manifest_hash, oracle_version,
adapter (path + last commit touching it + whether it has uncommitted changes), ibwd_export_hash, oracle_export_hash and the
hash of the relation report; then TP/FP/FN counts (not percentages alone), supported-scope TP/FN, adjudication counts and the
declared gate thresholds (benchmarks/SPRINT3_gate_definition.md section 4). `verify` re-hashes every input file, re-reads the
export's own commit fields and fails loudly when anything in the summary no longer matches.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPTERS = {"sphinx": "benchmarks/tools/scip_python_to_graph.py", "scrapy": "benchmarks/tools/scip_python_to_graph.py",
            "celery": "benchmarks/tools/scip_python_to_graph.py", "redux-toolkit": "benchmarks/tools/ts_oracle.js",
            "bulletproof-react": "benchmarks/tools/ts_oracle.js"}
ADJUDICATIONS = "benchmarks/adjudications/sprint_3_adjudications_v2.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def thresholds(rel: dict) -> dict:
    """Declared population thresholds (gate definition section 4) on the CALLS relation, raw and adjudicated."""
    calls = rel["CALLS"]["resolved"]
    out = {"overall_precision_min": 0.95, "import_map_min": 0.99, "same_module_min": 0.99, "inherited_min": 0.99, "supported_recall_min": 0.90}
    tiers = calls["by_tier"]

    def adj(tier: str) -> float | None:
        """Tier precision after adjudication: oracle_error records count as correct IBWD edges, out-of-scope ones leave the denominator."""
        t = tiers.get(tier)
        if not t:
            return None
        fixed = calls.get("_tier_adjudicated", {}).get(tier, {})
        den = t["ibwd_edges"] - fixed.get("out_of_scope", 0)
        return round((t["TP"] + fixed.get("oracle_error", 0)) / den, 4) if den else None

    result = {"declared": out, "raw": {}, "adjudicated": {}}
    result["raw"] = {"overall_precision": calls["raw_precision"], "import_map": (tiers.get("import_map") or {}).get("raw_precision"),
                     "same_module": (tiers.get("same_module") or {}).get("raw_precision"), "inherited": (tiers.get("inherited") or {}).get("raw_precision"),
                     "supported_recall": calls["supported_scope_recall"]}
    result["adjudicated"] = {"overall_precision": calls["adjudicated_precision"], "import_map": adj("import_map"), "same_module": adj("same_module"),
                             "inherited": adj("inherited"), "supported_recall": calls["supported_scope_recall"]}
    passes = {}
    for basis in ("raw", "adjudicated"):
        r = result[basis]
        checks = {"overall_precision": r["overall_precision"] >= out["overall_precision_min"],
                  "supported_recall": r["supported_recall"] >= out["supported_recall_min"]}
        for k in ("import_map", "same_module", "inherited"):
            checks[k] = r[k] is None or r[k] >= out[f"{k}_min"]
        passes[basis] = checks
    result["passes"] = passes
    return result


def build(evidence: Path, out: Path, repos: list[str]) -> None:
    summary = {"purpose": "Sprint 3 semantic comparison: IBWD export vs oracle graph, per relation, resolved edges and candidate hints separately.",
               "gate_definition": "benchmarks/SPRINT3_gate_definition.md (v2)", "adjudications_file": ADJUDICATIONS,
               "adjudications_sha256": sha256(ROOT / ADJUDICATIONS), "repos": {}}
    for repo in repos:
        rel_path = evidence / f"{repo}_relations.json"
        report = json.loads(rel_path.read_text())
        ibwd_path, oracle_path = evidence / f"{repo}_ibwd.json", evidence / f"{repo}_oracle_v2.json"
        ibwd, oracle = json.loads(ibwd_path.read_text()), json.loads(oracle_path.read_text())
        manifest_path = ROOT / "benchmarks" / "manifests" / f"{repo}.json"
        adapter = ADAPTERS[repo]
        adapter_dirty = bool(git("status", "--porcelain", "--", adapter))
        # per-tier adjudication counts, so tier precision can be reported after adjudication
        adj = json.loads((ROOT / ADJUDICATIONS).read_text())["records"]
        tier_adj: dict[str, dict[str, int]] = {}
        for rec in adj:
            if rec["repo"] == repo and rec["relation"] == "CALLS" and rec["kind"] == "FP_resolved":
                t = tier_adj.setdefault(rec["tier"], {"oracle_error": 0, "out_of_scope": 0, "unresolved": 0})
                key = {"oracle_error": "oracle_error", "legitimate_out_of_scope": "out_of_scope"}.get(rec["verdict"], "unresolved")
                t[key] += 1
        report["relations"]["CALLS"]["resolved"]["_tier_adjudicated"] = tier_adj
        counts = {v: sum(1 for r in adj if r["repo"] == repo and r["verdict"] == v) for v in
                  ("ibwd_defect", "oracle_error", "legitimate_out_of_scope", "unresolved_disagreement")}
        summary["repos"][repo] = {
            "provenance": {
                "full_ibwd_commit": ibwd["ibwd_commit"], "dirty": ibwd["ibwd_dirty"], "edge_build_version": ibwd["edge_build_version"],
                "repo_sha": report["repo_sha"], "manifest_hash": sha256(manifest_path), "manifest_sha256_field": report["manifest_sha256"],
                "oracle_version": oracle.get("oracle"), "oracle_text_encoding": oracle.get("text_encoding"),
                "adapter": adapter, "adapter_commit": git("log", "-1", "--format=%H", "--", adapter), "adapter_dirty": adapter_dirty,
                "ibwd_export_hash": sha256(ibwd_path), "oracle_export_hash": sha256(oracle_path), "relation_report_hash": sha256(rel_path),
                "scope": ibwd.get("scope"),
            },
            "adjudication_counts": counts,
            "relations": {rel: {"resolved": {k: v for k, v in block["resolved"].items() if not k.startswith("_")},
                                "candidates": block["candidate"], "oracle_edges": block["oracle_edges"],
                                "oracle_supported_scope": block["oracle_supported_scope"],
                                "oracle_type_declared_or_possible": block["oracle_declared_type_declared"]}
                          for rel, block in report["relations"].items()},
            "thresholds": thresholds(report["relations"]),
            "disagreement_counts": {"resolved_false_positives": sum(1 for d in report["disagreements"] if d["kind"] == "FP_resolved"),
                                    "supported_scope_false_negatives": sum(1 for d in report["disagreements"] if d["kind"] == "FN" and d.get("in_supported_scope"))},
        }
        # the summary itself must match its inputs
        problems = check_repo(summary["repos"][repo], evidence, repo)
        if problems:
            raise SystemExit("summary does not match its inputs: " + "; ".join(problems))
    out.write_text(json.dumps(summary, indent=1) + "\n")
    print(f"wrote {out} for {', '.join(repos)}")


def check_repo(entry: dict, evidence: Path, repo: str) -> list[str]:
    p, problems = entry["provenance"], []
    ibwd, oracle = json.loads((evidence / f"{repo}_ibwd.json").read_text()), json.loads((evidence / f"{repo}_oracle_v2.json").read_text())
    if sha256(evidence / f"{repo}_ibwd.json") != p["ibwd_export_hash"]:
        problems.append(f"{repo}: ibwd export hash differs")
    if sha256(evidence / f"{repo}_oracle_v2.json") != p["oracle_export_hash"]:
        problems.append(f"{repo}: oracle export hash differs")
    if sha256(evidence / f"{repo}_relations.json") != p["relation_report_hash"]:
        problems.append(f"{repo}: relation report hash differs")
    if sha256(ROOT / "benchmarks" / "manifests" / f"{repo}.json") != p["manifest_hash"]:
        problems.append(f"{repo}: manifest hash differs")
    report = json.loads((evidence / f"{repo}_relations.json").read_text())
    if report["input_sha256"]["ibwd_export"] != p["ibwd_export_hash"] or report["input_sha256"]["oracle_export"] != p["oracle_export_hash"]:
        problems.append(f"{repo}: the relation report was computed from different export files")
    if ibwd["ibwd_commit"] != p["full_ibwd_commit"] or ibwd["edge_build_version"] != p["edge_build_version"]:
        problems.append(f"{repo}: export commit/build version differs")
    if ibwd["repo_sha"] != p["repo_sha"] or oracle["repo_sha"] != p["repo_sha"]:
        problems.append(f"{repo}: repo_sha differs between export, oracle and summary")
    if oracle["manifest_sha256"] != json.loads((ROOT / "benchmarks" / "manifests" / f"{repo}.json").read_text())["manifest_sha256"]:
        problems.append(f"{repo}: oracle was built with a different manifest")
    if p["dirty"]:
        problems.append(f"{repo}: the IBWD export was made from a dirty tree")
    if p["adapter_dirty"]:
        problems.append(f"{repo}: the adapter has uncommitted changes")
    return problems


def verify(evidence: Path, summary_path: Path) -> int:
    summary, problems = json.loads(summary_path.read_text()), []
    if sha256(ROOT / ADJUDICATIONS) != summary["adjudications_sha256"]:
        problems.append("adjudications file differs")
    head = git("rev-parse", "HEAD")
    for repo, entry in summary["repos"].items():
        problems += check_repo(entry, evidence, repo)
        if entry["provenance"]["full_ibwd_commit"] != head and git("diff", "--name-only", entry["provenance"]["full_ibwd_commit"], "HEAD", "--", "src/ibwd/graph", "src/ibwd/scanner", "src/ibwd/scan.py", "src/ibwd/export.py", "src/ibwd/retrieval") != "":
            problems.append(f"{repo}: graph-affecting src changed since the IBWD export commit {entry['provenance']['full_ibwd_commit'][:8]}")
    print("OK: summary matches its inputs" if not problems else "MISMATCH:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "build":
        build(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:])
    elif len(sys.argv) == 4 and sys.argv[1] == "verify":
        raise SystemExit(verify(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        raise SystemExit(__doc__)
