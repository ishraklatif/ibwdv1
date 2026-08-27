"""Orchestrates a repo scan: filesystem walk -> graph sync -> manifest update."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ibwd.graph.database import connect, get_node_by_path
from ibwd.graph.manifest import DEFAULT_MANIFEST_PATH, load_manifest, save_manifest
from ibwd.graph.queries import ScanSummary, sync_files, sync_symbols
from ibwd.scanner.filesystem import scan_files
from ibwd.scanner.symbols import extract_symbols


def run_scan(repo_root: Path | None = None) -> dict:
    """Scan repo_root (default: cwd), sync the graph, update the manifest.

    Returns a JSON-serializable summary dict.
    """
    repo_root = repo_root or Path.cwd()
    manifest_path = repo_root / DEFAULT_MANIFEST_PATH

    scanned = scan_files(repo_root)
    previous_manifest = load_manifest(manifest_path)

    conn = connect(repo_root / ".ibwd" / "graph.db")
    try:
        summary: ScanSummary = sync_files(conn, scanned, previous_manifest)

        # Only reparse source files whose content actually changed since the
        # last scan — symbol extraction is the relatively expensive step.
        for scanned_file in scanned:
            if scanned_file.kind != "source":
                continue
            if previous_manifest.get(scanned_file.path) == scanned_file.content_hash:
                continue
            file_row = get_node_by_path(conn, "File", scanned_file.path)
            if file_row is None:
                continue
            symbols = extract_symbols(repo_root / scanned_file.path, scanned_file.path)
            sync_symbols(conn, file_row["id"], scanned_file.path, symbols)
        conn.commit()
    finally:
        conn.close()

    new_manifest = {f.path: f.content_hash for f in scanned}
    save_manifest(new_manifest, manifest_path)

    return {**asdict(summary), "total_files": summary.total}
