"""Portable setup and ordinary-work reporting, without client/model launches."""
import json
import tomllib

import anyio
import pytest
from click.testing import CliRunner
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ibwd.cli import main
from ibwd.setup import setup_project, plan_setup, skill_path, skill_source, TOOLS
from ibwd.telemetry import read_events
from ibwd.usage_evidence import retrieval_evidence
from ibwd.usage_hooks import capture_session, refresh_comparison
from tests.test_usage_hooks import transcript


@pytest.mark.parametrize('client', ['codex', 'claude'])
def test_fresh_setup_update_and_transport_evidence(tmp_path, client):
    repo = tmp_path / "project with 'quotes'"
    repo.mkdir()
    source = repo / 'app.py'
    source.write_text('def finish(): return 1\n')
    result = setup_project(repo, client)
    assert result['readiness']['status'] == 'ready'
    assert result['readiness']['runtime']['python']
    route = repo / result['readiness']['clients'][client]['routing_file']
    route.write_text('User instructions\n' + route.read_text() + '\nUser suffix\n')
    skill = repo / skill_path(client)
    skill.write_text('<!-- ibwd:managed-skill:v1 -->\nold bundled skill\n')
    hooks_path = repo / ('.codex/hooks.json' if client == 'codex' else '.claude/settings.local.json')
    hooks = json.loads(hooks_path.read_text())
    hooks['user_setting'] = {'keep': True}
    hooks['hooks']['Stop'].append({'hooks': [{'type': 'command', 'command': 'user-command'}]})
    hooks_path.write_text(json.dumps(hooks))
    setup_project(repo, client)
    assert skill.read_text() == skill_source()
    assert route.read_text().startswith('User instructions\n')
    assert route.read_text().endswith('\nUser suffix\n')
    assert json.loads(hooks_path.read_text()) == hooks
    assert plan_setup(repo, client) == {}
    config = (tomllib.loads((repo / '.codex/config.toml').read_text())['mcp_servers']['ibwd']
              if client == 'codex' else json.loads((repo / '.mcp.json').read_text())['mcpServers']['ibwd'])

    async def exercise():
        with anyio.fail_after(30):
            async with stdio_client(StdioServerParameters(**config, cwd=str(tmp_path))) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    assert {t.name for t in (await session.list_tools()).tools} == TOOLS
                    source.write_text('def changed(): return 2\n')
                    response = await session.call_tool('ibwd_context', {'task': 'changed', 'semantic': True})
                    assert not getattr(response, 'is_error', getattr(response, 'isError', False))
                    oid = response.meta['ibwd']['observation_id']
                    event = next(e for e in read_events(repo) if e['observation_id'] == oid and e['phase'] == 'completed')
                    assert event['freshness'] == 'validated' and event['refreshed'] is True
                    assert event['semantic_status'] == 'fallback'
                    assert event['embedding']['mode'] == 'fallback'
                    assert not event['embedding']['inference_attempted']
                    assert event['embedding']['elapsed_ms'] >= 0
                    assert event['embedding_attempts'] == [event['embedding']]
                    assert event['evidence_files'] >= 1
                    assert event['result_count'] >= 1 and event['response_bytes'] > 0
                    assert event['index_generation']
                    assert 'reason' not in event and 'changed' not in json.dumps(event)
                    await session.call_tool('ibwd_find_symbol', {'name': 'changed'})
                    next_event = read_events(repo)[-1]
                    assert next_event['freshness'] == 'validated' and next_event['refreshed'] is False
                    assert next_event['result_count'] == 1 and next_event['index_generation']
                    assert 'semantic_status' not in next_event
                    assert 'embedding' not in next_event
                    await session.call_tool('ibwd_context', {'task': 'changed', 'semantic': False})
                    disabled = read_events(repo)[-1]
                    assert disabled['embedding']['mode'] == 'disabled'
                    await session.call_tool('ibwd_read', {'symbol_id_or_path': 'app.py', 'expected_hash': 'wrong'})
                    failed_event = read_events(repo)[-1]
                    assert failed_event['status'] == 'error'
                    assert 'freshness' not in failed_event
                    return oid

    oid = anyio.run(exercise)
    path, hook = transcript(repo, client)
    response = {'isError': False, '_meta': {'ibwd': {'schema_version': 1, 'observation_id': oid}}}
    if client == 'codex':
        completion = {'type': 'response_item', 'payload': {'type': 'function_call_output',
                      'call_id': 'call-1', 'output': json.dumps(response)}}
    else:
        completion = {'type': 'user', 'message': {'content': [{'type': 'tool_result',
                      'tool_use_id': 'call-1', 'content': json.dumps(response)}]}}
    with path.open('a') as stream:
        stream.write(json.dumps(completion) + '\n')
    report = json.loads(capture_session(hook, client, repo).read_text())
    assert report['server_evidence']['linked_requests'] == 1
    assert report['server_evidence']['freshness_validated'] == 1
    assert report['server_evidence']['semantic_fallbacks'] == 1
    assert report['server_evidence']['embedding']['modes'] == {'fallback': 1}
    comparison = json.loads((repo / '.ibwd/usage/comparison.json').read_text())
    assert comparison['groups'][0]['retrieval_evidence']['freshness_validated'] == 1


def test_evidence_pending_legacy_errors_and_duplicates_remain_visible():
    pending = {'observation_id': 'a', 'phase': 'invoked', 'tool': 'ibwd_context'}
    complete = pending | {'phase': 'completed', 'status': 'success', 'freshness': 'validated',
                          'result_count': 2, 'response_bytes': 300, 'duration_ms': 4,
                          'evidence_files': 1, 'truncated': True, 'semantic_status': 'fallback'}
    error = pending | {'observation_id': 'b', 'phase': 'completed', 'status': 'error'}
    legacy = pending | {'observation_id': 'c', 'phase': 'completed', 'status': 'success'}
    scan = pending | {'observation_id': 'scan', 'tool': 'ibwd_scan'}
    result = retrieval_evidence([pending, complete, pending, complete, error, legacy,
                                 pending | {'observation_id': 'd'}, scan])
    assert result['linked_requests'] == 5
    assert result['retrieval_requests'] == 4 and result['completed_retrievals'] == 3
    assert result['freshness_validated'] == 1 and result['freshness_unknown'] == 3
    assert result['errors'] == result['semantic_fallbacks'] == result['truncated_responses'] == 1
    assert result['returned_items'] == 2 and result['item_count_known_requests'] == 1


def test_labels_persist_until_transcript_changes_and_comparisons_keep_failures(tmp_path):
    path, event = transcript(tmp_path)
    saved = capture_session(event, 'codex', tmp_path)
    report = json.loads(saved.read_text())
    result = CliRunner().invoke(main, ['usage-label', report['session_key'], '--client', 'codex',
        '--repo', str(tmp_path), '--outcome', 'failed', '--task-kind', 'debugging', '--rework', 'yes'])
    assert result.exit_code == 0, result.output
    capture_session(event, 'codex', tmp_path)
    report = json.loads(saved.read_text())
    assert report['outcome'] == 'failed' and report['rework'] == 'yes'
    assert report['label_evidence'] == {'source': 'user_reported', 'status': 'current'}
    comparison = json.loads((saved.parent.parent / 'comparison.json').read_text())
    assert comparison['groups'][0]['outcomes'] == {'failed': 1}
    assert comparison['groups'][0]['rework'] == {'yes': 1}
    assert comparison['groups'][0]['missing_token_totals'] == 1
    with path.open('a') as stream:
        stream.write('{"type":"new_activity"}\n')
    capture_session(event, 'codex', tmp_path)
    report = json.loads(saved.read_text())
    assert report['outcome'] == 'unknown' and report['label_evidence']['status'] == 'stale'
    assert report['provisional']


def test_comparison_separates_projects_and_keeps_unreadable_reports(tmp_path):
    _, event = transcript(tmp_path)
    saved = capture_session(event, 'codex', tmp_path)
    report = json.loads(saved.read_text())
    report.update(project_key='another-project', outcome='incomplete')
    (saved.parent / 'other.json').write_text(json.dumps(report))
    (saved.parent / 'broken.json').write_text('[]')
    refresh_comparison(saved.parent.parent, [])
    result = json.loads((saved.parent.parent / 'comparison.json').read_text())
    assert result['unreadable_reports'] == 1
    assert len(result['groups']) == 2
    assert any(row['outcomes'] == {'incomplete': 1} for row in result['groups'])
