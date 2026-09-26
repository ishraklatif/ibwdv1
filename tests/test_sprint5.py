"""Local deterministic Sprint 5 contracts; no models or benchmark sessions."""
import json
from pathlib import Path
import shutil

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.mcp.server import ibwd_find_symbol, ibwd_list_symbols, ibwd_callers
from ibwd.retrieval.impact import impact
from ibwd.retrieval.compiler import compiler_evidence
from ibwd.retrieval.context import read
from ibwd.retrieval.bounded import encoded_size
from ibwd.scan import run_scan


def put(root, path, text):
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    put(tmp_path, 'core.py', 'def work(value):\n    return value\n')
    put(tmp_path, 'app.py', 'from core import work as run\ndef use():\n    return run(1)\n')
    put(tmp_path, 'tests/test_core.py', 'from app import use\ndef test_use():\n    assert use() == 1\n')
    put(tmp_path, 'tests/test_other.py', 'from core import work\ndef test_registration():\n    register(work)\n')
    put(tmp_path, 'tests/test_app.py', 'def test_smoke():\n    pass\n')
    monkeypatch.chdir(tmp_path)
    run_scan(tmp_path)
    return tmp_path


def test_production_defaults_and_explicit_test_discovery(repo):
    assert ibwd_find_symbol('test_use') == []
    assert ibwd_find_symbol('test_use', response_version=2)['items'] == []
    found = ibwd_find_symbol('test_use', response_version=2, scope='test')
    assert [i['symbol_id'] for i in found['items']] == ['tests/test_core.py::test_use']
    assert ibwd_list_symbols('tests/test_core.py') == []
    assert ibwd_list_symbols('tests/test_core.py', response_version=2, scope='all')['items']
    body = read(repo, found['items'][0]['symbol_id'], found['files']['tests/test_core.py'])
    assert 'assert use()' in body['items'][0]['text']
    assert {i['file'] for i in ibwd_callers('work')} == {'app.py'}
    assert {i['file'] for i in ibwd_callers('work', response_version=2)['items']} == {'app.py'}


def test_paths_relations_and_test_relevance(repo):
    result = impact(repo, ['core.py::work'], depth=2, max_bytes=65536)
    test = next(i for i in result['items'] if i['symbol_id'] == 'tests/test_core.py::test_use')
    assert test['relevance'] == 'reference' and test['coverage'] == 'unknown'
    assert [e['relation'] for e in test['evidence_path']] == ['CALLS', 'CALLS']
    assert test['evidence_path'][0]['target']['symbol_id'] == 'core.py::work'
    registered = next(i for i in result['items'] if i['symbol_id'] == 'tests/test_other.py::test_registration')
    assert registered['evidence_path'][0]['relation'] == 'REFERENCES'
    calls = impact(repo, ['core.py::work'], relations=['CALLS'], heuristics=False, max_bytes=65536)
    assert not any(i['file'] == 'tests/test_other.py' for i in calls['items'])
    outgoing = impact(repo, ['app.py::use'], direction='outgoing', heuristics=False)
    assert [i['symbol_id'] for i in outgoing['items']] == ['core.py::work']
    heuristic = impact(repo, ['app.py'], max_bytes=65536)
    assert any(i['file'] == 'tests/test_app.py' and i['relevance'] == 'filename_heuristic' for i in heuristic['items'])
    tests = impact(repo, ['core.py::work'], scopes=['test'], max_bytes=65536)
    assert all(i['scope'] == 'test' for i in tests['items'])
    assert any(i['distance'] == 2 for i in tests['items'])  # traverses production intermediates


def test_deletion_and_changed_signatures_keep_both_snapshots(repo):
    put(repo, 'core.py', 'def replacement(value, extra):\n    return value + extra\n')
    result = impact(repo, diff=True, max_bytes=65536)
    changed = {(i['snapshot'], i['symbol_id']) for i in result['items'] if i['relevance'] == 'changed_identity'}
    assert ('previous', 'core.py::work') in changed
    assert ('scoped', 'core.py::replacement') in changed
    assert any(i.get('of') == 'core.py::work' and i['symbol_id'] == 'app.py::use' for i in result['items'])
    assert result['files']['previous']['core.py'] != result['files']['scoped']['core.py']
    tests_only = impact(repo, diff=True, scopes=['test'], max_bytes=65536)
    assert tests_only['items'] and all(i['scope'] == 'test' for i in tests_only['items'])
    run_scan(repo)  # no-op scan must not erase the old graph
    assert impact(repo, diff=True, max_bytes=65536)['items'] == result['items']
    (repo / 'core.py').unlink()
    deleted = impact(repo, diff=True, max_bytes=65536)
    assert any(i['snapshot'] == 'previous' and i['symbol_id'] == 'core.py::replacement' for i in deleted['items'])
    assert not any(i['snapshot'] == 'scoped' and i['file'] == 'core.py' for i in deleted['items'])


def test_diff_needs_baseline(tmp_path):
    put(tmp_path, 'a.py', 'def run(): pass\n')
    with pytest.raises(ValueError, match='previous indexed snapshot'):
        impact(tmp_path, diff=True)


def test_pages_hashes_and_stale_cursors(repo):
    full = impact(repo, ['core.py'], max_bytes=65536)
    result = impact(repo, ['core.py'], limit=1)
    cursor = result['next_cursor']
    items = list(result['items'])
    while result['next_cursor']:
        assert encoded_size(result) <= 16384
        result = impact(repo, ['core.py'], limit=1, cursor=result['next_cursor'])
        items.extend(result['items'])
    assert items == full['items']
    put(repo, 'app.py', 'from core import work\ndef use(): return work(2)\n')
    with pytest.raises(ValueError, match='stale'):
        impact(repo, ['core.py'], limit=1, cursor=cursor)
    with pytest.raises(ValueError, match='Budget'):
        impact(repo, ['core.py'], max_bytes=256)


def test_work_limit_is_explicit(repo, monkeypatch):
    monkeypatch.setattr('ibwd.retrieval.bounded.MAX_EDGES', 1)
    result = impact(repo, ['core.py'], max_bytes=65536)
    assert result['truncated'] and result['limit_reason'] == 'visited_edges'
    assert result['items'] == [] and result['next_cursor'] is None


def test_ts_aliases_callbacks_and_test_scope(tmp_path):
    put(tmp_path, 'src/core.ts', 'export function work() { return 1; }\n')
    put(tmp_path, 'src/barrel.ts', "export { work as action } from './core';\n")
    put(tmp_path, 'tsconfig.json', json.dumps({'compilerOptions': {'baseUrl': '.', 'paths': {'@/*': ['src/*']}}}))
    put(tmp_path, 'tests/core.test.ts', "import { action } from '@/barrel';\nexport function testWork() { [1].map(() => action()); }\n")
    result = impact(tmp_path, ['src/core.ts::work'], scopes=['test'], heuristics=False, max_bytes=65536)
    assert any(i['file'] == 'tests/core.test.ts' and i['relevance'] == 'reference' for i in result['items'])


def test_test_changes_do_not_perturb_production(repo):
    before = ibwd_callers('work', response_version=2)['items']
    put(repo, 'tests/test_shadow.py', 'def work(): pass\ndef test_it(): work()\n')
    assert ibwd_callers('work', response_version=2)['items'] == before
    result = impact(repo, ['tests/test_shadow.py::work'], heuristics=False)
    assert [i['symbol_id'] for i in result['items']] == ['tests/test_shadow.py::test_it']


def test_cli(repo):
    result = CliRunner().invoke(main, ['impact', 'core.py::work', '--repo', str(repo), '--scope', 'test', '--max-bytes', '65536'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['items']


def test_compiler_missing_environment_is_unknown(repo):
    put(repo, 'a.ts', 'const x = 1;\n')
    result = compiler_evidence(repo, 'a.ts', 1, 7)
    assert result['status'] == 'unknown' and result['items'] == []
    with pytest.raises(ValueError, match='inside'):
        compiler_evidence(repo, '../outside.ts', 1, 1)


def test_installed_compiler_evidence(tmp_path):
    # Reuse a local compiler if available; never install one to run this check.
    options = [Path('/usr/local/lib/node_modules/typescript/lib/typescript.js'),
               Path('/opt/homebrew/lib/node_modules/typescript/lib/typescript.js'),
               Path(__file__).resolve().parents[1] / 'node_modules/typescript/lib/typescript.js',
               Path(__file__).resolve().parents[1] / 'ibwd-sprint3-kit/node_modules/typescript/lib/typescript.js']
    compiler = next((p for p in options if p.is_file()), None)
    if not compiler or not shutil.which('node'):
        pytest.skip('No installed TypeScript compiler')
    put(tmp_path, 'tsconfig.json', '{"compilerOptions":{"strict":true},"include":["*.ts"]}')
    put(tmp_path, 'core.ts', 'export class Service { run() { return 1; } }\n')
    put(tmp_path, 'use.ts', 'import { Service } from "./core";\nexport function invoke(s: Service) { return s.run(); }\n')
    result = compiler_evidence(tmp_path, 'core.ts', 1, 24, compiler=str(compiler), max_bytes=65536)
    assert result['status'] == 'available', result
    assert result['version'] and result['project'] == 'tsconfig.json'
    assert any(i['file'] == 'use.ts' for i in result['items'])
    assert all(i['resolution_status'] == 'possible' for i in result['items'])
    put(tmp_path, 'use.ts', 'import { missing } from "absent-package";\n')
    result = compiler_evidence(tmp_path, 'core.ts', 1, 24, compiler=str(compiler), max_bytes=65536)
    assert result['status'] == 'incomplete' and result['diagnostics']


def test_compiler_deadline_remains_unknown(repo, monkeypatch):
    import subprocess
    put(repo, 'a.ts', 'const value = 1;\n')
    put(repo, 'tsconfig.json', '{}')
    put(repo, 'node_modules/typescript/lib/typescript.js', '// installed fixture')
    monkeypatch.setattr('ibwd.retrieval.compiler.shutil.which', lambda _: '/installed/node')

    def timeout(command, **kwargs):
        assert command[0] == '/installed/node'
        assert kwargs['timeout'] == 20 and '--max-old-space-size=512' in command
        raise subprocess.TimeoutExpired(command, 20)

    monkeypatch.setattr('ibwd.retrieval.compiler.subprocess.run', timeout)
    result = compiler_evidence(repo, 'a.ts', 1, 7)
    assert result['status'] == 'unknown' and 'deadline' in result['reason']
    assert result['items'] == []


def test_scope_reclassification_clears_test_symbols(repo):
    # Moving a test into production changes its query scope without leaving stale test definitions.
    source = repo / 'tests/test_app.py'
    source.rename(repo / 'smoke.py')
    scoped = ibwd_find_symbol('test_smoke', scope='test', response_version=2)
    assert scoped['items'] == []
    assert ibwd_find_symbol('test_smoke')[0]['file'] == 'smoke.py'
    result = impact(repo, diff=True, max_bytes=65536)
    changed = {(i['snapshot'], i['symbol_id']) for i in result['items'] if i['relevance'] == 'changed_identity'}
    assert ('previous', 'tests/test_app.py::test_smoke') in changed
    assert ('scoped', 'smoke.py::test_smoke') in changed
