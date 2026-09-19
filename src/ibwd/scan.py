"""Orchestrates a repo scan: filesystem walk -> graph sync -> manifest update."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ibwd.graph.database import connect, get_node_by_path
from ibwd.graph.manifest import DEFAULT_MANIFEST_PATH, load_manifest, save_manifest
from ibwd.graph.queries import ScanSummary, sync_files, sync_symbols
from ibwd.graph.resolution import EDGE_BUILD_VERSION, REFERENCE_RELATIONS, rebuild_reference_edges
from ibwd.scanner.filesystem import scan_files
from ibwd.scanner.symbols import EXTENSION_DIALECTS, extract_symbols


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
        source_changed = False
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
            source_changed = True
        conn.commit()

        # IMPORTS/CALLS/INHERITS resolution is repo-wide (an edit in one file can
        # newly resolve a call in another), so any source change — or a graph
        # built by an older extraction version — rebuilds all three relations.
        removed_source = any(
            Path(path).suffix.lower() in EXTENSION_DIALECTS
            for path in set(previous_manifest) - {f.path for f in scanned}
        )
        stale_edges = conn.execute("PRAGMA user_version").fetchone()[0] < EDGE_BUILD_VERSION
        if source_changed or removed_source or stale_edges:
            rebuild_reference_edges(conn, repo_root)

        placeholders = ",".join("?" * len(REFERENCE_RELATIONS))
        edge_counts = dict(
            conn.execute(
                f"SELECT relation, COUNT(*) FROM edges WHERE relation IN ({placeholders}) GROUP BY relation",
                REFERENCE_RELATIONS,
            ).fetchall()
        )
    finally:
        conn.close()

    new_manifest = {f.path: f.content_hash for f in scanned}
    save_manifest(new_manifest, manifest_path)

    return {
        **asdict(summary),
        "total_files": summary.total,
        "edges": {relation: edge_counts.get(relation, 0) for relation in REFERENCE_RELATIONS},
    }
