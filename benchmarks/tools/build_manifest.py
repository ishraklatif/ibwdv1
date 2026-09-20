#!/usr/bin/env python3
"""Build benchmarks/manifests/<repo>.json — the explicit benchmark file manifest — from a repository audit.

Usage: build_manifest.py CORPUS_DIR EVIDENCE_DIR OUT_DIR

The manifest is the single source of truth for benchmark scope: IBWD exports, oracle graphs and expected answers are all
filtered by it (apply_manifest.py). Every exclusion is a record {file, reason, evidence, category}; a file is never excluded
merely for having long lines, and hand-written files are never excluded on heuristics alone.
"""
import hashlib, json, re, sys
from pathlib import Path

corpus, evidence, out_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out_dir.mkdir(parents=True, exist_ok=True)

def sha256_bytes(b): return hashlib.sha256(b).hexdigest()

def avg_line(path):
    data = path.read_bytes()
    return round(len(data) / (data.count(b"\n") + 1))

def first_line(path, n=100):
    return path.read_text(errors="ignore").splitlines()[0][:n] if path.stat().st_size else ""

CONFIG = {
    "scrapy": {"ext": (".py",), "roots_note": "scrapy/ package, Python files"},
    "celery": {"ext": (".py",), "roots_note": "celery/ package, Python files (negative control)"},
    "sphinx": {"ext": (".py",), "roots_note": "sphinx/ package, Python files only (243); JavaScript kept in a separate classification diagnostic"},
    "redux-toolkit": {"ext": (".ts", ".tsx"), "roots_note": "four package src roots, TypeScript/TSX"},
    "bulletproof-react": {"ext": (".ts", ".tsx"), "roots_note": "apps/react-vite/src only; the two other app variants are not independent repositories"},
}

summary = {}
for name, cfg in CONFIG.items():
    audit_path = evidence / f"{name}_audit.json"
    audit = json.loads(audit_path.read_text())
    repo = corpus / name
    included, excluded = [], []
    classification = []  # Sphinx JavaScript diagnostic
    for f in sorted(audit["production_files"]):
        p = repo / f
        if f.endswith(cfg["ext"]):
            if name == "bulletproof-react" and not f.startswith("apps/react-vite/"):
                excluded.append({"file": f, "reason": "duplicate app variant, not an independent repository",
                                 "evidence": "the repository ships three near-identical apps (react-vite, nextjs-app, nextjs-pages); only react-vite/src is benchmark scope; the variants are used only for alias-stress diagnostics",
                                 "category": "duplicate_variant"})
            else:
                included.append(f)
            continue
        # not in the language scope: only Sphinx has such files inside its production roots
        if name == "sphinx" and f.endswith(".js"):
            al = avg_line(p)
            if "/non-minified-js/" in f:
                cat, reason, ev = "generated_asset", "generated stemmer source (Snowball output)", f"source header: '{first_line(p)}'; produced by utils/generate_snowball.py"
                cls = "generated_stemmer_source"
            elif "/minified-js/" in f:
                cat, reason, ev = "generated_asset", "generated minified stemmer", f"produced from non-minified-js by utils/generate_snowball.py regenerate_javascript(); measured average line length {al} chars"
                cls = "generated_stemmer_minified"
            elif re.match(r"sphinx/locale/[^/]+/LC_MESSAGES/sphinx\.js$", f):
                cat, reason, ev = "generated_asset", "generated translation catalog", "documented provenance: utils/babel_runner.py (line ~223) writes locale/*/LC_MESSAGES/sphinx.js from the .po catalogue; the file has no generated-file header"
                cls = "generated_translation_catalog"
            elif f.endswith("css3-mediaqueries.js"):
                cat, reason, ev = "vendor_asset", "vendored third-party library, minified", f"sibling css3-mediaqueries_src.js header names third-party author Wouter van der Graaf; measured average line length {al} chars"
                cls = "vendored_minified"
            elif f.endswith("css3-mediaqueries_src.js"):
                cat, reason, ev = "vendor_asset", "vendored third-party library, readable source", "header: 'css3-mediaqueries.js - CSS Helper and CSS3 Media Queries Enabler; author: Wouter van der Graaf'"
                cls = "vendored_source"
            else:
                cat, reason, ev = "out_of_language_scope", "hand-written JavaScript outside the Python-only benchmark scope", f"authored in this repository (first line: '{first_line(p)}'); not excluded for length or heuristics"
                cls = "handwritten"
            excluded.append({"file": f, "reason": reason, "evidence": ev, "category": cat})
            classification.append({"file": f, "classification": cls, "avg_line_length": al, "benchmark_decision": "excluded", "reason": reason, "evidence": ev})
        else:
            raise SystemExit(f"unclassified production file outside language scope: {name}: {f}")
    body = {"repo": name, "repo_sha": audit["repo_sha"], "scope_rule": cfg["roots_note"], "source_audit": audit_path.name,
            "source_audit_sha256": sha256_bytes(audit_path.read_bytes()), "included_files": included, "excluded_files": excluded}
    body["manifest_sha256"] = sha256_bytes(json.dumps({k: v for k, v in body.items() if k != "manifest_sha256"}, sort_keys=True).encode())
    (out_dir / f"{name}.json").write_text(json.dumps(body, indent=1) + "\n")
    summary[name] = (len(included), len(excluded), body["manifest_sha256"][:12])
    if classification:
        counts = {}
        for c in classification: counts[c["classification"]] = counts.get(c["classification"], 0) + 1
        diag = {"repo": name, "repo_sha": audit["repo_sha"], "purpose": "classification diagnostic for the JavaScript files inside Sphinx's audit production roots",
                "ibwd_excluded_as_generated": sorted(json.loads((evidence / "sphinx_reconciliation.json").read_text())["excluded_by_ibwd"]),
                "counts": counts, "files": classification}
        (out_dir / "sphinx_js_classification.json").write_text(json.dumps(diag, indent=1) + "\n")
        summary["sphinx_js_counts"] = counts
for k, v in summary.items(): print(k, v)
