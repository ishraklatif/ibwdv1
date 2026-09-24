"""Read-only local index diagnostics. No model, network, or schema migration."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ibwd.graph.resolution import EDGE_BUILD_VERSION
from ibwd.scanner.filesystem import scan_files
from ibwd.index_inputs import config_digest


def inspect_index(root: Path) -> dict:
    root = root.resolve()
    db = root / ".ibwd" / "graph.db"
    manifest = root / ".ibwd" / "manifest.json"
    report = {"repository": str(root), "status": "not_indexed", "problems": [],
              "expected_edge_build": EDGE_BUILD_VERSION,
              "remedy": "Run ibwd scan --repo " + str(root)}
    if (root / '.ibwd/publishing.json').exists():
        report.update(status='stale', problems=['Interrupted index publication; retrieval must refresh.'])
        return report
    if not db.is_file() or not manifest.is_file():
        report["problems"].append("Both .ibwd/graph.db and .ibwd/manifest.json are required.")
        return report
    try:
        saved = json.loads(manifest.read_text())
        if not isinstance(saved, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in saved.items()):
            raise ValueError("manifest must map file paths to hashes")
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
        try:
            check = conn.execute("PRAGMA quick_check").fetchall()
            if check != [("ok",)]:
                raise ValueError(f"SQLite integrity check failed: {check}")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='index_metadata'").fetchone():
                row = conn.execute("SELECT value FROM index_metadata WHERE key='generation'").fetchone()
                report['index_generation'] = row[0] if row else None
                row = conn.execute("SELECT value FROM index_metadata WHERE key='config_digest'").fetchone()
                report['config_digest'] = row[0] if row else None
            indexed = {p: (h, k) for p, h, k in conn.execute(
                "SELECT file_path, content_hash, kind FROM nodes WHERE node_type = 'File'")}
            report["symbols"] = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_type IN ('Class', 'Function', 'Method')").fetchone()[0]
            report["edges"] = dict(conn.execute("SELECT relation, COUNT(*) FROM edges GROUP BY relation"))
        finally:
            conn.close()
        scanned = scan_files(root)
        current = {f.path: (f.content_hash, f.kind) for f in scanned}
        if report.get('config_digest') != config_digest(root, scanned):
            report['problems'].append('Resolution configuration changed.')
    except (OSError, ValueError, sqlite3.Error) as exc:
        report.update(status="invalid", problems=[str(exc)])
        return report
    report["edge_build"] = version
    changes = {"added": sorted(current.keys() - indexed.keys()),
               "removed": sorted(indexed.keys() - current.keys()),
               "changed": sorted(p for p in current.keys() & indexed.keys() if current[p] != indexed[p])}
    report["changes"] = changes
    report["files"] = len(indexed)
    if version != EDGE_BUILD_VERSION:
        report["problems"].append("Index build version differs from this installation.")
    generation_file = root / '.ibwd/generation'
    if not report.get('index_generation') or not generation_file.exists() or generation_file.read_text() != report['index_generation']:
        report['problems'].append('Missing or inconsistent published index generation.')
    if saved != {p: h for p, (h, _) in indexed.items()}:
        report["problems"].append("Manifest and database disagree; a scan may have been interrupted.")
    if any(changes.values()):
        report["problems"].append("Repository files differ from the index.")
    report["status"] = "stale" if report["problems"] else "ready"
    if report["status"] == "ready":
        report["remedy"] = None
    report["scope"] = "File inventory; symbols/edges cover production Python and JS/TS only. Freshness is not completeness."
    return report
