"""Freshness, publication failure, bounded retrieval and actual MCP contracts."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ibwd.health import inspect_index
from ibwd.scan import run_scan
from ibwd.mcp.server import ibwd_find_symbol, ibwd_find_files, ibwd_dependents, ibwd_trace_path
from ibwd.retrieval.bounded import encoded_size
from tests.test_incremental_equivalence import BASE, MUTATIONS, _write, _edges


def test_freshness_add_edit_delete_ignore_and_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    file = tmp_path / 'a.py'
    file.write_text('def before(): pass\n')
    first = ibwd_find_symbol('before', response_version=2)
    assert first['items'][0]['end_line'] == 1
    assert first['files']['a.py']
    file.write_text('def after(): pass\n')
    second = ibwd_find_symbol('after', response_version=2)
    assert second['index_generation'] != first['index_generation']
    assert not ibwd_find_symbol('before', response_version=2)['items']
    (tmp_path / 'new.py').write_text('def extra(): pass\n')
    assert ibwd_find_symbol('extra', response_version=2)['items']
    (tmp_path / '.gitignore').write_text('new.py\n')
    assert not ibwd_find_symbol('extra', response_version=2)['items']
    file.unlink()
    assert not ibwd_find_symbol('after', response_version=2)['items']


@pytest.mark.parametrize('mutate', MUTATIONS, ids=lambda f: f.__name__)
def test_automatic_refresh_equals_fresh_graph_after_all_mutations(tmp_path, monkeypatch, mutate):
    import shutil
    incremental, fresh = tmp_path / 'incremental', tmp_path / 'fresh'
    _write(incremental, BASE)
    monkeypatch.chdir(incremental)
    ibwd_find_files(response_version=2)
    mutate(incremental)
    ibwd_find_files(response_version=2)
    shutil.copytree(incremental, fresh, ignore=shutil.ignore_patterns('.ibwd'))
    run_scan(fresh)
    assert _edges(incremental) == _edges(fresh)


def test_nested_ignore_and_ignored_extends_config_are_fresh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/a.ts').write_text("import {b} from '@/b'; export function a() { b(); }\n")
    (tmp_path / 'src/b.ts').write_text('export function b() {}\n')
    (tmp_path / 'tsconfig.json').write_text('{"extends":"./hidden.json"}')
    (tmp_path / '.gitignore').write_text('hidden.json\n')
    (tmp_path / 'hidden.json').write_text('{"compilerOptions":{"paths":{"@/*":["src/*"]}}}')
    assert ibwd_dependents('a', response_version=2)['items'][0]['name'] == 'b'
    (tmp_path / 'hidden.json').write_text('{"compilerOptions":{"paths":{"@/*":["missing/*"]}}}')
    assert not ibwd_dependents('a', response_version=2)['items']
    (tmp_path / 'src/.gitignore').write_text('b.ts\n')
    assert not ibwd_find_symbol('b', response_version=2)['items']


def test_branch_switch_refreshes_without_clean_git_assumptions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init', '-b', 'one')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'a.py').write_text('def one(): pass\n')
    git('add', 'a.py')
    git('commit', '-m', 'one')
    assert ibwd_find_symbol('one', response_version=2)['items']
    git('switch', '-c', 'two')
    (tmp_path / 'a.py').write_text('def two(): pass\n')
    git('add', 'a.py')
    git('commit', '-m', 'two')
    assert ibwd_find_symbol('two', response_version=2)['items']
    git('switch', 'one')
    assert ibwd_find_symbol('one', response_version=2)['items']
    assert not ibwd_find_symbol('two', response_version=2)['items']


def test_pagination_byte_budget_and_cursor_binding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for n in range(15):
        (tmp_path / f'file{n:02}.py').write_text(f'def unique_{n}(): pass\n')
    first = ibwd_find_files(response_version=2, limit=3, max_bytes=3000)
    assert first['truncated'] and first['next_cursor']
    seen = list(first['items'])
    cursor = first['next_cursor']
    while cursor:
        page = ibwd_find_files(response_version=2, limit=3, max_bytes=3000, cursor=cursor)
        assert encoded_size(page) <= 3000
        seen.extend(page['items'])
        cursor = page['next_cursor']
    assert len(seen) == len({r['path'] for r in seen}) == 15
    with pytest.raises(ValueError, match='mismatched cursor'):
        ibwd_find_files(kind='source', response_version=2, cursor=first['next_cursor'])
    (tmp_path / 'new.py').write_text('pass\n')
    with pytest.raises(ValueError, match='stale'):
        ibwd_find_files(response_version=2, cursor=first['next_cursor'])
    with pytest.raises(ValueError, match='budget'):
        ibwd_find_files(response_version=2, max_bytes=256)


def test_graph_bounds_preserve_relations_and_never_claim_no_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'a.py').write_text('def a():\n    b()\n    return b\ndef b():\n    c()\ndef c(): pass\n')
    reached = ibwd_dependents('a', depth=2, response_version=2)
    assert reached['items'][0]['relations'] == ['CALLS', 'REFERENCES']
    assert reached['items'][0]['symbol_id'] == 'a.py::b'
    path = ibwd_trace_path('a', 'c', edge_types=['CALLS', 'REFERENCES'], response_version=2)
    assert path['items'][0]['path'][1]['edge_types'] == ['CALLS', 'REFERENCES']
    monkeypatch.setattr('ibwd.retrieval.bounded.MAX_EDGES', 1)
    limited = ibwd_trace_path('a', 'c', response_version=2)
    assert limited['truncated'] and limited['limit_reason'] == 'visited_edges'
    assert limited['items'] == [] and limited['next_cursor'] is None


def test_dense_cyclic_graph_is_bounded_and_node_time_limits_are_explicit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = ''.join(f'def n{i}():\n' + ''.join(f'    n{j}()\n' for j in range(40)) for i in range(40))
    (tmp_path / 'dense.py').write_text(source)
    monkeypatch.setattr('ibwd.retrieval.bounded.MAX_EDGES', 50)
    limited = ibwd_dependents('n0', depth=5, response_version=2)
    assert limited['limit_reason'] == 'visited_edges' and limited['truncated']
    monkeypatch.setattr('ibwd.retrieval.bounded.MAX_NODES', 3)
    assert ibwd_dependents('n0', response_version=2)['limit_reason'] == 'visited_nodes'
    monkeypatch.setattr('ibwd.retrieval.bounded.MAX_SECONDS', -1)
    assert ibwd_dependents('n0', response_version=2)['limit_reason'] == 'elapsed_time'


def test_change_during_query_retries_once_and_rejects_stale_citations(tmp_path, monkeypatch):
    import ibwd.retrieval.service as service
    monkeypatch.chdir(tmp_path)
    file = tmp_path / 'a.py'
    file.write_text('def a(): pass\n')
    original = service.execute
    calls = 0
    def changing(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            file.write_text('\ndef a(): pass\n')
        return result
    monkeypatch.setattr(service, 'execute', changing)
    assert ibwd_find_symbol('a', response_version=2)['items'][0]['line'] == 2
    assert calls == 2
    def unstable(*args, **kwargs):
        result = original(*args, **kwargs)
        file.write_text('\n' + file.read_text())
        return result
    monkeypatch.setattr(service, 'execute', unstable)
    with pytest.raises(ValueError, match='twice'):
        ibwd_find_symbol('a', response_version=2)


@pytest.mark.parametrize('after_replace', [False, True])
def test_killed_publisher_leaves_previous_or_explicit_stale_state(tmp_path, monkeypatch, after_replace):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'a.py').write_text('def old(): pass\n')
    run_scan(tmp_path)
    (tmp_path / 'a.py').write_text('def fresh(): pass\n')
    script = '''
import os, sys
from pathlib import Path
import ibwd.scan as scan
original = scan.os.replace
def replace(source, target):
    if Path(target).name == 'graph.db':
        if sys.argv[2] == 'True': original(source, target)
        os._exit(73)
    return original(source, target)
scan.os.replace = replace
scan.run_scan(Path(sys.argv[1]))
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), str(after_replace)], timeout=20)
    assert result.returncode == 73
    assert inspect_index(tmp_path)['status'] == 'stale'
    assert ibwd_find_symbol('fresh', response_version=2)['items']
    assert inspect_index(tmp_path)['status'] == 'ready'


def test_two_mcp_processes_refresh_and_serialize_bounded_responses(tmp_path):
    (tmp_path / 'a.py').write_text('def a(): pass\n')
    async def exercise():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'ibwd.mcp.server', '--repo', str(tmp_path)])
        async with stdio_client(params) as (r1, w1), stdio_client(params) as (r2, w2):
            async with ClientSession(r1, w1) as one, ClientSession(r2, w2) as two:
                await asyncio.gather(one.initialize(), two.initialize())
                async def query(session):
                    result = await session.call_tool('ibwd_find_symbol', {'name': 'a', 'response_version': 2, 'max_bytes': 3000})
                    assert not result.is_error
                    wire = result.model_dump(mode='json', by_alias=True, exclude_none=True)
                    assert len(json.dumps(wire, ensure_ascii=False, separators=(',', ':')).encode()) <= 3000
                    payload = wire['structuredContent']
                    payload = payload.get('result', payload)
                    assert payload['items'][0]['symbol_id'] == 'a.py::a'
                    assert payload['index_generation']
                    return payload
                a, b = await asyncio.gather(query(one), query(two))
                assert a['index_generation'] == b['index_generation']
                (tmp_path / 'a.py').write_text('\ndef a(): pass\n')
                a, b = await asyncio.gather(query(one), query(two))
                assert a['items'][0]['line'] == b['items'][0]['line'] == 2
                assert a['index_generation'] == b['index_generation']
    asyncio.run(exercise())
