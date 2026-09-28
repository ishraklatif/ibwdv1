"""Realistic transcript wrappers and active-session refresh, without model calls."""
import json
import os
import signal
import subprocess
import sys
import time

from click.testing import CliRunner

from ibwd.cli import main
from ibwd.usage import analyze_log
from ibwd.usage_dashboard import build_dashboard
from ibwd.usage_hooks import capture_session
from ibwd.usage_live import refresh_reports, session_key
from ibwd.usage_observations import assessments, command_kind
from tests.test_usage import codex_usage


def append(path, *events):
    with path.open('a') as stream:
        for event in events:
            stream.write(json.dumps(event) + '\n')


def item(kind, **values):
    return dict(type='response_item', payload=dict(type=kind, **values))


def packet():
    return dict(schema_version=2, retrieval_version=2, index_generation='generation',
                items=[{'file': 'private.py'}], files={'private.py': 'hash'}, truncated=False,
                semantic={'status': 'disabled'})


def test_response_blocks_receipts_and_legacy_envelopes_are_recovered(tmp_path):
    path = tmp_path / 'session.jsonl'
    oid = 'a' * 32
    receipt = dict(schema_version=1, observation_id=oid, tool='ibwd_context')
    envelope = dict(content=[dict(type='text', text=json.dumps(packet())),
                             dict(type='text', text=json.dumps({'ibwd_observation': receipt}))], isError=False)
    append(path, dict(type='session_meta', payload=dict(id='one')),
           item('custom_tool_call', name='exec', call_id='outer', input='private execution source'),
           item('custom_tool_call_output', call_id='outer', output=[
               dict(type='input_text', text='Script completed'),
               dict(type='input_text', text=json.dumps(envelope))]),
           item('function_call', name='ibwd_context', call_id='direct'),
           item('function_call_output', call_id='direct', output=[
               dict(type='input_text', text=json.dumps(packet()))]))
    state = {}
    report = analyze_log(path, 'codex', state=state)
    assert report['observed_ibwd_calls'] == 2
    assert report['direct_ibwd_calls'] == 1
    assert report['server_observation_ids'] == [oid]
    assert report['response_evidence']['items'] == 2
    assert 'private.py' not in json.dumps(report)
    assert 'private execution source' not in json.dumps(state)
    # A printed sentence or an embedded source string is not a receipt.
    append(path, item('custom_tool_call', name='exec', call_id='prose'),
           item('custom_tool_call_output', call_id='prose', output='Example: ' + json.dumps(envelope)))
    assert analyze_log(path, 'codex')['observed_ibwd_calls'] == 2


def test_codex_completed_mcp_items_recover_receipts_and_deduplicate(tmp_path):
    path = tmp_path / 'session.jsonl'
    oid = 'b' * 32
    result = dict(content=[dict(type='text', text=json.dumps(packet()))],
                  _meta={'ibwd': dict(schema_version=1, observation_id=oid)}, isError=False)
    mcp_item = dict(type='McpToolCall', id='native-call', server='ibwd', tool='ibwd_context',
                    arguments={'task': 'private task'}, status='completed', result=result)
    started = dict(type='event_msg', payload=dict(type='item_started', thread_id='one', item=mcp_item))
    completed = dict(type='event_msg', payload=dict(type='item_completed', thread_id='one', item=mcp_item))
    append(path, dict(type='session_meta', payload=dict(id='one')), started, completed, completed,
           item('function_call', name='mcp__ibwd__ibwd_context', call_id='second-wrapper'),
           item('function_call_output', call_id='second-wrapper', output=result))
    state = {}
    report = analyze_log(path, 'codex', state=state)
    assert report['server_observation_ids'] == [oid]
    assert report['observed_ibwd_calls'] == 1
    assert report['response_evidence']['items'] == 1
    assert 'private task' not in json.dumps(state)
    assert 'private.py' not in json.dumps(state)
    append(path, dict(type='event_msg', payload=dict(type='item_completed', thread_id='child',
        item=dict(mcp_item, id='child-call', result=dict(_meta={'ibwd': dict(schema_version=1, observation_id='c' * 32)})))))
    assert analyze_log(path, 'codex')['server_observation_ids'] == [oid]


def test_item_receipts_are_recovered_after_checkpoint_upgrade(tmp_path):
    from ibwd.usage_stream import incremental_report, STATE_VERSION
    path = tmp_path / 'log.jsonl'
    checkpoint = tmp_path / 'checkpoint.json'
    append(path, dict(type='session_meta', payload=dict(id='one')),
           dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='McpToolCall',
               server='ibwd', tool='ibwd_read', id='call', result=dict(isError=True,
                   _meta={'ibwd': dict(schema_version=1, observation_id='d' * 32)})))))
    _, saved = incremental_report(path, 'codex', checkpoint)
    saved['version'] = STATE_VERSION - 1
    saved['parser']['calls'] = {}
    checkpoint.write_text(json.dumps(saved))
    report, _ = incremental_report(path, 'codex', checkpoint)
    assert not report['parser']['resumed']
    assert report['server_observation_ids'] == ['d' * 32]
    assert report['observations']['retrieval_errors']['value'] == 1


def test_refresh_discovers_exact_repo_and_reads_active_append_without_stop(tmp_path, monkeypatch):
    repo = tmp_path / 'project'
    repo.mkdir()
    home = tmp_path / 'client'
    sessions = home / 'sessions'
    sessions.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    path = sessions / 'session.jsonl'
    sid = 'live-session'
    append(path, dict(type='session_meta', payload=dict(id=sid, cwd=str(repo))),
           dict(type='event_msg', payload=dict(type='task_started')), codex_usage(100))
    other = sessions / 'unrelated.jsonl'
    append(other, dict(type='session_meta', payload=dict(id='other', cwd=str(tmp_path))), codex_usage(999))
    key = session_key('codex', sid)
    assert refresh_reports(repo, 'codex')['refreshed'] == 1
    saved = repo / f'.ibwd/usage/sessions/codex-{key}.json'
    first = json.loads(saved.read_text())
    assert first['usage']['total_tokens'] == 110
    assert assessments(first)['outcome']['value'] == 'Turn in progress'
    assert first['outcome'] == 'unknown'  # No invented task-success grade.
    append(path, codex_usage(200))
    result = CliRunner().invoke(main, ['usage-refresh', '--repo', str(repo), '--client', 'codex', '--json'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['usage']['total_tokens'] == 210
    append(path, codex_usage(300))
    page = build_dashboard(repo, 'codex', live_seconds=5).read_text()
    assert '310' in page and 'Turn in progress' in page
    assert 'Sessions Needing Labels' not in page
    assert 'location.reload()' in page
    assert json.loads(saved.read_text())['parser']['resumed']
    # A partial line preserves earlier counters and becomes readable after completion.
    with path.open('a') as stream:
        stream.write(json.dumps(codex_usage(400))[:-1])
    refresh_reports(repo, 'codex', key)
    report = json.loads(saved.read_text())
    assert report['usage']['total_tokens'] == 310 and report['parser']['pending_line']
    with path.open('a') as stream:
        stream.write('}\n')
    refresh_reports(repo, 'codex', key)
    assert json.loads(saved.read_text())['usage']['total_tokens'] == 410


def test_automatic_checks_and_labels_have_separate_meanings(tmp_path):
    path = tmp_path / 'session.jsonl'
    append(path, dict(type='session_meta', payload=dict(id='checks', cwd=str(tmp_path))),
           dict(type='event_msg', payload=dict(type='task_started')),
           item('function_call', name='exec_command', call_id='check', arguments=json.dumps({'cmd': 'pytest -q'})),
           item('function_call_output', call_id='check', output='Process exited with code 0\nOutput:\npassed'),
           item('custom_tool_call', name='apply_patch', call_id='edit', input='private source'),
           dict(type='event_msg', payload=dict(type='task_complete')),
           codex_usage(50))
    report = analyze_log(path, 'codex')
    auto = assessments(report)
    assert 'later edits not checked' in auto['validation']['value']
    assert 'outcome unverified' in auto['outcome']['value']
    assert auto['task_kind']['value'] == 'implementation'
    assert report['observations']['usage_incomplete']['value'] is False
    event = dict(hook_event_name='Stop', session_id='checks', cwd=str(tmp_path), transcript_path=str(path))
    saved = capture_session(event, 'codex', tmp_path)
    result = CliRunner().invoke(main, ['usage-label', report['session_key'], '--repo', str(tmp_path),
                                      '--client', 'codex', '--outcome', 'failed'])
    assert result.exit_code == 0
    report = json.loads(saved.read_text())
    assert assessments(report)['outcome']['value'] == 'failed'
    assert report['automatic_assessment']['outcome']['source'].startswith('Explicit user')


def test_mixed_models_do_not_claim_missing_token_counters(tmp_path):
    path = tmp_path / 'session.jsonl'
    append(path, dict(type='session_meta', payload=dict(id='one')),
           dict(type='turn_context', payload=dict(model='a', effort='low')),
           dict(type='turn_context', payload=dict(model='b', effort='high')), codex_usage(100))
    report = analyze_log(path, 'codex')
    assert not report['observations']['usage_incomplete']['value']
    assert not report['comparable']
    assert report['activity']['current_model'] == 'b'


def test_watch_updates_open_snapshot_and_stops_cleanly(tmp_path, monkeypatch):
    home = tmp_path / 'client'
    (home / 'sessions').mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME', str(home))
    path = home / 'sessions/session.jsonl'
    append(path, dict(type='session_meta', payload=dict(id='watch', cwd=str(tmp_path))), codex_usage(100))
    output = tmp_path / '.ibwd/usage/dashboard.html'
    process = subprocess.Popen([sys.executable, '-m', 'ibwd.cli', 'usage-dashboard', '--repo', str(tmp_path),
                                '--client', 'codex', '--watch', '--interval', '2', '--no-open'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        deadline = time.monotonic() + 10
        while not output.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert output.exists()
        append(path, codex_usage(200))
        while '210' not in output.read_text() and time.monotonic() < deadline:
            time.sleep(.05)
        assert '210' in output.read_text() and process.poll() is None
        process.send_signal(signal.SIGINT)
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert 'if (0 > 0)' in output.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_error_receipt_survives_flattened_text(tmp_path):
    path = tmp_path / 'session.jsonl'
    append(path, dict(type='session_meta', payload=dict(id='one')),
           item('function_call', name='ibwd_context', call_id='failure'),
           item('function_call_output', call_id='failure', output=[
               dict(type='input_text', text='Validation failed'),
               dict(type='input_text', text=json.dumps({'ibwd_observation': dict(
                   schema_version=1, observation_id='b'*32, tool='ibwd_context', status='error')}))]))
    report = analyze_log(path, 'codex')
    assert report['observations']['retrieval_errors']['value'] == 1
    assert report['server_observation_ids'] == ['b'*32]


def test_check_detection_requires_a_real_unpiped_check_command():
    assert command_kind('exec_command', {'cmd': '.venv/bin/python -m pytest -q'}) == 'check'
    for command in ['echo pytest', 'rg pytest tests', 'pytest -q | tee result', 'python -c "print(123)"']:
        assert command_kind('exec_command', {'cmd': command}) == 'command'
