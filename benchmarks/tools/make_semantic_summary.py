#!/usr/bin/env python3
"""Build benchmarks/sprint_3_semantic_comparison.json from the evidence directory, with provenance and adjudications.

Usage: make_semantic_summary.py EVIDENCE_DIR CORPUS_DIR OUT.json
Reads <repo>_ibwd.json (provenance), <repo>_vs_scip.json, <repo>_scip_oracle.json and
benchmarks/adjudications/sprint_3_adjudications.json. Reproduces every recall figure from machine-readable inputs.
"""
import datetime, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from summarize_comparison import summarize  # noqa: E402

evidence, corpus, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
adj = json.loads((Path(__file__).resolve().parents[1] / "adjudications" / "sprint_3_adjudications.json").read_text())
verdict = {(a["source"], a["target"]): a["verdict"] for a in adj["scip_python_pass_supported_category_misses"]}
result = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "purpose": "Full-repo semantic comparison of IBWD CALLS edges against scip-python (Pyright) — Python repositories, pair level",
    "oracle": "scip-python 0.6.6 + ast owner ranges (benchmarks/tools/scip_python_to_graph.py)",
    "granularity": "caller symbol -> target symbol per relation; IBWD stores no callsite positions",
    "status_of_data": "DEVELOPMENT data: these repositories were inspected and the resolver was changed in response. Not an untouched generalization test.",
    "denominator_policy": "broader = all oracle edges minus individually evidenced oracle errors; supported = broader minus the declared type/data-flow category and adjudicated unsupported cases. Nothing is removed for being hard. precision_raw is unadjudicated.",
    "adjudications_file": "benchmarks/adjudications/sprint_3_adjudications.json",
    "repos": {},
}
for name in ["sphinx", "scrapy", "celery"]:
    ib = json.loads((evidence / f"{name}_ibwd.json").read_text())
    stats = summarize(json.loads((evidence / f"{name}_vs_scip.json").read_text()), json.loads((evidence / f"{name}_scip_oracle.json").read_text()), corpus / name, verdict)
    result["repos"][name] = {
        "provenance": {k: ib.get(k) for k in ("repo_sha", "ibwd_commit", "ibwd_dirty", "edge_build_version", "scope")},
        "role": "negative_control" if name == "celery" else "headline_candidate",
        **stats,
    }
out.write_text(json.dumps(result, indent=1) + "\n")
for n, r in result["repos"].items():
    print(f"{n:8} ibwd {r['provenance']['ibwd_commit'][:8]} dirty={r['provenance']['ibwd_dirty']} | precision_raw {r['precision_raw']} | broader {r['tp']}/{r['broader_denominator']}={r['broader_recall']} | supported {r['tp']}/{r['supported_denominator']}={r['supported_recall']} | unadjudicated {r['unadjudicated_supported_misses']}")
