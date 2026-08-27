"""IBWD MCP server — exposes the codebase graph as typed tools for Claude Code."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from ibwd.graph.database import connect
from ibwd.graph.queries import find_files, find_symbol, list_symbols
from ibwd.scan import run_scan

mcp = MCPServer("ibwd")


@mcp.tool()
def ibwd_scan() -> dict:
    """Rescan the repository (incremental) and update the codebase graph.

    Run this if results from other ibwd tools look stale relative to recent
    edits. Returns counts of added/changed/removed/unchanged files.
    """
    return run_scan()


@mcp.tool()
def ibwd_find_files(kind: str | None = None, name_pattern: str | None = None) -> list[dict]:
    """List files in the codebase graph, optionally filtered.

    Args:
        kind: restrict to one of "source", "test", "doc", "config", "other".
        name_pattern: substring to match against the file path.

    Returns a compact list of {path, kind} — prefer this over repeated Glob
    calls for file discovery/categorization questions. Results reflect the
    graph as of the last ibwd_scan; call ibwd_scan first if unsure.
    """
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        rows = find_files(conn, kind=kind, name_pattern=name_pattern)
    finally:
        conn.close()
    return [{"path": row["file_path"], "kind": row["kind"]} for row in rows]


@mcp.tool()
def ibwd_find_symbol(name: str) -> list[dict]:
    """Find where a class/function/method is defined by name.

    Tries an exact (case-sensitive) match first; if none exist, falls back to
    a case-insensitive substring match. Prefer this over Grep for "where is X
    defined" questions — it returns every definition (including same-named
    symbols in different files) with an exact file:line, no false positives
    from comments/strings/usages.

    Args:
        name: the symbol name to look up.

    Returns a list of {name, kind, file, line} — kind is one of
    "Class"/"Function"/"Method". Results reflect the graph as of the last
    ibwd_scan; call ibwd_scan first if unsure. Currently covers Python and
    JS/JSX/TS/TSX only.
    """
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        rows = find_symbol(conn, name)
    finally:
        conn.close()
    return [
        {"name": row["name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
        for row in rows
    ]


@mcp.tool()
def ibwd_list_symbols(file: str) -> list[dict]:
    """List every class/function/method defined in a file, in source order.

    Args:
        file: repo-relative path to the file (as returned by ibwd_find_files).

    Returns a list of {name, kind, file, line}. Results reflect the graph as
    of the last ibwd_scan; call ibwd_scan first if unsure.
    """
    conn = connect(Path.cwd() / ".ibwd" / "graph.db")
    try:
        rows = list_symbols(conn, file)
    finally:
        conn.close()
    return [
        {"name": row["name"], "kind": row["kind"], "file": row["file_path"], "line": row["start_line"]}
        for row in rows
    ]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
