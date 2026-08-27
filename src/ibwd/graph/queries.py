"""Higher-level graph operations built on top of database.py primitives."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath

from ibwd.graph.database import Node, upsert_edge, upsert_node
from ibwd.scanner.filesystem import ScannedFile

ROOT_DIR_PATH = "."


@dataclass
class ScanSummary:
    added: int = 0
    changed: int = 0
    removed: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.added + self.changed + self.unchanged


def _ensure_directory_chain(conn: sqlite3.Connection, dir_cache: dict[str, int], dir_path: str) -> int:
    """Upsert a Directory node (and its ancestors) and return its node id."""
    if dir_path in dir_cache:
        return dir_cache[dir_path]

    name = PurePosixPath(dir_path).name if dir_path != ROOT_DIR_PATH else "."
    dir_id = upsert_node(conn, Node(node_type="Directory", name=name, file_path=dir_path))
    dir_cache[dir_path] = dir_id

    if dir_path != ROOT_DIR_PATH:
        parent_path = PurePosixPath(dir_path).parent.as_posix()
        if parent_path == dir_path:
            parent_path = ROOT_DIR_PATH
        parent_id = _ensure_directory_chain(conn, dir_cache, parent_path)
        upsert_edge(conn, parent_id, dir_id, "CONTAINS")

    return dir_id


def sync_files(
    conn: sqlite3.Connection,
    scanned_files: list[ScannedFile],
    previous_manifest: dict[str, str],
) -> ScanSummary:
    """Reconcile scanned files against the graph + previous manifest. Returns change counts."""
    summary = ScanSummary()
    dir_cache: dict[str, int] = {}
    seen_paths = {f.path for f in scanned_files}

    for scanned in scanned_files:
        prev_hash = previous_manifest.get(scanned.path)
        if prev_hash == scanned.content_hash:
            summary.unchanged += 1
        elif prev_hash is None:
            summary.added += 1
        else:
            summary.changed += 1

        file_id = upsert_node(
            conn,
            Node(
                node_type="File",
                name=PurePosixPath(scanned.path).name,
                file_path=scanned.path,
                kind=scanned.kind,
                content_hash=scanned.content_hash,
            ),
        )
        parent_dir = PurePosixPath(scanned.path).parent.as_posix()
        dir_id = _ensure_directory_chain(conn, dir_cache, parent_dir)
        upsert_edge(conn, dir_id, file_id, "CONTAINS")

    removed_paths = set(previous_manifest) - seen_paths
    for removed_path in removed_paths:
        conn.execute("DELETE FROM nodes WHERE node_type = 'File' AND file_path = ?", (removed_path,))
    summary.removed = len(removed_paths)

    conn.commit()
    return summary


def find_files(
    conn: sqlite3.Connection,
    kind: str | None = None,
    name_pattern: str | None = None,
) -> list[sqlite3.Row]:
    query = "SELECT file_path, kind FROM nodes WHERE node_type = 'File'"
    params: list[str] = []
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    if name_pattern:
        query += " AND file_path LIKE ?"
        params.append(f"%{name_pattern}%")
    query += " ORDER BY file_path"
    return conn.execute(query, params).fetchall()
