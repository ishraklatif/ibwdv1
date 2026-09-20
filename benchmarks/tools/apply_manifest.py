#!/usr/bin/env python3
"""Filter an IBWD export, an oracle graph or a ground-truth file to a benchmark manifest's included files.

Usage: apply_manifest.py MANIFEST.json IN.json OUT.json

Symbols are kept when their file is included; edges are kept when BOTH endpoints are included symbols. The output records
the manifest hash and the input file's SHA-256, so a summary can prove which exact files it was computed from.
"""
import hashlib, json, sys
from pathlib import Path

manifest_path, in_path, out_path = (Path(a) for a in sys.argv[1:4])
manifest = json.loads(manifest_path.read_text()); included = set(manifest["included_files"])
raw = in_path.read_bytes(); data = json.loads(raw)
file_of = {s["id"]: s["file"] for s in data.get("symbols", [])}
symbols = [s for s in data.get("symbols", []) if s["file"] in included]
keep = {s["id"] for s in symbols}
edges = [e for e in data.get("edges", []) if e["source"] in keep and e["target"] in keep]
data.update({"symbols": symbols, "edges": edges, "manifest_sha256": manifest["manifest_sha256"], "manifest_repo_sha": manifest["repo_sha"],
             "filtered_from_sha256": hashlib.sha256(raw).hexdigest(), "scope": f"manifest {manifest['repo']} ({len(included)} files)"})
out_path.write_text(json.dumps(data, indent=1) + "\n")
print(f"{in_path.name}: {len(file_of)} -> {len(symbols)} symbols, edges -> {len(edges)} (manifest {manifest['manifest_sha256'][:12]})")
