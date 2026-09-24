"""Higher-level graph operations built on top of database.py primitives."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import PurePosixPath

from ibwd.graph.database import Node, upsert_edge, upsert_node, upsert_symbol_node
from ibwd.scanner.filesystem import ScannedFile
from ibwd.scanner.symbols import SymbolInfo

ROOT_DIR_PATH = "."
SYMBOL_NODE_TYPES = ("Class", "Function", "Method")


def _bounded_rows(cursor):
    rows = cursor.fetchmany(2001)
    if len(rows) > 2000:
        raise ValueError('Legacy result budget exceeded; use response_version=2 with pagination or narrow the query.')
    return rows


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
        delete_symbols_for_file(conn, removed_path)
    summary.removed = len(removed_paths)

    conn.commit()
    return summary


def delete_symbols_for_file(conn: sqlite3.Connection, file_path: str) -> None:
    """Delete all Class/Function/Method nodes for a file (their DEFINES edges
    cascade-delete via the edges table's ON DELETE CASCADE foreign keys)."""
    placeholders = ",".join("?" * len(SYMBOL_NODE_TYPES))
    conn.execute(
        f"DELETE FROM nodes WHERE file_path = ? AND node_type IN ({placeholders})",
        (file_path, *SYMBOL_NODE_TYPES),
    )


def sync_symbols(conn: sqlite3.Connection, file_id: int, file_path: str, symbols: list[SymbolInfo]) -> None:
    """Replace all symbol nodes + DEFINES edges for one file with a fresh set."""
    delete_symbols_for_file(conn, file_path)
    for symbol in symbols:
        symbol_id = upsert_symbol_node(
            conn,
            Node(
                node_type=symbol.kind,
                name=symbol.name,
                qualified_name=symbol.qualified_name,
                file_path=symbol.file_path,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
            ),
        )
        upsert_edge(conn, file_id, symbol_id, "DEFINES")


def find_symbol(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """Exact match (case-sensitive) first; if none, case-insensitive substring match."""
    placeholders = ",".join("?" * len(SYMBOL_NODE_TYPES))
    # An explicit identity must never fall back to a different symbol.
    if "::" in name:
        return _bounded_rows(conn.execute(
            f"SELECT id, name, qualified_name, node_type AS kind, file_path, start_line, end_line "
            f"FROM nodes WHERE node_type IN ({placeholders}) AND qualified_name = ?",
            (*SYMBOL_NODE_TYPES, name),
        ))
    exact = _bounded_rows(conn.execute(
        f"""
        SELECT id, name, qualified_name, node_type AS kind, file_path, start_line, end_line
        FROM nodes WHERE node_type IN ({placeholders}) AND name = ?
        ORDER BY file_path, start_line
        """,
        (*SYMBOL_NODE_TYPES, name),
    ))
    if exact:
        return exact

    return _bounded_rows(conn.execute(
        f"""
        SELECT id, name, qualified_name, node_type AS kind, file_path, start_line, end_line
        FROM nodes WHERE node_type IN ({placeholders}) AND name LIKE ? ESCAPE '\\'
        ORDER BY file_path, start_line
        """,
        (*SYMBOL_NODE_TYPES, f"%{_escape_like(name)}%"),
    ))


def resolve_targets(conn: sqlite3.Connection, name: str, file: str | None = None) -> list[sqlite3.Row]:
    """Resolve a user-supplied name to graph nodes: a File path, else symbol(s) by name.

    Returns rows with {id, name, kind, file_path, start_line}. An exact File
    path wins (so callers/dependents work on files too); otherwise this is
    find_symbol's exact-then-substring matching, optionally narrowed to one file.
    """
    file_row = conn.execute(
        "SELECT id, name, node_type AS kind, file_path, start_line FROM nodes "
        "WHERE node_type = 'File' AND file_path = ?",
        (name,),
    ).fetchone()
    if file_row is not None and file is None:
        return [file_row]

    rows = find_symbol(conn, name)
    if file is not None:
        rows = [row for row in rows if row["file_path"] == file]
    return rows


def list_symbols(conn: sqlite3.Connection, file_path: str) -> list[sqlite3.Row]:
    placeholders = ",".join("?" * len(SYMBOL_NODE_TYPES))
    return _bounded_rows(conn.execute(
        f"""
        SELECT name, qualified_name, node_type AS kind, file_path, start_line, end_line
        FROM nodes WHERE node_type IN ({placeholders}) AND file_path = ?
        ORDER BY start_line
        """,
        (*SYMBOL_NODE_TYPES, file_path),
    ))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    return _bounded_rows(conn.execute(query, params))
