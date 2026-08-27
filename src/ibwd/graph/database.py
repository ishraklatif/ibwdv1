"""SQLite graph store: schema loading + node/edge upsert helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

DEFAULT_DB_PATH = Path(".ibwd") / "graph.db"


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    schema_sql = resources.files("ibwd.graph").joinpath("schema.sql").read_text()
    conn.executescript(schema_sql)
    conn.commit()


@dataclass
class Node:
    node_type: str
    name: str
    file_path: str | None = None
    qualified_name: str | None = None
    kind: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    content_hash: str | None = None
    id: int | None = field(default=None)


def upsert_node(conn: sqlite3.Connection, node: Node) -> int:
    """Insert or update a node keyed on (node_type, file_path). Returns node id."""
    cur = conn.execute(
        """
        INSERT INTO nodes (node_type, name, qualified_name, file_path, kind,
                            start_line, end_line, content_hash, updated_at)
        VALUES (:node_type, :name, :qualified_name, :file_path, :kind,
                :start_line, :end_line, :content_hash, datetime('now'))
        ON CONFLICT (node_type, file_path) DO UPDATE SET
            name = excluded.name,
            qualified_name = excluded.qualified_name,
            kind = excluded.kind,
            start_line = excluded.start_line,
            end_line = excluded.end_line,
            content_hash = excluded.content_hash,
            updated_at = datetime('now')
        RETURNING id
        """,
        {
            "node_type": node.node_type,
            "name": node.name,
            "qualified_name": node.qualified_name,
            "file_path": node.file_path,
            "kind": node.kind,
            "start_line": node.start_line,
            "end_line": node.end_line,
            "content_hash": node.content_hash,
        },
    )
    return cur.fetchone()[0]


def upsert_edge(
    conn: sqlite3.Connection,
    source_id: int,
    target_id: int,
    relation: str,
    confidence: float = 1.0,
    source_type: str = "static_analysis",
) -> None:
    conn.execute(
        """
        INSERT INTO edges (source_id, target_id, relation, confidence, source_type)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (source_id, target_id, relation) DO UPDATE SET
            confidence = excluded.confidence,
            source_type = excluded.source_type
        """,
        (source_id, target_id, relation, confidence, source_type),
    )


def get_node_by_path(conn: sqlite3.Connection, node_type: str, file_path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM nodes WHERE node_type = ? AND file_path = ?",
        (node_type, file_path),
    ).fetchone()


def delete_node_by_path(conn: sqlite3.Connection, file_path: str) -> None:
    conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
