"""Orchestrates a repo scan: filesystem walk -> graph sync -> manifest update."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import uuid

from ibwd.local_io import atomic_write, report_lock
from ibwd.index_inputs import config_digest
from ibwd.retrieval.lexical import rebuild as rebuild_lexical

from ibwd.graph.database import connect, get_node_by_path
from ibwd.graph.modules import is_ts_config
from ibwd.graph.manifest import save_manifest
from ibwd.graph.queries import SYMBOL_NODE_TYPES, ScanSummary, sync_files, sync_symbols
from ibwd.graph.resolution import EDGE_BUILD_VERSION, REFERENCE_RELATIONS, rebuild_reference_edges
from ibwd.scanner.filesystem import scan_files
from ibwd.scanner.symbols import EXTENSION_DIALECTS, extract_symbols


def run_scan(repo_root: Path | None = None) -> dict:
    root = (repo_root or Path.cwd()).resolve()
    with report_lock(root / '.ibwd/index.lock', timeout=30):
        return scan_locked(root)


def scan_locked(root: Path) -> dict:
    """Build privately; publish under the repository lock with a crash marker.

    Readers holding the same lock cannot observe a split publication. A killed
    publisher leaves the marker, so the next reader rebuilds before answering.
    """
    folder = root / '.ibwd'
    folder.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        before = scan_files(root)
        inputs = config_digest(root, before)
        with tempfile.TemporaryDirectory(prefix='scan-', dir=folder) as temporary:
            staged = Path(temporary) / 'graph.db'
            live = folder / 'graph.db'
            previous = {}
            if live.exists():
                try:
                    source = sqlite3.connect(live.as_uri() + '?mode=ro', uri=True)
                    try:
                        previous = dict(source.execute("SELECT file_path, content_hash FROM nodes WHERE node_type='File'"))
                        destination = sqlite3.connect(staged)
                        try:
                            source.backup(destination)
                        finally:
                            destination.close()
                    finally:
                        source.close()
                except sqlite3.Error:
                    if staged.exists():
                        staged.unlink()
                    previous = {}
            summary = _scan_into(root, staged, before, previous, inputs)
            if before != scan_files(root) or inputs != config_digest(root, before):
                if attempt == 0:
                    continue
                raise RuntimeError('Repository changed during indexing twice; retry when edits settle.')
            generation = uuid.uuid4().hex
            conn = sqlite3.connect(staged)
            try:
                with conn:
                    conn.execute('CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                    conn.execute("INSERT OR REPLACE INTO index_metadata VALUES ('generation', ?)", (generation,))
                    conn.execute("INSERT OR REPLACE INTO index_metadata VALUES ('config_digest', ?)", (inputs,))
            finally:
                conn.close()
            atomic_write(folder / 'publishing.json', json.dumps({'generation': generation}))
            os.replace(staged, live)
            save_manifest({f.path: f.content_hash for f in before}, folder / 'manifest.json')
            atomic_write(folder / 'generation', generation)
            (folder / 'publishing.json').unlink()
            return summary | {'index_generation': generation}
    raise RuntimeError('Index unavailable')


def _scan_into(repo_root, database_path, scanned, previous_manifest, inputs) -> dict:
    """Scan repo_root (default: cwd), sync the graph, update the manifest.

    Returns a JSON-serializable summary dict.
    """
    conn = connect(database_path)
    try:
        old_inputs = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='index_metadata'").fetchone():
            row = conn.execute("SELECT value FROM index_metadata WHERE key='config_digest'").fetchone()
            old_inputs = row[0] if row else None
        previous_kinds = {row[0]: row[1] for row in conn.execute("SELECT file_path, kind FROM nodes WHERE node_type = 'File'")}
        summary: ScanSummary = sync_files(conn, scanned, previous_manifest)

        # Only reparse source files whose content actually changed since the
        # last scan — symbol extraction is the relatively expensive step.
        source_changed = False
        for scanned_file in scanned:
            if scanned_file.kind != "source":
                continue
            if previous_manifest.get(scanned_file.path) == scanned_file.content_hash and previous_kinds.get(scanned_file.path) == "source":
                continue                  # unchanged, and it was already indexed as source (a reclassified file has no symbols yet)
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
        # A file reclassified as vendor/generated/... since the last scan keeps stale symbols; drop them.
        placeholders_sym = ",".join("?" * len(SYMBOL_NODE_TYPES))
        stale_symbols = conn.execute(
            f"DELETE FROM nodes WHERE node_type IN ({placeholders_sym}) AND file_path IN "
            "(SELECT file_path FROM nodes WHERE node_type = 'File' AND kind != 'source')",
            SYMBOL_NODE_TYPES,
        ).rowcount
        removed_source = any(
            Path(path).suffix.lower() in EXTENSION_DIALECTS
            for path in set(previous_manifest) - {f.path for f in scanned}
        )
        stale_edges = conn.execute("PRAGMA user_version").fetchone()[0] < EDGE_BUILD_VERSION
        # tsconfig/jsconfig `paths` aliases change how JS/TS imports resolve.
        config_changed = any(
            is_ts_config(path) and previous_manifest.get(path) != h
            for path, h in {f.path: f.content_hash for f in scanned}.items()
        ) or any(is_ts_config(path) for path in set(previous_manifest) - {f.path for f in scanned})
        if source_changed or removed_source or stale_edges or config_changed or stale_symbols or old_inputs != inputs:
            rebuild_reference_edges(conn, repo_root)

        rebuild_lexical(conn, repo_root, scanned)

        placeholders = ",".join("?" * len(REFERENCE_RELATIONS))
        edge_counts = dict(
            conn.execute(
                f"SELECT relation, COUNT(*) FROM edges WHERE relation IN ({placeholders}) GROUP BY relation",
                REFERENCE_RELATIONS,
            ).fetchall()
        )
    finally:
        conn.close()

    return {
        **asdict(summary),
        "total_files": summary.total,
        "edges": {relation: edge_counts.get(relation, 0) for relation in REFERENCE_RELATIONS},
    }
