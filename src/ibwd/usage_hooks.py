"""Local, opt-in session reports driven by client lifecycle hooks.

No model calls, transcript copies, daemon, or client launches. Hook stdout stays
empty so reporting cannot inject instructions or trigger another agent turn.
"""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
import sqlite3

from ibwd.usage_stream import incremental_report
from ibwd.local_io import atomic_write as _atomic_write, report_lock
from ibwd.telemetry import read_events


def hook_config(client: str, repo: Path) -> dict:
    """Generate POSIX command hooks using the installed interpreter."""
    command = shlex.join([str(Path(sys.executable).absolute()), "-m", "ibwd.cli",
                          "usage-hook", "--client", client, "--repo", str(repo.resolve())])
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command,
                                         "timeout": timeout}]}]
                      for event, timeout in (("Stop", 30), ("SessionEnd", 3))}}


def merge_hook_config(config: dict, client: str, repo: Path) -> dict:
    """Merge command entries without replacing unrelated settings or hooks."""
    if not isinstance(config, dict) or not isinstance(config.get("hooks", {}), dict):
        raise ValueError("Existing hook configuration must contain a hooks object.")
    hooks = config.setdefault("hooks", {})
    for event, groups in hook_config(client, repo)["hooks"].items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"Existing {event} hooks must be an array.")
        if groups[0] not in existing:
            existing.append(groups[0])
    return config


def install_hooks(client: str, repo: Path) -> Path:
    """Merge only our hooks; preserve existing settings and do not grant trust."""
    repo = repo.resolve()
    relative = ".codex/hooks.json" if client == "codex" else ".claude/settings.local.json"
    path = repo / relative
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Refusing to install hooks through a symlink.")
    original = path.read_text(encoding="utf-8") if path.exists() else None
    config = merge_hook_config(json.loads(original) if original is not None else {}, client, repo)
    updated = json.dumps(config, indent=2) + "\n"
    if original != updated:
        if original is not None:
            # Retain the first pre-install configuration, never overwrite it.
            backup = path.with_name(path.name + ".ibwd-backup")
            try:
                with backup.open("x", encoding="utf-8") as stream:
                    stream.write(original)
            except FileExistsError:
                pass
        _atomic_write(path, updated)
    return path


def render_report(report: dict) -> str:
    usage = report["usage"]
    tokens = f"{usage['total_tokens']:,}" if usage else "not recorded"
    evidence = report.get("instruction_evidence", {})
    guidance = "observed" if evidence.get("ibwd_mentioned") else "not observed (not proof it was absent)"
    lines = ["# IBWD session report", "",
             f"Client: {report['client']}",
             f"Snapshot: {report['capture_event']} (recorded data may be incomplete)",
             f"Last recorded activity: {report['last_timestamp'] or 'unknown'}", "",
             f"Direct IBWD calls: {report['direct_ibwd_calls']}",
             f"Recorded tokens: {tokens}",
             f"IBWD mention in recognized instruction records: {guidance}", "",
             "## Recorded tool calls", ""]
    lines.extend(f"- `{name}`: {count}" for name, count in report["tool_calls"].items())
    if not report["tool_calls"]:
        lines.append("No direct tool calls recorded.")
    lines += ["", "## Interpretation", "",
              "Zero direct IBWD calls does not establish why it was unused or whether it was available.",
              "Shell/orchestration calls and child sessions may be missing from these counts.",
              "Instruction evidence is limited to recognized instruction records, not ordinary mentions.",
              "Task success, server availability, task category and savings are not inferred.",
              "Token totals describe this session, not tokens saved by IBWD."]
    if report["warnings"]:
        lines += ["", "## Recording warnings", ""]
        lines.extend(f"- {warning}" for warning in report["warnings"])
    lines += ["", "## Adoption evidence", ""]
    for name, observation in report.get("observations", {}).items():
        lines.append(f"- {name}: {observation['value']} — {observation['source']}")
    lines += ["", "A scan is maintenance, not retrieval. Counts are observed lower bounds, not proof of complete coverage.",
              "See comparison.md for separate client cohorts and unattributed server activity."]
    return "\n".join(lines) + "\n"


def _configuration(repo, client):
    import tomllib
    from ibwd.setup import server_config, _server_matches
    path = repo / (".codex/config.toml" if client == "codex" else ".mcp.json")
    try:
        config = tomllib.loads(path.read_text()) if client == "codex" else json.loads(path.read_text())
        entry = config.get("mcp_servers" if client == "codex" else "mcpServers", {}).get("ibwd")
        value = True if _server_matches(entry, server_config(repo)) and entry.get("enabled") is not False else "unknown"
        if isinstance(entry, dict) and entry.get("enabled") is False:
            value = False
        if client == "claude":
            settings = repo / ".claude/settings.local.json"
            if settings.exists() and "ibwd" in json.loads(settings.read_text()).get("disabledMcpjsonServers", []):
                value = False
        return {"value": value, "source": "Project MCP command/repo snapshot at capture; global settings and client loading not verified."}
    except (OSError, ValueError, AttributeError):
        return {"value": "unknown", "source": "No readable matching project MCP configuration."}


def refresh_comparison(folder: Path, events: list[dict]) -> None:
    """Display observational cohorts; no automatic savings or outcome inference."""
    from collections import defaultdict
    import statistics
    groups = defaultdict(list)
    ids = set()
    unreadable = 0
    for path in sorted((folder / "sessions").glob("*.json")):
        try:
            report = json.loads(path.read_text())
            group = (report["client"], tuple(report["models"]), tuple(report["efforts"]), tuple(report["client_versions"]),
                     report["task_kind"], report["condition"])
            groups[group].append(report)
            ids.update(report.get("server_observation_ids", []))
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
    server = {e["observation_id"]: e for e in events}
    unmatched = sum(oid not in ids for oid in server)
    lines = ["# IBWD ordinary-work comparison", "", "Observational snapshots, not matched tasks or measured savings.",
             "Models, effort, client versions, task kinds and conditions remain separate; unknown labels are not inferred.", "",
             f"Unreadable reports: {unreadable}", f"Retained server requests: {len(server)}",
             f"Unattributed retained server requests: {unmatched}",
             "Server ledger is a bounded recent window; connection/request IDs are not conversation IDs.", ""]
    for group, reports in sorted(groups.items()):
        tokens = [r["usage"]["total_tokens"] for r in reports if r.get("usage")]
        lines += [f"## {' / '.join(str(v) for v in group)}", "",
                  f"Sessions: {len(reports)}; missing token totals: {len(reports) - len(tokens)}; "
                  f"unknown outcomes: {sum(r['outcome'] == 'unknown' for r in reports)}.",
                  f"Median recorded tokens (available snapshots, all outcomes): {statistics.median(tokens) if tokens else 'unknown'}.",
                  f"Incomplete/uncertain usage: {sum(r.get('observations', {}).get('usage_incomplete', {}).get('value') != False for r in reports)}.",
                  f"Observed scans: {sum(r.get('observations', {}).get('scan_calls', {}).get('value', 0) for r in reports)}; "
                  f"observed retrieval calls: {sum(r.get('observations', {}).get('retrieval_calls', {}).get('value', 0) for r in reports)}.", ""]
    _atomic_write(folder / "comparison.md", "\n".join(lines) + "\n")


def capture_session(event: dict, client: str, repo: Path) -> Path | None:
    """Consume a client's actual transcript path, never guess the newest log."""
    if not isinstance(event, dict):
        raise ValueError("Expected a hook event object.")
    if event.get("hook_event_name") not in {"Stop", "SessionEnd"}:
        raise ValueError("Expected a Stop or SessionEnd hook event.")
    if event.get("agent_id") or event.get("agent_type"):
        return None  # Parent and child usage must not be silently combined.
    repo = repo.resolve()
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("Hook event has no absolute working directory.")
    if not Path(cwd).resolve().is_relative_to(repo):
        raise ValueError("Hook working directory is outside the configured repository.")
    transcript = event.get("transcript_path")
    if not isinstance(transcript, str) or not Path(transcript).is_absolute():
        raise ValueError("Client did not supply an absolute transcript path.")
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Client did not supply a session identity.")
    # Match transcript identity to the event before persisting an attribution.
    import hashlib

    expected = hashlib.sha256((client + ":" + session_id).encode()).hexdigest()[:20]
    base = repo / ".ibwd/usage"
    name = f"{client}-{expected}"
    with report_lock(base / ".report.lock"):
        checkpoint = base / "state" / f"{name}.json"
        report, state = incremental_report(Path(transcript), client, checkpoint)
        if report["session_key"] != expected:
            raise ValueError("Hook session identity does not match the transcript.")
        report["capture_event"] = event["hook_event_name"]
        report["provisional"] = True  # SessionEnd does not promise final accounting.
        report["observations"]["configured"] = _configuration(repo, client)
        try:
            events = read_events(repo)
        except (OSError, ValueError, sqlite3.Error):
            events = []
            report["warnings"].append("Server ledger could not be read; attribution remains incomplete.")
        linked = [e for e in events if e["observation_id"] in report["server_observation_ids"]]
        report["server_evidence"] = {"linked_requests": len({e['observation_id'] for e in linked}),
                                     "join": "Exact response _meta.ibwd.observation_id only; never timestamps or connection identity."}
        if linked:
            report["observations"]["connection_observed"] = {"value": True, "source": "Exact response-ID join to local server ledger."}
        folder = base / "sessions"
        destination = folder / f"{name}.json"
        _atomic_write(destination, json.dumps(report, indent=2) + "\n")
        _atomic_write(folder / f"{name}.md", render_report(report))
        _atomic_write(base / f"latest-{client}.md", render_report(report))
        refresh_comparison(base, events)
        # Commit parser progress last: interrupted publication replays safely.
        _atomic_write(checkpoint, json.dumps(state, separators=(",", ":")) + "\n")
    return destination
