"""Render locally saved evaluations inside the usage dashboard."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

from ibwd.evaluation import METHODS


def _escape(value):
    return html.escape(str(value), quote=True)


def _percent(values):
    values = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
    return f'{sum(values) / len(values):.1%}' if values else 'Not evaluated'


def _checks(rows, key, value):
    checked = [row[key][value] for row in rows if row[key].get(value) is not None]
    return f'{sum(v is True for v in checked)} / {len(checked)} passed' if checked else 'Not evaluated'


def _method_rows(report):
    rows = report['results']
    repeated = [r for r in report['repeatability'].values() if r['runs'] > 1]
    variants = [r for r in report['robustness'].values() if r['variants'] > 1]
    semantic = [r['arguments']['semantic_match_rate'] for r in rows]
    recovery = [r['recovery'] for r in rows if r['recovery'] is not None and r['recovery']['recovered'] is not None]
    state_total = sum(r['state_tracking']['total'] + r['dependencies']['total'] for r in rows)
    state_passed = sum(r['state_tracking']['passed'] + r['dependencies']['passed'] for r in rows)
    observed_calls = sum(r['cost']['tool_calls'] for r in rows)
    summaries = {
        'schema': _checks(rows, 'schema', 'valid') if observed_calls else 'No tool calls',
        'arguments': 'F1 ' + _percent([r['arguments']['parameter_f1'] for r in rows]),
        'semantic': _percent(semantic),
        'grounding': f"{sum(r['grounding']['unsupported_count'] for r in rows)} lexical flags" if observed_calls else 'No tool calls',
        'execution': (f"{sum(r['execution']['successful_calls'] for r in rows)} successful; "
                      f"{sum(r['execution']['failed_calls'] for r in rows)} failed; "
                      f"{sum(r['execution']['unobserved_calls'] for r in rows)} unrecorded"),
        'retrieval': 'Precision ' + _percent([r['retrieval']['precision'] for r in rows]) +
                     '; recall ' + _percent([r['retrieval']['recall'] for r in rows]),
        'trajectory': 'Precision ' + _percent([r['trajectory']['precision'] for r in rows]) +
                      '; recall ' + _percent([r['trajectory']['recall'] for r in rows]),
        'abstention': _checks(rows, 'abstention', 'correct'),
        'recovery': f"{sum(r['recovered'] and r['retry_limit_respected'] for r in recovery)} / {len(recovery)} recovered within limit" if recovery else 'Not evaluated',
        'robustness': f"{len(variants)} groups; pass rate " + _percent([r['pass_rate'] for r in variants]) if variants else 'Not evaluated',
        'safety': _checks(rows, 'safety', 'safe'),
        'cost': f"{sum(any(r['cost'].get(k) is not None for k in ('tokens', 'latency_ms', 'cost_usd')) for r in rows)} / {len(rows)} runs with measurements",
        'repeatability': f"{len(repeated)} repeated cases; pass@1 " + _percent([r['pass_at_1'] for r in repeated]) if repeated else 'Not evaluated',
        'state_tracking': f'{state_passed} / {state_total} checks passed' if state_total else 'Not evaluated',
    }
    notes = {
        'schema': 'Published MCP argument schemas', 'arguments': 'Ordered alignment; exact identifiers',
        'semantic': 'Explicit free-text fields only', 'grounding': 'Hints for review; excluded from pass/fail',
        'execution': report.get('execution_mode', 'recorded-outcomes'),
        'retrieval': 'Exact evidence identities; precision needs returned evidence',
        'trajectory': f"{sum(r['redundant_identical_calls'] for r in rows)} repeated identical calls",
        'abstention': 'Includes required clarifications', 'recovery': 'Success must follow the injected error',
        'robustness': 'Distinct variants within matching recorded settings',
        'safety': 'Tool, path and confirmation rules; no injection-resistance score',
        'cost': 'Recorded measurements only', 'repeatability': 'Same case and recorded settings',
        'state_tracking': 'Dependencies and latest argument values',
    }
    return ''.join(f'<tr><th scope="row">{_escape(label)}</th><td>{_escape(summaries[key])}</td>'
                   f'<td class="muted">{_escape(notes[key])}</td></tr>' for key, label in METHODS.items())


def _report_html(report, index):
    rows = report['results']
    summary = report['summary']
    linked = report.get('session_key')
    scope = f"{report.get('client')} session {linked}" if linked else 'Repository evaluation (no session association)'
    run_rows = []
    for row in rows:
        status = row['status']
        if status not in {'passed', 'failed', 'not_evaluated'}:
            raise ValueError('Unknown evaluation status')
        details = json.dumps(row, indent=2, ensure_ascii=False, allow_nan=False)
        run_rows.append(f'<details data-evaluation-status="{status}"><summary aria-label="Evidence for {_escape(row["case_id"])}">'
                        f'<span class="evaluation-run-grid"><span>{_escape(row["case_id"])}</span>'
                        f'<span>{_escape(row["run_id"])}</span><span class="eval-{status}">'
                        f'{_escape(status.replace("_", " ").title())}</span></span></summary><pre>{_escape(details)}</pre></details>')
    unrun = report.get('unrun_cases', [])
    limits = ''.join(f'<li>{_escape(item)}</li>' for item in report.get('limitations', []))
    cohorts = json.dumps({'repeatability': report['repeatability'], 'robustness': report['robustness']},
                        indent=2, ensure_ascii=False, allow_nan=False)
    return f'''<div class="evaluation-report" id="evaluation-{index}" {'hidden' if index else ''}>
      <p class="fine">{_escape(scope)} | {_escape(report['created_at'])}</p>
      <div class="evaluation-counts"><span class="eval-passed">{_escape(summary['passed'])} passed</span>
        <span class="eval-failed">{_escape(summary['failed'])} failed</span>
        <span>{_escape(summary['not_evaluated'])} not evaluated</span><span>{len(unrun)} cases not run</span></div>
      <div class="table-scroll" tabindex="0" role="region" aria-label="Evaluation methods"><table class="evaluation-methods">
        <thead><tr><th>Method</th><th>Result</th><th>Evidence</th></tr></thead><tbody>{_method_rows(report)}</tbody></table></div>
      <h3>Evaluated runs</h3>
      <div class="evaluation-runs" role="region" aria-label="Evaluated runs">
        <div class="evaluation-run-grid evaluation-run-heading"><span>Case</span><span>Run</span><span>Outcome</span></div>
        {''.join(run_rows)}</div>
      <details><summary>Repeated trials and robustness</summary><pre>{_escape(cohorts)}</pre></details>
      <details><summary>Coverage and limitations</summary><p>Cases not run: {_escape(', '.join(unrun) or 'None')}</p><ul>{limits}</ul></details>
    </div>'''


def evaluation_panel(folder: Path, current_report: dict | None, client='auto') -> str:
    candidates, unreadable = [], 0
    for path in (folder / 'evaluations').glob('*.json'):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(report, dict) or report.get('schema_version') != 2 or report.get('evaluation') != 'offline-recorded-agent-traces':
                raise ValueError('Unsupported evaluation report')
            if not isinstance(report.get('created_at'), str) or type(report.get('runs')) is not int:
                raise ValueError('Invalid evaluation metadata')
            if report.get('repo') != str(folder.parent.parent.resolve()):
                continue
            if report.get('session_key'):
                if client != 'auto' and report.get('client') != client:
                    continue
                if current_report and (report.get('session_key'), report.get('client')) != (current_report.get('session_key'), current_report.get('client')):
                    continue
            _report_html(report, 0)  # Validate nested fields before sorting/rendering the selected reports.
            candidates.append(report)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
    candidates.sort(key=lambda report: (report['created_at'], report.get('report_id', '')), reverse=True)
    notice = f'<p class="fine">Unreadable or unsupported evaluation reports: {unreadable}</p>' if unreadable else ''
    if not candidates:
        return '<section class="panel" id="agent-evaluations"><h2>Agent Evaluations</h2><p class="muted">No evaluations recorded for this session or repository.</p>' + notice + '</section>'
    options = []
    for i, report in enumerate(candidates):
        scope = 'Session' if report.get('session_key') else 'Repository'
        options.append(f'<option value="evaluation-{i}">{_escape(report["created_at"])} | {scope} | {report["runs"]} runs</option>')
    if current_report and not any(report.get('session_key') for report in candidates):
        notice += '<p class="fine">No evaluation is linked to the selected session. Showing repository evaluations.</p>'
    return f'''<section class="panel" id="agent-evaluations">
      <div class="toolbar"><h2>Agent Evaluations</h2><label>Report <select id="evaluation-select">{''.join(options)}</select></label></div>
      {notice}{''.join(_report_html(report, i) for i, report in enumerate(candidates))}</section>'''
