#!/usr/bin/env python3
"""Record the SHA-256 of benchmarks/ground_truth/<repo>.yaml in benchmarks/manifests/<repo>.json (must match at run time)."""
import hashlib
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
for repo in sys.argv[1:]:
    gt = root / "benchmarks" / "ground_truth" / f"{repo}.yaml"
    mp = root / "benchmarks" / "manifests" / f"{repo}.json"
    m = json.loads(mp.read_text())
    m["ground_truth"] = {"file": f"benchmarks/ground_truth/{repo}.yaml", "sha256": hashlib.sha256(gt.read_bytes()).hexdigest()}
    mp.write_text(json.dumps(m, indent=1) + "\n")
    print(repo, m["ground_truth"]["sha256"][:16])
