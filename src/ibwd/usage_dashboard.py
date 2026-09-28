"""Local HTML usage dashboard built from saved IBWD reports."""
from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path
import webbrowser
from datetime import datetime, timezone

from ibwd.local_io import atomic_write
from ibwd.usage_evidence import embedding_profile, token_components
from ibwd.usage_observations import assessments, assessment_value


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _sessions(folder: Path) -> list[tuple[Path, dict]]:
    sessions = []
    for path in sorted((folder / "sessions").glob("*.json")):
        data = _read_json(path)
        if (data is not None and all(isinstance(data.get(k, {}), dict) for k in ('observations', 'server_evidence'))
                and all(isinstance(v, dict) for v in data.get('observations', {}).values())
                and all(isinstance(data.get(k, 'unknown'), str) for k in ('client', 'outcome', 'rework', 'retrieval_usefulness'))):
            sessions.append((path, data))
    return sessions


def _selected_session(sessions: list[tuple[Path, dict]], client: str, session_key: str | None) -> tuple[Path, dict] | None:
    candidates = [(p, r) for p, r in sessions if client == "auto" or r.get("client") == client]
    if session_key:
        for path, report in candidates:
            if report.get("session_key") == session_key:
                return path, report
        raise ValueError("No saved session report matches the requested client/session key.")
    if not candidates:
        return None
    return max(candidates, key=lambda item: (str(item[1].get("last_timestamp") or ""), item[0].stat().st_mtime))


def _count_observation(report: dict, name: str) -> int:
    value = report.get("observations", {}).get(name, {}).get("value", 0)
    return value if type(value) is int else 0


def _tokens(report: dict) -> int | None:
    usage = report.get("usage")
    value = usage.get("total_tokens") if isinstance(usage, dict) else None
    return value if type(value) is int and value >= 0 else None


def _embedding_modes(report: dict) -> Counter:
    modes = Counter()
    embedding = report.get("server_evidence", {}).get("embedding", {})
    if isinstance(embedding, dict):
        for mode, count in embedding.get("modes", {}).items():
            if isinstance(mode, str) and type(count) is int:
                modes[mode] += count
    if not modes:
        for mode, count in report.get('response_evidence', {}).get('semantic_modes', {}).items():
            if type(count) is int:
                modes[mode] += count
    return modes


def _fmt_number(value) -> str:
    if value is None:
        return "Not recorded"
    if isinstance(value, float):
        return f"{value:,.1f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _escape(value) -> str:
    return html.escape(str(value), quote=True)


def _distribution(value):
    if isinstance(value, dict):
        return '; '.join(f'{name} ({count})' for name, count in value.items()) or 'Not recorded'
    return str(value)


def _bar(width: float, label: str, css_class: str = "") -> str:
    bounded = max(0.0, min(100.0, width))
    return (f'<div class="bar-track" aria-label="{_escape(label)}">'
            f'<span class="bar {css_class}" style="width: {bounded:.1f}%"></span></div>')


def _dashboard_html(repo: Path, folder: Path, sessions: list[tuple[Path, dict]],
                    current: tuple[Path, dict] | None, comparison: dict, selected=False, live_seconds=0, client='auto') -> str:
    from ibwd.evaluation_dashboard import evaluation_panel
    evaluation_html = evaluation_panel(folder, current[1] if current else None, client)
    reports = [report for _, report in sessions]
    tokens = [_tokens(report) for report in reports if _tokens(report) is not None]
    modes = Counter()
    for report in reports:
        session_modes = _embedding_modes(report)
        if session_modes:
            modes.update(session_modes)
    retrieval_sessions = sum(_count_observation(report, "retrieval_calls") > 0 or
                             report.get("server_evidence", {}).get("retrieval_requests", 0) > 0
                             for report in reports)
    current_report = current[1] if current else None
    current_path = current[0] if current else None
    current_modes = _embedding_modes(current_report or {})
    current_profile = embedding_profile(current_report or {}) if current_report else ("unknown",)
    groups = comparison.get("groups", []) if isinstance(comparison, dict) else []
    missing = comparison.get("unattributed_retained_requests", 0) if isinstance(comparison, dict) else 0
    unreadable = comparison.get("unreadable_reports", 0) if isinstance(comparison, dict) else 0

    cards = [
        ("Sessions", len(reports), "Saved ordinary-work snapshots"),
        ("Retrieval Sessions", retrieval_sessions, "Sessions with observed retrieval evidence"),
        ("Evidence Items", sum(r.get('response_evidence', {}).get('items', 0) for r in reports), "Items observed in captured responses"),
        ("Passed Checks", sum(r.get('activity', {}).get('operations', {}).get('checks_passed', 0) for r in reports), "Recognized checks with successful exit codes"),
        ("Nonzero Exits", sum(r.get('activity', {}).get('operations', {}).get('failed', 0) for r in reports), "Recorded command and tool exit codes"),
        ("Usage Recorded", f"{len(tokens)}/{len(reports)}", "Snapshots with recorded token totals"),
    ]
    card_html = "\n".join(
        f'<section class="metric"><span>{_escape(label)}</span><strong>{_escape(_fmt_number(value))}</strong>'
        f'<small>{_escape(note)}</small></section>'
        for label, value, note in cards
    )

    mode_total = sum(modes.values()) or 1
    mode_html = "\n".join(
        f'<div class="mode-row"><span>{_escape(mode)}</span><strong>{count}</strong>'
        f'{_bar(count * 100 / mode_total, mode, "accent")}</div>'
        for mode, count in sorted(modes.items())
    ) or '<p class="muted">No embedding observations yet.</p>'

    current_html = '<p class="muted">No saved session report found yet.</p>'
    if current_report:
        current_tokens = _tokens(current_report)
        breakdown = token_components(current_report)
        breakdown_html = ''.join(f'<div><span>{_escape(name.replace("_", " ").title())}</span>'
                                 f'<strong>{_escape("Not separately reported" if name == "cache_creation" and current_report.get("client") == "codex" else _fmt_number(value))}</strong></div>'
                                 for name, value in breakdown.items())
        evidence = current_report.get('server_evidence', {})
        auto_html = ''.join(f'<div><span>{_escape(title)}</span><strong title="{_escape(data["source"])}">{_escape(data["value"])}</strong></div>'
                            for field, title in [('outcome', 'Work status'), ('task_kind', 'Activity (inferred)'),
                                                 ('condition', 'IBWD status'), ('validation', 'Checks'),
                                                 ('rework', 'Correction signals'), ('retrieval_usefulness', 'Retrieval results')]
                            for data in [assessments(current_report)[field]])
        current_html = f"""
        <div class="current-grid">
          <div><span>Client</span><strong>{_escape(current_report.get('client', 'unknown'))}</strong></div>
          <div><span>Session</span><strong>{_escape(current_report.get('session_key', 'unknown'))}</strong></div>
          {auto_html}
          <div><span>Tokens</span><strong>{_escape(_fmt_number(current_tokens))}</strong></div>
          <div><span>Retrieval calls</span><strong>{_escape(_count_observation(current_report, 'retrieval_calls'))}</strong></div>
          <div><span>Embedding observations</span><strong>{_escape(_distribution(dict(current_modes)))}</strong></div>
          <div><span>Current model</span><strong>{_escape(current_report.get('activity', {}).get('current_model', 'Not recorded'))}</strong></div>
          <div><span>Model history</span><strong>{_escape(', '.join(current_report.get('models', [])) or 'Not recorded')}</strong></div>
          <div><span>Effort history</span><strong>{_escape(', '.join(current_report.get('efforts', [])) or 'Not recorded')}</strong></div>
          <div><span>Client version</span><strong>{_escape(', '.join(current_report.get('client_versions', [])) or 'unknown')}</strong></div>
          <div><span>Snapshot</span><strong>Current recorded totals</strong></div>
          <div><span>Saved through</span><strong>{_escape(current_report.get('last_timestamp') or 'unknown')}</strong></div>
          <div><span>Detailed telemetry</span><strong>{_escape(evidence.get('linked_requests', 0))} requests linked</strong></div>
          <div><span>Freshness retries</span><strong>{_escape(_fmt_number(evidence.get('freshness_retries')) if evidence.get('linked_requests') else 'Not captured for older calls')}</strong></div>
          <div><span>Retrieval errors</span><strong>{_escape(current_report.get('observations', {}).get('retrieval_errors', {}).get('value', 'Not recorded'))}</strong></div>
          <div><span>Local helper time (ms)</span><strong>{_escape(_fmt_number(evidence.get('local_helper', {}).get('elapsed_ms')))}</strong></div>
          {breakdown_html}
        </div>
        <p class="fine">Report: {_escape(current_path.name if current_path else 'unknown')}</p>
        <p class="fine">Embedding profile: {_escape(' | '.join(current_profile))}</p>
        """

    rows = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        embedding = group.get("embedding_profile", "unknown")
        displays = group.get('display_assessments', {})
        component_cells = ''.join(f'<td>{_escape(_fmt_number(group.get("median_token_components", {}).get(name)))}</td>'
                                  for name in ('uncached_input', 'cached_input', 'cache_creation', 'output'))
        rows.append(
            f'<tr data-client="{_escape(group.get("client", "unknown"))}">'
            f"<td>{_escape(group.get('client', 'unknown'))}</td>"
            f"<td>{_escape(', '.join(group.get('models', [])) or 'unknown')}</td>"
            f"<td>{_escape(', '.join(group.get('efforts', [])) or 'unknown')}</td>"
            f"<td>{_escape(', '.join(group.get('client_versions', [])) or 'unknown')}</td>"
            f"<td>{_escape(_distribution(displays.get('task_kind', group.get('task_kind', 'Not recorded'))))}</td>"
            f"<td>{_escape(_distribution(displays.get('condition', group.get('condition', 'Not recorded'))))}</td>"
            f"<td>{_escape(embedding)}</td>"
            f"<td>{_escape(group.get('helper_profile', ['unknown']))}</td>"
            f"<td>{_escape(group.get('sessions', 0))}</td>"
            f"<td>{_escape(group.get('provisional_sessions', 'unknown'))}</td>"
            f"<td>{_escape(group.get('missing_token_totals', 'unknown'))}</td>"
            f"<td>{_escape(_distribution(displays.get('retrieval_usefulness', group.get('retrieval_usefulness', {}))))}</td>"
            f"<td>{_escape(_distribution(displays.get('outcome', group.get('outcomes', {}))))}</td>"
            f"<td>{_escape(_distribution(displays.get('rework', group.get('rework', {}))))}</td>"
            f"<td>{_escape(_fmt_number(group.get('median_recorded_tokens')))}</td>"
            f"{component_cells}"
            "</tr>"
        )
    cohort_rows = "\n".join(rows) or '<tr><td colspan="19">No comparison cohorts yet.</td></tr>'

    label_rows = "\n".join(
        "<tr>"
        f"<td>{_escape(report.get('client', 'unknown'))}</td>"
        f"<td>{_escape(report.get('session_key', 'unknown'))}</td>"
        f"<td>{_escape(assessment_value(report, 'outcome'))}</td>"
        f"<td>{_escape(assessment_value(report, 'validation'))}</td>"
        f"<td>{_escape(assessment_value(report, 'retrieval_usefulness'))}</td>"
        "</tr>"
        for report in reports
    ) or '<tr><td colspan="5">No sessions recorded.</td></tr>'

    title = "IBWD Usage Dashboard"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; --ink: #17202a; --muted: #667085; --line: #d0d7de; --panel: #ffffff;
      --page: #f5f7fb; --accent: #0f766e; --accent-2: #4f46e5; --warn: #b45309; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--page); }}
    header {{ padding: 24px 28px 16px; background: #0b1220; color: white; }}
    header h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: 0; }}
    header p {{ margin: 0; color: #cbd5e1; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .metric {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }}
    .metric {{ padding: 14px; min-height: 118px; }}
    .metric span, .current-grid span {{ display: block; color: var(--muted); font-size: 12px; }}
    .metric strong {{ display: block; font-size: 28px; margin: 8px 0; }}
    .metric small {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }}
    .grid > div {{ min-width: 0; }}
    .panel {{ padding: 16px 0; margin-bottom: 16px; min-width: 0; border-top: 1px solid var(--line); }}
    .table-scroll {{ overflow-x: auto; max-width: 100%; }}
    .evaluation-counts {{ display: flex; flex-wrap: wrap; gap: 20px; font-weight: 600; margin: 12px 0; }}
    .evaluation-methods {{ min-width: 650px; }}
    .evaluation-run-grid {{ display: inline-grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 110px;
      gap: 12px; width: calc(100% - 20px); vertical-align: top; overflow-wrap: anywhere; }}
    .evaluation-run-heading {{ font-weight: 600; margin-left: 20px; padding: 10px 0; }}
    .evaluation-runs details {{ border-bottom: 1px solid var(--line); padding: 10px 0; }}
    @media (max-width: 600px) {{ .evaluation-run-grid {{ grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 85px; gap: 8px; }} }}
    .evaluation-report pre {{ white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; max-height: 360px; overflow: auto; }}
    .evaluation-report details {{ margin: 10px 0; }}
    .evaluation-report summary {{ cursor: pointer; }}
    .eval-passed {{ color: #166534; }}
    .eval-failed {{ color: #b91c1c; }}
    .eval-not_evaluated {{ color: var(--muted); }}
    #evaluation-select {{ max-width: 100%; }}
    #agent-evaluations .toolbar label {{ min-width: 0; max-width: 100%; }}
    .cohorts {{ min-width: 1800px; table-layout: fixed; }}
    .labels {{ min-width: 850px; table-layout: fixed; }}
    .labels th:nth-child(1) {{ width: 70px; }}
    .labels th:nth-child(2) {{ width: 180px; }}
    .labels th:nth-child(3), .labels th:nth-child(4) {{ width: 110px; }}
    td, th {{ overflow-wrap: anywhere; }}
    header p {{ overflow-wrap: anywhere; }}
    .toolbar {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }}
    select {{ padding: 8px; border: 1px solid var(--line); border-radius: 4px; background: white; font: inherit; }}
    h2 {{ margin: 0 0 12px; font-size: 18px; }}
    .current-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
    .current-grid div {{ padding: 10px 0; border-bottom: 1px solid #e5e7eb; }}
    .current-grid strong {{ display: block; margin-top: 4px; overflow-wrap: anywhere; }}
    .mode-row {{ display: grid; grid-template-columns: 92px 40px minmax(0, 1fr); gap: 10px; align-items: center; margin: 10px 0; }}
    .bar-track {{ height: 10px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
    .bar {{ display: block; height: 100%; background: var(--accent-2); }}
    .bar.accent {{ background: var(--accent); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; vertical-align: top; padding: 9px 8px; border-bottom: 1px solid #e5e7eb; }}
    th {{ color: #475467; font-weight: 600; background: #f8fafc; }}
    td code {{ white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }}
    .muted, .fine {{ color: var(--muted); }}
    .fine {{ font-size: 12px; overflow-wrap: anywhere; }}
    .quality {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; padding: 7px 10px; background: #fbfcfe; }}
    .pill.warn {{ border-color: #f59e0b; color: var(--warn); background: #fffbeb; }}
    @media (max-width: 820px) {{ main {{ padding: 14px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>{_escape(repo)}</p>
    <p>Updated {_escape(datetime.now(timezone.utc).isoformat(timespec='seconds'))}</p>
  </header>
  <main>
    <section class="metrics">{card_html}</section>
    <section class="grid">
      <div>
        <section class="panel">
          <h2>{'Selected saved session' if selected else 'Latest saved session'}</h2>
          {current_html}
        </section>
        {evaluation_html}
        <section class="panel">
          <div class="toolbar"><h2>Comparison Cohorts</h2>
            <label>Client <select id="cohort-client"><option value="">All clients</option><option value="codex">Codex</option><option value="claude">Claude</option></select></label>
          </div>
          <div class="table-scroll" tabindex="0" role="region" aria-label="Comparison cohorts"><table class="cohorts">
            <thead><tr><th>Client</th><th>Model history</th><th>Effort history</th><th>Version</th><th>Activity</th><th>IBWD status</th><th>Embedding</th><th>Local helper</th><th>Sessions</th><th>Snapshots</th><th>Missing usage</th><th>Retrieval results</th><th>Work status</th><th>Correction signals</th><th>Median tokens</th><th>Uncached input</th><th>Cached input</th><th>Cache creation</th><th>Output</th></tr></thead>
            <tbody>{cohort_rows}</tbody>
          </table></div>
        </section>
      </div>
      <div>
        <section class="panel">
          <h2>Embedding Modes</h2>
          {mode_html}
        </section>
        <section class="panel">
          <h2>Evidence Quality</h2>
          <div class="quality">
            <span class="pill{' warn' if unreadable else ''}">Unreadable reports: {_escape(unreadable)}</span>
            <span class="pill{' warn' if missing else ''}">Unattributed requests (repository history): {_escape(missing)}</span>
            <span class="pill">Linked requests: {_escape(comparison.get('attributed_retained_requests', 'Not recorded'))} / {_escape(comparison.get('retained_server_requests', 'Not recorded'))}</span>
            <span class="pill">Automatic activity summaries: {_escape(len(reports))}</span>
          </div>
          <p class="fine">Observational snapshots; no matched savings baseline. Missing usage remains unknown.</p>
          <p class="fine">Request attribution uses exact receipt IDs across saved sessions. Older or uncaptured receipts remain unlinked; this is not a failed-request count.</p>
        </section>
      </div>
    </section>
    <section class="panel">
      <h2>Session Activity</h2>
      <div class="table-scroll" tabindex="0" role="region" aria-label="Session activity"><table class="labels">
        <thead><tr><th>Client</th><th>Session</th><th>Work status</th><th>Checks</th><th>Retrieval results</th></tr></thead>
        <tbody>{label_rows}</tbody>
      </table></div>
    </section>
  </main>
  <script>
    const clientFilter = document.getElementById('cohort-client');
    clientFilter.value = sessionStorage.getItem('ibwd-client') || '';
    document.getElementById('cohort-client').addEventListener('change', function () {{
      sessionStorage.setItem('ibwd-client', this.value);
      document.querySelectorAll('.cohorts tbody tr[data-client]').forEach(row => {{
        row.hidden = Boolean(this.value && row.dataset.client !== this.value);
      }});
    }});
    clientFilter.dispatchEvent(new Event('change'));
    const evaluationSelect = document.getElementById('evaluation-select');
    if (evaluationSelect) {{
      const applyEvaluation = () => {{
        document.querySelectorAll('.evaluation-report').forEach(report => {{
          report.hidden = report.id !== evaluationSelect.value;
        }});
      }};
      evaluationSelect.addEventListener('change', applyEvaluation);
      applyEvaluation();
    }}
    if ({int(live_seconds)} > 0) setTimeout(() => location.reload(), {int(live_seconds)} * 1000);
  </script>
</body>
</html>
"""


def build_dashboard(repo: Path, client: str = "auto", session_key: str | None = None, *, refresh=True, live_seconds=0) -> Path:
    """Write a local, self-contained HTML dashboard and return its path."""
    repo = repo.resolve()
    if client not in {"auto", "codex", "claude"}:
        raise ValueError("Expected client auto, codex or claude.")
    folder = repo / ".ibwd" / "usage"
    if refresh:
        from ibwd.usage_live import refresh_reports
        status = refresh_reports(repo, client, session_key)
    else:
        status = dict(warnings=[])
    from ibwd.local_io import report_lock
    from ibwd.usage_hooks import refresh_comparison
    from ibwd.telemetry import read_events
    with report_lock(folder / '.report.lock'):
        refresh_comparison(folder, read_events(repo))
        sessions = _sessions(folder)
        current = _selected_session(sessions, client, session_key)
        comparison = _read_json(folder / "comparison.json") or {}
    output = folder / "dashboard.html"
    page = _dashboard_html(repo, folder, sessions, current, comparison, selected=session_key is not None, live_seconds=live_seconds, client=client)
    if status['warnings']:
        page = page.replace('<main>', '<main><p role="status">' + _escape(' '.join(status['warnings'])) + '</p>')
    atomic_write(output, page)
    return output


def open_dashboard(path: Path) -> bool:
    """Open the dashboard in the user's browser/window."""
    return webbrowser.open(path.resolve().as_uri())
