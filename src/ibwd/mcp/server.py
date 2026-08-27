"""IBWD MCP server — exposes the codebase graph as typed tools for Claude Code."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from ibwd.graph.database import connect
from ibwd.graph.queries import find_files
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
