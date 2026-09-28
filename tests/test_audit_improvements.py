"""Audit regressions, deterministic local adapters, and observable accounting."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.mcp.server import mcp, ibwd_find_files, ibwd_find_symbol
from ibwd.retrieval.context import context
from ibwd.retrieval import semantic, local_helper
from ibwd.retrieval.durable import retrieve
from ibwd.scan import run_scan
from ibwd.scanner.filesystem import scan_files
from ibwd.local_io import report_lock
from ibwd.usage_evidence import token_components
from tests.test_sprint6 import setup


def test_noop_scan_preserves_semantics_cursors_and_database(setup, monkeypatch):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    monkeypatch.chdir(repo)
    first = ibwd_find_files(response_version=2, limit=1)
    database = (repo / '.ibwd/graph.db').read_bytes()
    before = context(repo, 'save', semantic=True)
    scan = run_scan(repo)
    assert scan['index_generation'] == first['index_generation']
    assert scan['added'] == scan['changed'] == scan['removed'] == 0
    assert (repo / '.ibwd/graph.db').read_bytes() == database
    assert ibwd_find_files(response_version=2, limit=1, cursor=first['next_cursor'])['items']
    assert context(repo, 'save', semantic=True)['semantic']['status'] == before['semantic']['status'] == 'ready'


def test_test_symbol_roundtrip_and_scope(tmp_path, monkeypatch):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_app.py').write_text('def test_work():\n    assert True\n')
    monkeypatch.chdir(tmp_path)
    found = ibwd_find_symbol('test_work', scope='test', response_version=2)
    target = found['items'][0]['symbol_id']
    result = context(tmp_path, 'verification', targets=[target], scopes=['test'])
    assert not result['unresolved_targets']
    assert result['items'][0]['symbol_id'] == target
    assert result['items'][0]['reason'] == 'exact'
    assert result['items'][0]['definitions'][0]['symbol_id'] == target
    assert context(tmp_path, 'verification', targets=[target], scopes=['source'])['unresolved_targets'] == [target]


@pytest.mark.parametrize('name,args,message', [
    ('ibwd_find_files', {'response_version': 2, 'cursor': 'bad'}, 'cursor'),
    ('ibwd_context', {'task': 'x', 'budget_tokens': 1}, 'budget_tokens'),
    ('ibwd_impact', {'targets': ['x'], 'depth': 99}, 'depth'),
    ('ibwd_artifact_read', {'artifact_id': '../bad'}, 'artifact_id'),
])
def test_expected_errors_survive_mcp_boundary(tmp_path, monkeypatch, name, args, message):
    monkeypatch.chdir(tmp_path)
    async def call():
        with pytest.raises(Exception, match=message) as error:
            await mcp.call_tool(name, args)
        assert type(error.value).__name__ == 'ToolError'
    asyncio.run(call())


def test_streaming_fingerprints_detect_same_size_edit_and_rename(tmp_path):
    file = tmp_path / 'large.bin'
    file.write_bytes(b'x' * (2 * 1024 * 1024))
    with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')):
        first = scan_files(tmp_path)
        with patch.object(Path, 'open', side_effect=AssertionError('unchanged content reread')):
            assert scan_files(tmp_path) == first
    old = file.stat()
    file.write_bytes(b'y' * old.st_size)
    os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
    second = scan_files(tmp_path)
    assert second[0].content_hash != first[0].content_hash
    file.rename(tmp_path / 'renamed.bin')
    assert scan_files(tmp_path)[0].path == 'renamed.bin'


def test_query_inference_releases_graph_lock_and_caches_vector(setup, monkeypatch):
    repo, model, calls = setup
    semantic.build(repo, model, 2)
    calls.clear()
    original = semantic.embed
    def checked(*args, **kwargs):
        with report_lock(repo / '.ibwd/index.lock', timeout=0):
            pass
        return original(*args, **kwargs)
    monkeypatch.setattr(semantic, 'embed', checked)
    assert context(repo, 'save', semantic=True)['semantic']['status'] == 'ready'
    assert context(repo, 'save', semantic=True)['semantic']['status'] == 'ready'
    assert len(calls) == 1


def test_model_fingerprint_cache_invalidates_weights(setup):
    _, model, _ = setup
    first = semantic.model_digest(model)
    with patch.object(Path, 'open', side_effect=AssertionError('unchanged weights reread')):
        assert semantic.model_digest(model) == first
    (model / 'weights').write_bytes(b'new weights')
    assert semantic.model_digest(model) != first


@pytest.fixture
def provider(tmp_path, monkeypatch):
    local_helper.configure(tmp_path, True, 'fixture:local')
    state = {'ids': [1, 0], 'posts': 0, 'digest': 'a' * 64}
    def handler(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'fixture:local', 'digest': state['digest'],
                                                       'remote_host': state.get('remote_host')}]})
        state['posts'] += 1
        assert request.url.path == '/api/chat'
        body = json.loads(request.content)
        assert body['stream'] is False and body['format']['required'] == ['ids']
        return httpx.Response(200, json={'message': {'content': json.dumps({'ids': state['ids']})}})
    client = httpx.Client
    monkeypatch.setattr(local_helper.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    return state


def test_helper_validates_and_caches_by_model_and_evidence(tmp_path, provider):
    ids, status = local_helper.select(tmp_path, 'task', ['a', 'b'])
    assert ids == [1, 0] and status['status'] == 'ready' and status['inference_attempted']
    assert local_helper.select(tmp_path, 'task', ['a', 'b'])[1]['cache_hit']
    assert provider['posts'] == 1
    local_helper.select(tmp_path, 'task', ['changed', 'b'])
    assert provider['posts'] == 2
    provider['digest'] = 'b' * 64
    local_helper.select(tmp_path, 'task', ['changed', 'b'])
    assert provider['posts'] == 3


@pytest.mark.parametrize('ids', [[99], [True], [0, 0], [], 'invented'])
def test_invalid_helper_ids_use_deterministic_fallback(tmp_path, provider, ids):
    provider['ids'] = ids
    selected, status = local_helper.select(tmp_path, 'task', ['a', 'b'])
    assert selected == [0, 1] and status['status'] == 'fallback'


def test_helper_disabled_never_connects(tmp_path, monkeypatch):
    monkeypatch.setattr(local_helper.httpx, 'Client', lambda **kw: pytest.fail('unexpected connection'))
    assert local_helper.select(tmp_path, 'task', ['a'])[1]['status'] == 'disabled'
    result = CliRunner().invoke(main, ['helper-config', '--repo', str(tmp_path), '--enabled', '--model', 'fixture'])
    assert result.exit_code == 0


@pytest.mark.parametrize('endpoint', ['https://example.com', 'http://192.168.1.1:11434', 'http://user@127.0.0.1:11434', 'http://127.0.0.1/proxy'])
def test_helper_rejects_nonlocal_endpoints(tmp_path, endpoint):
    with pytest.raises(ValueError):
        local_helper.configure(tmp_path, True, 'fixture', endpoint)


def test_helper_timeout_fallback(tmp_path, provider, monkeypatch):
    monkeypatch.setattr(local_helper, '_request', lambda *a, **k: (_ for _ in ()).throw(httpx.ReadTimeout('private details')))
    selected, status = local_helper.select(tmp_path, 'task', ['a', 'b'])
    assert selected == [0, 1] and status['status'] == 'fallback'
    assert 'private' not in json.dumps(status)


def test_remote_alias_is_rejected_before_inference(tmp_path, provider):
    provider['remote_host'] = 'https://example.com'
    assert local_helper.select(tmp_path, 'task', ['a'])[1]['status'] == 'fallback'
    assert provider['posts'] == 0


def test_context_helper_keeps_exact_targets_and_revalidates(tmp_path, provider, monkeypatch):
    for name in ('a', 'b', 'c'):
        (tmp_path / f'{name}.py').write_text(f'def {name}():\n    return "topic"\n')
    original = local_helper.select
    def unlocked(*args, **kwargs):
        with report_lock(tmp_path / '.ibwd/index.lock', timeout=0):
            pass
        return original(*args, **kwargs)
    monkeypatch.setattr(local_helper, 'select', unlocked)
    packet = context(tmp_path, 'topic', targets=['a.py::a'], budget_tokens=8000, max_bytes=65536)
    assert packet['items'][0]['symbol_id'] == 'a.py::a'
    assert [i['file'] for i in packet['items']] == ['a.py', 'c.py', 'b.py']
    assert packet['local_helper']['status'] == 'ready'
    def edited(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / 'c.py').write_text('def fresh(): pass\n')
        return result
    monkeypatch.setattr(local_helper, 'select', edited)
    packet = context(tmp_path, 'topic', targets=['a.py::a'], budget_tokens=8000, max_bytes=65536)
    assert packet['local_helper']['status'] == 'fallback'
    assert 'c.py' not in [i['file'] for i in packet['items']]


def test_assisted_summary_tracks_omitted_dependencies(tmp_path, provider):
    provider['ids'] = [0]
    for name in ('a', 'b'):
        (tmp_path / f'{name}.py').write_text(f'def {name}(): pass\n')
    packet = context(tmp_path, 'a b', targets=['a.py', 'b.py'], helper=False, budget_tokens=8000, max_bytes=65536)
    payload = dict(evidence=[dict(file=f'{n}.py', hash=packet['files'][f'{n}.py'], range=[1, 1]) for n in ('a', 'b')], dependencies=[])
    result = local_helper.assist(tmp_path, 'summary', payload)
    assert len(result['artifact']['extracts']) == 1
    assert result['artifact']['files'].keys() == {'a.py', 'b.py'}
    (tmp_path / 'b.py').write_text('def changed(): pass\n')
    assert retrieve(tmp_path, result['artifact_id'])['status'] == 'stale'


def test_assist_budget_and_concurrent_handoff_edits_cannot_publish(tmp_path, provider, monkeypatch):
    (tmp_path / 'a.py').write_text('value = 1\n')
    payload = dict(goal='task', user_decisions=[], changed_paths=['a.py'], commands=[],
                   unresolved_questions=['first', 'second'], evidence=[])
    with pytest.raises(ValueError, match='Budget'):
        local_helper.assist(tmp_path, 'handoff', payload, max_bytes=256)
    assert not (tmp_path / '.ibwd/durable').exists()
    original = local_helper.select
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / 'a.py').write_text('value = 2\n')
        return result
    monkeypatch.setattr(local_helper, 'select', changed)
    with pytest.raises(ValueError, match='Evidence changed'):
        local_helper.assist(tmp_path, 'handoff', payload)
    assert not (tmp_path / '.ibwd/durable').exists()


def test_log_preserves_diagnostics_exit_code_and_raw_output(tmp_path, provider):
    provider['ids'] = [0]
    lines = ['progress'] * 60 + ['FAILED tests/test_app.py:14', 'assert 1 == 2', 'details'] + ['done'] * 20
    raw = '\n'.join(lines)
    result = local_helper.assist(tmp_path, 'log', dict(text=raw, command='pytest', exit_code=1))
    assert result['exit_code'] == 1
    assert {'FAILED tests/test_app.py:14', 'assert 1 == 2'} <= {i['text'] for i in result['lines']}
    assert (tmp_path / result['raw_log']).read_text() == raw
    assert result['omitted_lines'] > 0


def test_handoff_preserves_supplied_decisions_and_commands(tmp_path, provider):
    payload = dict(goal='finish', user_decisions=['Keep API'], changed_paths=[], commands=[
        dict(command='touch should-not-exist', exit_code=0, result='caller reported')],
        unresolved_questions=['first', 'second'], evidence=[])
    result = local_helper.assist(tmp_path, 'handoff', payload)
    saved = result['artifact']['payload']
    assert saved['user_decisions'] == payload['user_decisions']
    assert saved['commands'] == payload['commands']
    assert saved['unresolved_questions'] == ['second', 'first']
    assert not (tmp_path / 'should-not-exist').exists()


def test_token_components_keep_missing_unknown_and_cache_separate():
    codex = token_components(dict(client='codex', usage=dict(input_tokens=100, cached_input_tokens=70, output_tokens=5)))
    assert codex == dict(uncached_input=30, cached_input=70, cache_creation=None, output=5)
    claude = token_components(dict(client='claude', usage=dict(input_tokens=30, cache_read_input_tokens=70,
                                                              cache_creation_input_tokens=10, output_tokens=5)))
    assert claude == dict(uncached_input=30, cached_input=70, cache_creation=10, output=5)
    assert all(v is None for v in token_components({'client': 'codex'}).values())


def test_resident_worker_reuses_runtime_and_recovers_after_timeout(tmp_path, monkeypatch):
    from ibwd.retrieval import embedding_worker
    stub = '''
import os, time
from pathlib import Path
class SentenceTransformer:
    max_seq_length = 512
    def __init__(self, path, **kwargs):
        assert kwargs['local_files_only'] and not kwargs['trust_remote_code']
        assert os.environ['HF_HUB_OFFLINE'] == '1'
        with (Path(path) / 'loads').open('a') as out:
            out.write('loaded\\n')
    def get_sentence_embedding_dimension(self): return 2
    def encode(self, texts, **kwargs):
        if texts == ['slow']: time.sleep(2)
        class Result:
            def tolist(self): return [[0.6, 0.8]]
        return Result()
'''
    (tmp_path / 'sentence_transformers.py').write_text(stub)
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    embedding_worker.close()
    try:
        for task in ('first', 'second'):
            assert embedding_worker.encode(tmp_path, 'digest', task, 2, 5) == [[0.6, 0.8]]
        assert (tmp_path / 'loads').read_text().splitlines() == ['loaded']
        with pytest.raises(ValueError, match='deadline'):
            embedding_worker.encode(tmp_path, 'digest', 'slow', 2, 0.1)
        assert embedding_worker._process is None
        assert embedding_worker.encode(tmp_path, 'digest', 'recovered', 2, 5) == [[0.6, 0.8]]
        assert (tmp_path / 'loads').read_text().splitlines() == ['loaded', 'loaded']
    finally:
        embedding_worker.close()


def test_dashboard_exposes_cohort_dimensions_and_missing_usage(tmp_path):
    from ibwd.usage_dashboard import build_dashboard
    from tests.test_usage_hooks import transcript
    from ibwd.usage_hooks import capture_session
    log, event = transcript(tmp_path)
    path = capture_session(event, 'codex', tmp_path)
    report = json.loads(path.read_text())
    report.update(models=['model-alpha'], efforts=['high'], client_versions=['9.1'], provisional=True,
                  usage=dict(input_tokens=100, cached_input_tokens=70, output_tokens=5, total_tokens=105))
    path.write_text(json.dumps(report))
    second = dict(report, session_key='second', models=['model-beta'], usage=None)
    (path.parent / 'second.json').write_text(json.dumps(second))
    text = build_dashboard(tmp_path, refresh=False).read_text()
    for label in ('model-alpha', 'model-beta', 'high', '9.1', 'Snapshots', 'Missing usage',
                  'Uncached input', 'Cached input', 'Cache creation', 'Output', 'Latest saved session'):
        assert label in text
    comparison = json.loads((path.parent.parent / 'comparison.json').read_text())
    assert len(comparison['groups']) == 2
    assert sum(g['missing_token_totals'] for g in comparison['groups']) == 1
    assert 'Selected saved session' in build_dashboard(tmp_path, session_key='second', refresh=False).read_text()
