"""IBWD command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ibwd.scan import run_scan


@click.group()
def main() -> None:
    """IBWD — persistent codebase memory for AI coding agents."""


@main.command()
def scan() -> None:
    """Scan the repo (incrementally) and update the graph."""
    summary = run_scan()
    click.echo(
        f"Scanned {summary['total_files']} files "
        f"(added={summary['added']}, changed={summary['changed']}, "
        f"unchanged={summary['unchanged']}, removed={summary['removed']})"
    )


@main.command()
@click.argument("out", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--repo-sha", default=None, help="Commit SHA to record (default: git HEAD of the current directory).")
def export(out: Path, repo_sha: str | None) -> None:
    """Export the graph as canonical JSON (stable symbol ids) for oracle comparison."""
    from ibwd.export import detect_repo_sha, export_graph
    from ibwd.graph.database import connect

    root = Path.cwd()
    conn = connect(root / ".ibwd" / "graph.db")
    try:
        data = export_graph(conn, root.name, repo_sha or detect_repo_sha(root))
    finally:
        conn.close()
    out.write_text(json.dumps(data, indent=1) + "\n")
    click.echo(f"Exported {len(data['symbols'])} symbols and {len(data['edges'])} edges to {out}")


if __name__ == "__main__":
    main()
