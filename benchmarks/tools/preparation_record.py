#!/usr/bin/env python3
"""Write the preparation record: what exactly the reported build-27 development-validation results were computed from.

Usage: preparation_record.py EVIDENCE_DIR OUT.json
Everything is read from files and git, nothing is typed in. The record separates evidence that EXISTS from work still PENDING.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
REPOS = ["sphinx", "scrapy", "celery", "redux-toolkit", "bulletproof-react"]

evidence = Path(sys.argv[1])
summary = json.loads((ROOT / "benchmarks/sprint_3_semantic_comparison.json").read_text())
src = ROOT / "src/ibwd/graph/resolution.py"
build = next(int(l.split("=")[1]) for l in src.read_text().splitlines() if l.startswith("EDGE_BUILD_VERSION ="))
rec = {
    "status": "Free graph validation reported complete; paid harness validation pending.",
    "result_version": "build-27 development validation (recorded, not to be overwritten; any later graph-behavior change creates a new result version)",
    "ibwd_commit": git("rev-parse", "HEAD"), "edge_build_version": build,
    "working_tree_clean": git("status", "--porcelain") == "", "working_tree_status": git("status", "--porcelain") or "clean",
    "gate_definition": {"file": "benchmarks/SPRINT3_gate_definition.md", "sha256": sha(ROOT / "benchmarks/SPRINT3_gate_definition.md")},
    "semantic_summary": {"file": "benchmarks/sprint_3_semantic_comparison.json", "sha256": sha(ROOT / "benchmarks/sprint_3_semantic_comparison.json")},
    "adjudications": {"file": "benchmarks/adjudications/sprint_3_adjudications_v2.json", "sha256": sha(ROOT / "benchmarks/adjudications/sprint_3_adjudications_v2.json")},
    "repositories": {},
    "existing_evidence": ["oracle and IBWD exports (build 27)", "relation-level comparison and adjudications", "20 qualified golden tasks and the golden check",
                          "no-static-use / hard-case diagnostics", "runtime traces of three Python suites (accounting to be reconciled, step 2)",
                          "readiness timing medians and incremental-vs-fresh tests"],
    "pending": ["runtime accounting reconciliation", "scoped wording for empty graph results", "task/grader semantic consistency check (20 tasks)",
                "deterministic grader", "A/B runner with mock mode", "isolation checks and summarizer", "experiment freeze and preflight",
                "4 paid pilot sessions (needs spending authorisation)", "120-session experiment and verdict"],
}
for r in REPOS:
    p = summary["repos"][r]["provenance"]
    m = ROOT / "benchmarks/manifests" / f"{r}.json"
    g = ROOT / "benchmarks/ground_truth" / f"{r}.yaml"
    mj = json.loads(m.read_text())
    rec["repositories"][r] = {
        "repo_sha": p["repo_sha"], "manifest_sha256_file": sha(m), "manifest_sha256_field": mj["manifest_sha256"],
        "ibwd_export_sha256": sha(evidence / f"{r}_ibwd.json"), "ibwd_export_commit": p["full_ibwd_commit"], "ibwd_export_edge_build": p["edge_build_version"],
        "oracle": p["oracle_version"], "oracle_export_sha256": sha(evidence / f"{r}_oracle_v2.json"),
        "ground_truth_yaml_sha256": sha(g), "ground_truth_recorded_in_manifest": mj["ground_truth"]["sha256"],
    }
    assert rec["repositories"][r]["ground_truth_yaml_sha256"] == mj["ground_truth"]["sha256"]
Path(sys.argv[2]).write_text(json.dumps(rec, indent=1) + "\n")
print(json.dumps({k: v for k, v in rec.items() if k in ("status", "ibwd_commit", "edge_build_version", "working_tree_clean")}, indent=1))
