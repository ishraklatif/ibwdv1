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
@click.option("--setup", "check_setup", is_flag=True, help="Also check both clients' configuration, routing and reporting.")
def doctor(repo: Path, check_setup: bool) -> None:
    """Check index freshness locally without changing the graph or calling a model."""
    from ibwd.health import inspect_index

    if check_setup:
        from ibwd.setup import inspect_setup
        report = inspect_setup(repo.resolve())
    else:
        report = inspect_index(repo.resolve())
    click.echo(json.dumps(report, indent=2))
    if report["status"] != "ready":
        raise click.exceptions.Exit(1)


@main.command()
@click.option("--client", type=click.Choice(["codex", "claude", "both"]), default="both", show_default=True)
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option("--dry-run", is_flag=True, help="Validate and list proposed file changes without writing or scanning.")
def setup(client: str, repo: Path, dry_run: bool) -> None:
    """Set up MCP, routing, reports and a fresh index in one local operation."""
    from ibwd.setup import plan_setup, setup_project

    try:
        if dry_run:
            click.echo(json.dumps({"would_change": list(plan_setup(repo, client)), "would_scan": True}, indent=2))
            return
        result = setup_project(repo, client)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2))
    if result["readiness"]["status"] != "ready":
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


@main.command("usage-setup")
@click.option("--client", type=click.Choice(["codex", "claude", "both"]), default="both", show_default=True)
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
def usage_setup(client: str, repo: Path) -> None:
    """Enable automatic local reports in a project (one-time setup, no model calls)."""
    from ibwd.usage_hooks import install_hooks

    for selected in (("codex", "claude") if client == "both" else (client,)):
        try:
            path = install_hooks(selected, repo)
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"{selected}: configured {path}")
    click.echo(f"Reports: {repo.resolve() / '.ibwd' / 'usage' / 'sessions'}")
    click.echo("Restart/reconnect the client. In Codex, use /hooks to review and trust these hooks once.")
    click.echo("This enables reporting only; MCP connection and IBWD routing instructions are separate.")


@main.command("usage-hook")
@click.option("--client", type=click.Choice(["codex", "claude"]), required=True)
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
def usage_hook(client: str, repo: Path) -> None:
    """Internal hook: read client event from stdin and save a local report."""
    from ibwd.usage_hooks import capture_session

    try:
        capture_session(json.load(sys.stdin), client, repo)
    except (OSError, ValueError, TypeError) as exc:
        # Exit 1 is an advisory error. Never use exit 2 or Stop decision output,
        # which some clients interpret as asking the model to continue.
        raise click.ClickException(f"IBWD session report not saved: {exc}") from exc


@main.command('context')
@click.argument('task')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--target', 'targets', multiple=True)
@click.option('--scope', 'scopes', multiple=True, type=click.Choice(['source', 'test', 'doc', 'config']))
@click.option('--budget-tokens', default=2000, type=int)
@click.option('--max-bytes', default=16384, type=int)
@click.option('--detail', default='outline', type=click.Choice(['outline', 'source']))
@click.option('--cursor', default=None)
def context_command(task, repo, targets, scopes, budget_tokens, max_bytes, detail, cursor):
    """Return a bounded task-evidence packet without calling a model."""
    from ibwd.retrieval.context import context
    try:
        result = context(repo, task, list(targets), budget_tokens, detail, cursor, list(scopes) or None, max_bytes)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('read')
@click.argument('symbol_id_or_path')
@click.option('--expected-hash', required=True)
@click.option('--range', 'line_range', nargs=2, type=int, default=None)
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--budget-tokens', default=1000, type=int)
@click.option('--max-bytes', default=16384, type=int)
def read_command(symbol_id_or_path, expected_hash, line_range, repo, budget_tokens, max_bytes):
    """Read an exact source span, rejecting stale hashes."""
    from ibwd.retrieval.context import read
    try:
        result = read(repo, symbol_id_or_path, expected_hash, list(line_range) if line_range else None, budget_tokens, max_bytes)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('reduce-output')
@click.argument('log', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--format', 'format_name', required=True, type=click.Choice(['pytest', 'tsc']))
@click.option('--exit-code', required=True, type=int)
def reduce_output_command(log, format_name, exit_code):
    """Explicitly summarize a saved pytest/tsc log; never execute a command."""
    from ibwd.retrieval.output import reduce_output
    try:
        result = reduce_output(log, format_name, exit_code)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    if exit_code:
        raise click.exceptions.Exit(exit_code)


@main.command('impact')
@click.argument('targets', nargs=-1)
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--direction', type=click.Choice(['incoming', 'outgoing']), default='incoming')
@click.option('--relation', 'relations', multiple=True, type=click.Choice(['CALLS', 'IMPORTS', 'INHERITS', 'REFERENCES']))
@click.option('--scope', 'scopes', multiple=True, type=click.Choice(['source', 'test']))
@click.option('--depth', default=2, type=int)
@click.option('--diff', is_flag=True)
@click.option('--heuristics/--no-heuristics', default=True)
@click.option('--limit', default=50, type=int)
@click.option('--max-bytes', default=16384, type=int)
@click.option('--cursor', default=None)
def impact_command(targets, repo, direction, relations, scopes, depth, diff, heuristics, limit, max_bytes, cursor):
    """Show exposure paths and test relevance, optionally across the last indexed change."""
    from ibwd.retrieval.impact import impact
    try:
        result = impact(repo, list(targets), direction, list(relations) or None, depth, list(scopes) or None,
                        diff, heuristics, limit, max_bytes, cursor)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('compiler-evidence')
@click.argument('file')
@click.option('--line', required=True, type=int)
@click.option('--column', required=True, type=int)
@click.option('--project', default='tsconfig.json')
@click.option('--compiler', default=None, type=click.Path(path_type=Path))
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--max-bytes', default=16384, type=int)
def compiler_evidence_command(file, line, column, project, compiler, repo, max_bytes):
    """Query an installed TypeScript compiler; no downloads or emitted code."""
    from ibwd.retrieval.compiler import compiler_evidence
    try:
        result = compiler_evidence(repo, file, line, column, project, compiler, max_bytes)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


if __name__ == "__main__":
    main()
