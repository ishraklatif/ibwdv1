"""SQLite graph store: schema loading + node/edge upsert helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

DEFAULT_DB_PATH = Path(".ibwd") / "graph.db"


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Connect to the SQLite database, creating it and initializing schema if needed."""
    db_path.parent.mkdir(parents=True, exist_ok=True) # Ensure the parent directory .ibwd exists, creating it if necessary
    conn = sqlite3.connect(db_path) # Connect to the SQLite database at the specified path, creating it if it doesn't exist
    conn.row_factory = sqlite3.Row # Set the row factory to sqlite3.Row to allow accessing columns by name
    conn.execute("PRAGMA foreign_keys = ON") # Enable foreign key support in SQLite
    _init_schema(conn) # Initialize the database schema by executing the SQL script from the schema.sql file
    return conn # Return the connection object to the caller


def _init_schema(conn: sqlite3.Connection) -> None:
    """Initialize the database schema by executing the SQL script from the schema.sql file."""
    schema_sql = resources.files("ibwd.graph").joinpath("schema.sql").read_text() # Read the contents of the schema.sql file from the ibwd.graph package
    conn.executescript(schema_sql) # Execute the SQL script to create the necessary tables and indexes in the database
    conn.commit() # Commit the changes to the database to ensure that the schema is saved and available for use


@dataclass
class Node:
    """Represents a node in the graph database."""
    node_type: str # e.g., "File", "Directory", "Class", "Function", "Method"
    name: str # e.g., "filesystem.py", "Scanner", "scan_directory"
    file_path: str | None = None # e.g., "src/ibwd/scanner/filesystem.py" for a file, or None for a directory
    qualified_name: str | None = None # e.g., "src.ibwd.scanner.filesystem.Scanner.scan_directory" for a method, or None for a file/directory this is the fully qualified name of the node, which is unique across the entire codebase. For a file, it would be the module path (e.g., "src.ibwd.scanner.filesystem"). For a class or function, it would include the module path and the class/function name (e.g., "src.ibwd.scanner.filesystem.Scanner"). For a method, it would include the module path, class name, and method name (e.g., "src.ibwd.scanner.filesystem.Scanner.scan_directory").
    kind: str | None = None # e.g., "class", "function", "method"
    start_line: int | None = None # e.g., 42 for a method starting at line 42, or None for a file/directory
    end_line: int | None = None # e.g., 56 for a method ending at line 56, or None for a file/directory
    content_hash: str | None = None # e.g., "abcdef1234567890" for a file's content hash, or None for a directory
    id: int | None = field(default=None) # e.g., 1 for a node with ID 1, or None if the node has not been inserted into the database yet


def upsert_node(conn: sqlite3.Connection, node: Node) -> int:
    """Insert or update a node keyed on (node_type, file_path). Returns node id."""
    cur = conn.execute(
        """
        INSERT INTO nodes (node_type, name, qualified_name, file_path, kind,
                            start_line, end_line, content_hash, updated_at)
        VALUES (:node_type, :name, :qualified_name, :file_path, :kind,
                :start_line, :end_line, :content_hash, datetime('now'))
        ON CONFLICT (node_type, file_path) WHERE node_type IN ('File', 'Directory') DO UPDATE SET
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
    return cur.fetchone()[0] # Return the ID of the inserted or updated node, which can be used for further operations or references in the graph database


def upsert_symbol_node(conn: sqlite3.Connection, node: Node) -> int:
    """Insert or update a Class/Function/Method node keyed on qualified_name.

    Unlike upsert_node (keyed on node_type+file_path, one row per file), a
    file can define many symbols — qualified_name ("{file_path}::{Outer.Inner}")
    is what's unique here.
    """
    cur = conn.execute(
        """
        INSERT INTO nodes (node_type, name, qualified_name, file_path, kind,
                            start_line, end_line, content_hash, updated_at)
        VALUES (:node_type, :name, :qualified_name, :file_path, :kind,
                :start_line, :end_line, :content_hash, datetime('now'))
        ON CONFLICT (qualified_name) DO UPDATE SET
            node_type = excluded.node_type,
            name = excluded.name,
            file_path = excluded.file_path,
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
    """Insert or update an edge in the graph database, keyed on (source_id, target_id, relation)."""
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
    """Retrieve a node from the graph database by its type and file path."""
    return conn.execute(
        "SELECT * FROM nodes WHERE node_type = ? AND file_path = ?",
        (node_type, file_path),
    ).fetchone()


def delete_node_by_path(conn: sqlite3.Connection, file_path: str) -> None:
    """Delete a node from the graph database by its file path."""
    conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
