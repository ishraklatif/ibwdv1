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


@main.command('usage-label')
@click.argument('session_key')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default='.')
@click.option('--client', type=click.Choice(['codex', 'claude']), required=True)
@click.option('--outcome', type=click.Choice(['passed', 'failed', 'incomplete', 'unknown']), default='unknown')
@click.option('--task-kind', type=click.Choice(['structural', 'implementation', 'debugging', 'mixed', 'unknown']), default='unknown')
@click.option('--condition', type=click.Choice(['enabled', 'disabled', 'unknown']), default='unknown')
@click.option('--rework', type=click.Choice(['yes', 'no', 'unknown']), default='unknown')
@click.option('--retrieval-usefulness', type=click.Choice(['useful', 'partly-useful', 'not-useful', 'unknown']), default='unknown')
def usage_label(session_key, repo, client, outcome, task_kind, condition, rework, retrieval_usefulness):
    """Record your outcome/rework labels for an existing session snapshot."""
    from ibwd.usage_hooks import label_session
    try:
        path = label_session(repo, client, session_key, outcome=outcome, task_kind=task_kind,
                             condition=condition, rework=rework, retrieval_usefulness=retrieval_usefulness)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(path))


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


@main.command("usage-dashboard")
@click.option("--repo", type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option("--client", type=click.Choice(["auto", "codex", "claude"]), default="auto", show_default=True)
@click.option("--session-key", default=None, help="Open a specific saved session key instead of the latest report.")
@click.option("--open/--no-open", "open_browser", default=True, help="Open the generated local dashboard in a browser window.")
@click.option('--watch', is_flag=True, help='Keep refreshing the browser snapshot until Ctrl+C.')
@click.option('--interval', type=click.IntRange(2, 60), default=5, show_default=True)
def usage_dashboard(repo: Path, client: str, session_key: str | None, open_browser: bool, watch: bool, interval: int) -> None:
    """Refresh current transcript totals and open the local dashboard mid-session."""
    from ibwd.usage_dashboard import build_dashboard, open_dashboard

    try:
        path = build_dashboard(repo, client, session_key, live_seconds=interval if watch else 0)
        opened = open_dashboard(path) if open_browser else False
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(path))
    if open_browser and not opened:
        click.echo("Dashboard generated, but the browser did not report a successful open.", err=True)
    if watch:
        import time
        click.echo('Refreshing current transcript totals. Press Ctrl+C to stop.', err=True)
        try:
            while True:
                time.sleep(interval)
                build_dashboard(repo, client, session_key, live_seconds=interval)
        except KeyboardInterrupt:
            build_dashboard(repo, client, session_key, refresh=False)


@main.command('usage-refresh')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--client', type=click.Choice(['auto', 'codex', 'claude']), default='auto')
@click.option('--session-key', default=None)
@click.option('--json', 'as_json', is_flag=True, help='Print the refreshed report as JSON.')
def usage_refresh(repo, client, session_key, as_json):
    """Read current transcript totals and print a session report without ending the session."""
    from ibwd.usage_live import refresh_reports
    from ibwd.usage_dashboard import _sessions, _selected_session
    from ibwd.usage_hooks import render_report
    try:
        status = refresh_reports(repo, client, session_key)
        for warning in status['warnings']:
            click.echo(warning, err=True)
        selected = _selected_session(_sessions(repo.resolve() / '.ibwd/usage'), client, session_key)
        if not selected:
            raise ValueError('No matching transcript or saved report. Run project setup to enable capture.')
        report = selected[1]
        click.echo(json.dumps(report, indent=2) if as_json else render_report(report))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


@main.command('context')
@click.argument('task')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--target', 'targets', multiple=True)
@click.option('--scope', 'scopes', multiple=True, type=click.Choice(['source', 'test', 'doc', 'config']))
@click.option('--budget-tokens', default=2000, type=int)
@click.option('--max-bytes', default=16384, type=int)
@click.option('--detail', default='outline', type=click.Choice(['outline', 'source']))
@click.option('--cursor', default=None)
@click.option('--semantic/--no-semantic', default=None, help='Override the repository semantic setting.')
@click.option('--helper/--no-helper', default=None, help='Override local candidate reranking.')
def context_command(task, repo, targets, scopes, budget_tokens, max_bytes, detail, cursor, semantic, helper):
    """Return task evidence, using the repository semantic setting unless overridden."""
    from ibwd.retrieval.context import context
    try:
        result = context(repo, task, list(targets), budget_tokens, detail, cursor, list(scopes) or None, max_bytes, semantic, helper)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('helper-config')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--enabled/--disabled', required=True)
@click.option('--model', default='', help='An already installed local Ollama model; never downloaded.')
@click.option('--endpoint', default='http://127.0.0.1:11434', show_default=True)
@click.option('--timeout', type=float, default=5.0, show_default=True)
def helper_config_command(repo, enabled, model, endpoint, timeout):
    """Configure optional local candidate selection; no inference is run here."""
    from ibwd.retrieval.local_helper import configure
    try:
        result = configure(repo, enabled, model, endpoint, timeout)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result))


@main.command('local-assist')
@click.argument('kind', type=click.Choice(['summary', 'handoff', 'log']))
@click.argument('payload_file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--max-bytes', type=int, default=16384)
def local_assist_command(kind, payload_file, repo, max_bytes):
    """Select cited extracts or condense a supplied log using local assistance."""
    from ibwd.retrieval.local_helper import assist
    try:
        with payload_file.open('rb') as stream:
            raw = stream.read(100001)
        if len(raw) > 100000:
            raise ValueError('Payload exceeds 100000 bytes')
        result = assist(repo, kind, json.loads(raw), max_bytes)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False))


@main.command('semantic-index')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--model-path', required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option('--dimensions', required=True, type=int)
@click.option('--timeout', default=120.0, type=float, help='Indexing inference deadline in seconds.')
@click.option('--query-timeout', default=2.0, type=float, help='Optional query inference deadline (1..30 seconds).')
def semantic_index_command(repo, model_path, dimensions, timeout, query_timeout):
    """Explicitly embed source/docs using already installed local weights and runtime."""
    from ibwd.retrieval.semantic import build
    try:
        result = build(repo, model_path, dimensions, timeout, query_timeout)
    except (OSError, ValueError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('semantic-config')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--enabled/--disabled', 'enable', required=True)
@click.option('--resident/--one-shot', default=False, help='Reuse one process-local embedding worker for queries.')
def semantic_config_command(repo, enable, resident):
    """Persist the repository's local semantic opt-in. No inference or downloads."""
    from ibwd.retrieval.semantic import configure
    try:
        result = configure(repo, enable, resident)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result))


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


@main.command('artifact-save')
@click.argument('kind', type=click.Choice(['summary', 'handoff']))
@click.argument('payload_file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--max-bytes', default=16384, type=int)
def artifact_save_command(kind, payload_file, repo, max_bytes):
    """Save cited extracts or a supplied handoff from a JSON payload file."""
    from ibwd.retrieval.durable import save
    try:
        with payload_file.open('rb') as stream:
            raw = stream.read(24001)
        if len(raw) > 24000:
            raise ValueError('Artifact input exceeds 24000 bytes.')
        result = save(repo, kind, json.loads(raw), max_bytes)
    except (OSError, ValueError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('artifact-read')
@click.argument('artifact_id')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--max-bytes', default=16384, type=int)
def artifact_read_command(artifact_id, repo, max_bytes):
    """Retrieve a saved artifact only when its source and dependencies are current."""
    from ibwd.retrieval.durable import retrieve
    try:
        result = retrieve(repo, artifact_id, max_bytes)
    except (OSError, ValueError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, ensure_ascii=False, separators=(',', ':')))


@main.command('eval-agent')
@click.argument('cases_file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument('traces_file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--replay-read-only', is_flag=True, help='Replay allowlisted read-only IBWD calls in --repo.')
@click.option('--repo', type=click.Path(exists=True, file_okay=False, path_type=Path), default=Path.cwd)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), help='Write JSON report to this path.')
@click.option('--client', type=click.Choice(['codex', 'claude']), help='Client for an explicit session association.')
@click.option('--session-key', help='Session key shown in the usage dashboard; requires --client.')
def eval_agent(cases_file: Path, traces_file: Path, replay_read_only: bool, repo: Path, output: Path | None,
               client: str | None, session_key: str | None) -> None:
    """Score recorded agent tool traces locally; does not run an agent or model."""
    from copy import deepcopy
    from ibwd.evaluation import EvaluationInputError, evaluate, replay_read_only as replay, save_evaluation, validate_inputs
    from ibwd.local_io import atomic_write

    try:
        cases = json.loads(cases_file.read_text(encoding='utf-8'))
        traces = json.loads(traces_file.read_text(encoding='utf-8'))
        validate_inputs(cases, traces)
        if bool(client) != bool(session_key):
            raise EvaluationInputError('Supply both --client and --session-key to link a session')
        if session_key:
            import re
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_key):
                raise EvaluationInputError('Invalid session key')
        original_traces = deepcopy(traces)
        if replay_read_only:
            for run in traces.get('runs', []):
                replay_result = replay(run, repo)
                for call, item in zip(run['calls'], replay_result['calls']):
                    for field in ('result', 'output', 'error', 'is_error', 'isError', 'blocked', 'latency_ms'):
                        call.pop(field, None)
                    call.update(item)
                for field in ('final_state', 'returned_evidence', 'tokens', 'cost_usd'):
                    run.pop(field, None)
                run['latency_ms'] = sum(call.get('latency_ms', 0) for call in run['calls'])
        report = evaluate(cases, traces)
        report['execution_mode'] = 'read-only-replay' if replay_read_only else 'recorded-outcomes'
        save_evaluation(report, repo, client=client, session_key=session_key,
                        cases_data=cases, traces_data=original_traces)
    except (OSError, UnicodeError, json.JSONDecodeError, EvaluationInputError, KeyError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if output:
        try:
            atomic_write(output, rendered)
        except OSError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(str(output))
    else:
        click.echo(rendered, nl=False)
    from ibwd.usage_dashboard import build_dashboard
    try:
        build_dashboard(repo, refresh=False)
    except (OSError, ValueError, TimeoutError) as exc:
        click.echo(f'Evaluation saved; dashboard refresh failed: {exc}', err=True)


if __name__ == "__main__":
    main()
