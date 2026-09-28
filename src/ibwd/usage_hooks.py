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
from ibwd.telemetry import read_events, key
from ibwd.usage_evidence import retrieval_evidence, evidence_lines, attention, embedding_profile
from ibwd.usage_observations import assessments, assessment_value

LABEL_OPTIONS = {'outcome': {'passed', 'failed', 'incomplete', 'unknown'},
                 'task_kind': {'structural', 'implementation', 'debugging', 'mixed', 'unknown'},
                 'condition': {'enabled', 'disabled', 'unknown'}, 'rework': {'yes', 'no', 'unknown'},
                 'retrieval_usefulness': {'useful', 'partly-useful', 'not-useful', 'unknown'}}


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
             f"Session key: {report['session_key']}",
             f"Work status: {assessment_value(report, 'outcome')}",
             f"Activity: {assessment_value(report, 'task_kind')}",
             f"IBWD: {assessment_value(report, 'condition')}",
             f"Checks: {assessment_value(report, 'validation')}",
             f"Correction signals: {assessment_value(report, 'rework')}",
             f"Retrieval results: {assessment_value(report, 'retrieval_usefulness')}",
             f"Snapshot: {report['capture_event']} (current recorded totals; session end is not required)",
             f"Last recorded activity: {report['last_timestamp'] or 'unknown'}", "",
             f"Direct IBWD calls: {report['direct_ibwd_calls']}",
             f"Observed IBWD calls including decoded responses: {report.get('observed_ibwd_calls', report['direct_ibwd_calls'])}",
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
              "Activity categories are inferred; tool/check results do not independently grade task success or usefulness.",
              "Token totals describe this session, not tokens saved by IBWD."]
    if report["warnings"]:
        lines += ["", "## Recording warnings", ""]
        lines.extend(f"- {warning}" for warning in report["warnings"])
    lines += ["", "## Adoption evidence", ""]
    for name, observation in report.get("observations", {}).items():
        lines.append(f"- {name}: {observation['value']} — {observation['source']}")
    lines += ["", "A scan is maintenance, not retrieval. Counts are observed lower bounds, not proof of complete coverage.",
              "See comparison.md for separate client cohorts and unattributed server activity."]
    lines += ['', *evidence_lines(report)]
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


def apply_labels(report: dict, labels: dict) -> None:
    values = {field: (labels.get(field, 'unknown') if field == 'retrieval_usefulness' else labels[field])
              for field in LABEL_OPTIONS}
    if any(not isinstance(value, str) or value not in LABEL_OPTIONS[field] for field, value in values.items()):
        raise ValueError('Invalid labels')
    current = labels.get('snapshot') == report.get('snapshot_key', [report['records'], report['last_timestamp']])
    report['label_evidence'] = {'source': 'user_reported', 'status': 'current' if current else 'stale'}
    if current:
        report.update(values)


def label_session(repo: Path, client: str, session_key: str, *, outcome: str,
                  task_kind: str, condition: str, rework: str, retrieval_usefulness: str = 'unknown') -> Path:
    """Attach explicit user labels to one recorded snapshot; never certify tests."""
    import re
    if client not in {'codex', 'claude'} or not re.fullmatch(r'[0-9a-f]{20}', session_key):
        raise ValueError('Expected a client and 20-character session_key from its saved report.')
    labels = dict(outcome=outcome, task_kind=task_kind, condition=condition, rework=rework,
                  retrieval_usefulness=retrieval_usefulness)
    if any(value not in LABEL_OPTIONS[field] for field, value in labels.items()):
        raise ValueError('Unsupported session label.')
    base = repo.resolve() / '.ibwd/usage'
    name = f'{client}-{session_key}'
    with report_lock(base / '.report.lock'):
        path = base / 'sessions' / f'{name}.json'
        report = json.loads(path.read_text())
        if report['client'] != client or report['session_key'] != session_key:
            raise ValueError('Saved report identity does not match.')
        labels['snapshot'] = report.get('snapshot_key', [report['records'], report['last_timestamp']])
        _atomic_write(base / 'labels' / f'{name}.json', json.dumps(labels) + '\n')
        apply_labels(report, labels)
        report['automatic_assessment'] = assessments(report)
        _atomic_write(path, json.dumps(report, indent=2) + '\n')
        _atomic_write(path.with_suffix('.md'), render_report(report))
        latest = base / f'latest-{client}.md'
        if latest.exists() and f'Session key: {session_key}\n' in latest.read_text():
            _atomic_write(latest, render_report(report))
        refresh_comparison(base, read_events(repo.resolve()))
    return path


def refresh_comparison(folder: Path, events: list[dict]) -> None:
    """Display observational cohorts; no automatic savings or outcome inference."""
    from collections import Counter, defaultdict
    import statistics
    from ibwd.usage_evidence import helper_profile, token_components
    groups = defaultdict(list)
    ids = set()
    unreadable = 0
    for path in sorted((folder / "sessions").glob("*.json")):
        try:
            report = json.loads(path.read_text())
            if not isinstance(report, dict):
                raise ValueError('Invalid report')
            for field in ('models', 'efforts', 'client_versions'):
                if not isinstance(report.get(field), list) or not all(isinstance(v, str) for v in report[field]):
                    raise ValueError('Invalid cohort metadata')
            for field in ('client', 'task_kind', 'condition', 'project_key', 'rework'):
                if not isinstance(report.get(field, 'unknown'), str):
                    raise ValueError('Invalid cohort label')
            group = (report["client"], tuple(report["models"]), tuple(report["efforts"]), tuple(report["client_versions"]),
                     report.get('project_key', 'unknown'),
                     *(report[field] if report[field] != 'unknown' else 'auto: ' + assessment_value(report, field)
                       for field in ('task_kind', 'condition')))
            if not isinstance(report.get('outcome'), str) or not isinstance(report.get('observations', {}), dict):
                raise ValueError('Invalid report metadata')
            observations = report.get('observations', {})
            if any(not isinstance(v, dict) for v in observations.values()):
                raise ValueError('Invalid observations')
            for name in ('scan_calls', 'retrieval_calls'):
                if type(observations.get(name, {}).get('value', 0)) is not int:
                    raise ValueError('Invalid observation counter')
            usage = report.get('usage')
            if usage is not None and (not isinstance(usage, dict) or type(usage.get('total_tokens')) is not int or usage['total_tokens'] < 0):
                raise ValueError('Invalid token total')
            observation_ids = report.get('server_observation_ids', [])
            if not isinstance(observation_ids, list) or not all(isinstance(oid, str) for oid in observation_ids):
                raise ValueError('Invalid observation identities')
            if not isinstance(report.get('server_evidence', {}), dict):
                raise ValueError('Invalid server evidence')
            if report.get('retrieval_usefulness', 'unknown') not in LABEL_OPTIONS['retrieval_usefulness']:
                raise ValueError('Invalid usefulness label')
            group = (*group, embedding_profile(report), helper_profile(report))
            groups[group].append(report)
            ids.update(observation_ids)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
    server = {e["observation_id"]: e for e in events}
    unmatched = sum(oid not in ids for oid in server)
    lines = ["# IBWD ordinary-work comparison", "", "Observational snapshots, not matched tasks or measured savings.",
             "Projects, models, effort, client versions, task kinds, conditions and observed embedding profiles remain separate; unknown labels are not inferred.",
             "No matched baseline is established: adoption and retrieval efficiency only. Failures and rework remain included.", "",
             f"Unreadable reports: {unreadable}", f"Retained server requests: {len(server)}",
             f"Unattributed retained server requests: {unmatched}",
             "Server ledger is a bounded recent window; connection/request IDs are not conversation IDs.", ""]
    rows = []
    for group, reports in sorted(groups.items()):
        tokens = [r["usage"]["total_tokens"] for r in reports if r.get("usage")]
        outcomes = dict(sorted(Counter(r['outcome'] for r in reports).items()))
        cohort_ids = {oid for r in reports for oid in r.get('server_observation_ids', [])}
        usefulness = dict(sorted(Counter(r.get('retrieval_usefulness', 'unknown') for r in reports).items()))
        components = [token_components(r) for r in reports]
        component_medians = {}
        component_missing = {}
        for name in ('uncached_input', 'cached_input', 'cache_creation', 'output'):
            values = [c[name] for c in components if c[name] is not None]
            component_medians[name] = statistics.median(values) if values else None
            component_missing[name] = len(reports) - len(values)
        rows.append(dict(zip(('client', 'models', 'efforts', 'client_versions', 'project_key', 'task_kind', 'condition', 'embedding_profile', 'helper_profile'), group)) | {
            'display_assessments': {field: dict(Counter(assessment_value(r, field) for r in reports))
                                    for field in ('task_kind', 'condition', 'outcome', 'rework', 'retrieval_usefulness')},
            'sessions': len(reports), 'outcomes': outcomes,
            'retrieval_usefulness': usefulness,
            'usefulness_known_sessions': len(reports) - usefulness.get('unknown', 0),
            'rework': dict(sorted(Counter(r.get('rework', 'unknown') for r in reports).items())),
            'provisional_sessions': sum(r.get('provisional', True) is not False for r in reports),
            'missing_token_totals': len(reports) - len(tokens),
            'median_recorded_tokens': statistics.median(tokens) if tokens else None,
            'median_token_components': component_medians, 'missing_token_components': component_missing,
            'retrieval_evidence': retrieval_evidence([e for e in events if e['observation_id'] in cohort_ids]),
        })
        lines += [f"## {' / '.join(str(v) for v in group)}", "",
                  f"Work status: {json.dumps(rows[-1]['display_assessments']['outcome'], sort_keys=True)}.",
                  f"Correction signals: {json.dumps(rows[-1]['display_assessments']['rework'], sort_keys=True)}.",
                  f"Retrieval results: {json.dumps(rows[-1]['display_assessments']['retrieval_usefulness'], sort_keys=True)}.",
                  f"Provisional sessions: {rows[-1]['provisional_sessions']}.",
                  f"Sessions: {len(reports)}; missing token totals: {len(reports) - len(tokens)}; "
                  f"unknown outcomes: {sum(r['outcome'] == 'unknown' for r in reports)}.",
                  f"Median recorded tokens (available snapshots, all outcomes): {statistics.median(tokens) if tokens else 'unknown'}.",
                  f"Incomplete/uncertain usage: {sum(r.get('observations', {}).get('usage_incomplete', {}).get('value') != False for r in reports)}.",
                  f"Observed scans: {sum(r.get('observations', {}).get('scan_calls', {}).get('value', 0) for r in reports)}; "
                  f"observed retrieval calls: {sum(r.get('observations', {}).get('retrieval_calls', {}).get('value', 0) for r in reports)}.", ""]
        lines.extend(f'- {item}' for item in sorted({item for report in reports for item in attention(report)}))
        lines += ['', *evidence_lines({'server_evidence': rows[-1]['retrieval_evidence']})]
        lines.append('')
    _atomic_write(folder / 'comparison.json', json.dumps({
        'schema_version': 1, 'groups': rows, 'unreadable_reports': unreadable,
        'unattributed_retained_requests': unmatched,
        'interpretation': 'Observational adoption and retrieval efficiency only; no matched baseline or savings claim.',
    }, indent=2) + '\n')
    _atomic_write(folder / "comparison.md", "\n".join(lines) + "\n")


def capture_session(event: dict, client: str, repo: Path) -> Path | None:
    """Consume a client's actual transcript path, never guess the newest log."""
    if not isinstance(event, dict):
        raise ValueError("Expected a hook event object.")
    if event.get("hook_event_name") not in {"Stop", "SessionEnd", "Refresh"}:
        raise ValueError("Expected a Stop, SessionEnd or Refresh event.")
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
        report['activity']['session_ended'] = event['hook_event_name'] == 'SessionEnd' or event.get('session_ended') is True
        report['project_key'] = key(repo)
        report['snapshot_key'] = key({k: v for k, v in state.items() if k != 'parser'})
        labels_path = base / 'labels' / f'{name}.json'
        if labels_path.exists():
            try:
                apply_labels(report, json.loads(labels_path.read_text()))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                report['warnings'].append('Saved user labels could not be read; labels remain unknown.')
        report["observations"]["configured"] = _configuration(repo, client)
        try:
            events = read_events(repo)
        except (OSError, ValueError, sqlite3.Error):
            events = []
            report["warnings"].append("Server ledger could not be read; attribution remains incomplete.")
        linked = [e for e in events if e["observation_id"] in report["server_observation_ids"]]
        report["server_evidence"] = retrieval_evidence(linked)
        if linked:
            report["observations"]["connection_observed"] = {"value": True, "source": "Exact response-ID join to local server ledger."}
        modes = report.get('response_evidence', {}).get('semantic_modes', {})
        if any(e.get('semantic_status') in {'fallback', 'ready', 'disabled'} for e in linked) or any(
                mode in modes for mode in ('fallback', 'ready', 'disabled')):
            report['observations']['fallback_observed'] = dict(
                value=bool(report['server_evidence']['semantic_fallbacks'] or modes.get('fallback')),
                source='Semantic fallback in captured responses/linked requests; shell fallback is not assessed.')
        report['automatic_assessment'] = assessments(report)
        from datetime import datetime, timezone
        report['captured_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        folder = base / "sessions"
        destination = folder / f"{name}.json"
        _atomic_write(destination, json.dumps(report, indent=2) + "\n")
        _atomic_write(folder / f"{name}.md", render_report(report))
        latest = base / f'latest-{client}.md'
        other_times = []
        for p in folder.glob(f'{client}-*.json'):
            if p != destination:
                try:
                    other_times.append(json.loads(p.read_text()).get('last_timestamp') or '')
                except (OSError, ValueError, AttributeError):
                    pass
        if not other_times or (report.get('last_timestamp') or '') >= max(other_times):
            _atomic_write(latest, render_report(report))
        refresh_comparison(base, events)
        # Commit parser progress last: interrupted publication replays safely.
        _atomic_write(checkpoint, json.dumps(state, separators=(",", ":")) + "\n")
        registration = dict(client=client, session_key=expected, transcript_path=transcript, cwd=cwd)
        if report['activity']['session_ended']:
            registration['ended_size'] = state['size']
        _atomic_write(base / 'sources' / f'{name}.json', json.dumps(registration) + '\n')
    return destination
