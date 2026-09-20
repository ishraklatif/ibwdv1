"""IBWD command-line interface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ibwd.scan import run_scan


@click.group()
def main() -> None:
    """IBWD — persistent codebase memory for AI coding agents."""


@main.command()
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
def scan(repo: Path) -> None:
    """Scan the repo (incrementally) and update the graph."""
    summary = run_scan(repo.resolve())
    click.echo(
        f"Scanned {summary['total_files']} files "
        f"(added={summary['added']}, changed={summary['changed']}, "
        f"unchanged={summary['unchanged']}, removed={summary['removed']})"
    )


@main.command()
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
def serve(repo: Path) -> None:
    """Serve this repository over MCP stdio (no model calls)."""
    from ibwd.mcp.server import main as serve_main

    serve_main(["--repo", str(repo.resolve())])


@main.command("client-config")
@click.option("--client", type=click.Choice(["codex", "claude"]), required=True)
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
def client_config(client: str, repo: Path) -> None:
    """Print configuration for an installed IBWD; never edit client settings."""
    # Do not resolve the Python symlink: doing so can escape its virtualenv.
    command = str(Path(sys.executable).absolute())
    args = ["-m", "ibwd.mcp.server", "--repo", str(repo.resolve())]
    if client == "claude":
        click.echo(json.dumps({"mcpServers": {"ibwd": {"command": command, "args": args}}}, indent=2))
    else:
        # JSON strings/arrays are also valid TOML for these string values.
        click.echo("[mcp_servers.ibwd]\ncommand = " + json.dumps(command, ensure_ascii=False)
                   + "\nargs = " + json.dumps(args, ensure_ascii=False))


@main.command()
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
def doctor(repo: Path) -> None:
    """Check index freshness locally without changing the graph or calling a model."""
    from ibwd.health import inspect_index

    report = inspect_index(repo.resolve())
    click.echo(json.dumps(report, indent=2))
    if report["status"] != "ready":
        raise click.exceptions.Exit(1)


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


@main.command("usage-report")
@click.argument("log", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--client", type=click.Choice(["codex", "claude"]), required=True)
@click.option("--condition", type=click.Choice(["enabled", "disabled", "unknown"]), default="unknown")
@click.option("--task-kind", type=click.Choice(["structural", "implementation", "debugging", "mixed", "unknown"]), default="unknown")
@click.option("--outcome", type=click.Choice(["passed", "failed", "unknown"]), default="unknown")
def usage_report(log: Path, client: str, condition: str, task_kind: str, outcome: str) -> None:
    """Summarize one existing JSONL transcript locally; never run a model."""
    from ibwd.usage import analyze_log

    try:
        report = analyze_log(log, client, condition, task_kind, outcome)
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, indent=2))


@main.command("usage-summary")
@click.argument("reports", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
def usage_summary(reports: tuple[Path, ...]) -> None:
    """Group saved reports by client/model/effort/version/task/condition."""
    from ibwd.usage import summarize_reports

    try:
        summary = summarize_reports(reports)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
