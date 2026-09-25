"""Local deterministic evidence contracts; no model or benchmark execution."""
import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.retrieval.bounded import encoded_size
from ibwd.retrieval.context import context, read
from ibwd.retrieval.output import reduce_output
from ibwd.scan import run_scan

TASKS = json.loads((Path(__file__).parent / 'fixtures/sprint4_tasks.json').read_text())


@pytest.fixture
def repo(tmp_path):
    files = {
        'src/auth.py': 'def refreshAccessToken(user):\n    """Renew an expired credential."""\n    return validate(user)\n\ndef validate(user):\n    return bool(user)\n',
        'src/session.ts': 'export function refreshSession(user: string) {\n  return user.trim();\n}\n',
        'tests/test_auth.py': 'def test_expired_credential():\n    assert refreshAccessToken("user")\n',
        'docs/auth.md': '# Authentication\n\n## Credential renewal\nRenew expired credentials with the session service.\n',
        'settings.toml': '[session]\nexpiry_seconds = 3600\n',
        'AGENTS.md': 'Run the local authentication tests after changes.\n',
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def pages(repo, **kwargs):
    page = context(repo, **kwargs)
    yield page
    while page['next_cursor']:
        page = context(repo, **kwargs, cursor=page['next_cursor'])
        yield page


def test_exact_target_graph_scope_and_intact_source(repo):
    packet = context(repo, 'renew credential', ['src/auth.py::refreshAccessToken'], detail='source')
    first = packet['items'][0]
    assert first['symbol_id'] == 'src/auth.py::refreshAccessToken'
    assert first['relationships'][0]['symbol_id'] == 'src/auth.py::validate'
    assert first['text'] == 'def refreshAccessToken(user):\n    """Renew an expired credential."""\n    return validate(user)\n'
    assert packet['instructions'] == ['AGENTS.md']
    expanded = read(repo, **{('line_range' if k == 'range' else k): v for k, v in first['read'].items()})
    assert expanded['items'][0]['text'] == first['text']
    assert encoded_size(packet) <= 8000


def test_split_identifiers_and_scopes(repo):
    packet = context(repo, 'access token', scopes=['source'])
    assert packet['items'][0]['file'] == 'src/auth.py'
    assert all(i['scope'] == 'source' for i in packet['items'])
    assert context(repo, 'expiry seconds', scopes=['config'])['items'][0]['file'] == 'settings.toml'
    assert context(repo, 'expired credential', scopes=['test'])['items'][0]['file'] == 'tests/test_auth.py'
    assert context(repo, 'renewal', scopes=['doc'])['items'][0]['heading'] == '## Credential renewal'
    assert not context(repo, 'zzzznonexistentzzzz')['items']


def test_pages_bound_to_generation_filters_and_details(repo):
    for n in range(25):
        (repo / f'module{n}.py').write_text(f'def shared_{n}(): return {n}\n')
    results = list(pages(repo, task='shared', budget_tokens=1200))
    items = [i for p in results for i in p['items']]
    assert len(items) == len({(i['file'], i['line'], i['end_line']) for i in items}) == 25
    assert all(encoded_size(p) <= 4800 for p in results)
    first = results[0]
    assert first['truncated']
    for changes in ({'task': 'different'}, {'scopes': ['doc']}, {'detail': 'source'}):
        with pytest.raises(ValueError, match='cursor'):
            context(repo, **({'task': 'shared', 'cursor': first['next_cursor']} | changes))
    (repo / 'module0.py').write_text('def changed(): pass\n')
    with pytest.raises(ValueError, match='stale'):
        context(repo, 'shared', cursor=first['next_cursor'])


def test_exact_read_rejects_stale_hash_and_preserves_crlf_unicode(repo):
    source = repo / 'unicode.py'
    source.write_bytes('def hello():\r\n    return "世界"\r\n'.encode())
    packet = context(repo, 'hello', targets=['unicode.py::hello'])
    hash_value = packet['files']['unicode.py']
    assert read(repo, 'unicode.py::hello', hash_value)['items'][0]['text'] == source.read_bytes().decode()
    assert read(repo, 'unicode.py', hash_value, [2, 2])['items'][0]['text'] == '    return "世界"\r\n'
    source.write_text('def hello(): return 2\n')
    with pytest.raises(ValueError, match='Stale'):
        read(repo, 'unicode.py', hash_value)


def test_secrets_ignored_binary_symlinks_and_large_files_are_not_evidence(repo):
    (repo / '.env').write_text('TOPSECRET=superprivate\n')
    (repo / '.gitignore').write_text('ignored.py\n')
    (repo / 'ignored.py').write_text('superprivate = 1\n')
    (repo / 'binary.txt').write_bytes(b'superprivate\0')
    (repo / 'large.txt').write_text('superprivate ' * 100000)
    (repo / 'link.py').symlink_to(repo / 'src/auth.py')
    assert not context(repo, 'superprivate')['items']
    for path in ('.env', '../outside.py', '/etc/passwd', 'ignored.py', 'binary.txt', 'large.txt', 'link.py'):
        with pytest.raises(ValueError):
            read(repo, path, 'anything')


def test_source_budget_does_not_cut_a_body(repo):
    (repo / 'big.py').write_text('def large():\n' + '    value = "012345678901234567890123456789"\n' * 150)
    packet = context(repo, 'large', targets=['big.py::large'], detail='source')
    item = packet['items'][0]
    assert 'text' not in item and item['source_omitted']
    with pytest.raises(ValueError, match='complete source span'):
        read(repo, 'big.py::large', packet['files']['big.py'])
    assert read(repo, 'big.py', packet['files']['big.py'], [1, 2])['items'][0]['end_line'] == 2
    for span in ([0, 1], [2, 1], [1, 10000], [True, 2]):
        with pytest.raises(ValueError):
            read(repo, 'big.py', packet['files']['big.py'], span)


def test_cache_is_cross_client_but_invalidates_on_mutation(repo, monkeypatch):
    import ibwd.retrieval.context as module
    first = context(repo, 'refreshAccessToken')
    original = module.candidates
    def unexpected(*args):
        pytest.fail('same generation/query should reuse the packet')
    monkeypatch.setattr(module, 'candidates', unexpected)
    assert context(repo, 'refreshAccessToken') == first
    monkeypatch.setattr(module, 'candidates', original)
    (repo / 'src/auth.py').write_text('\ndef refreshAccessToken(): return 1\n')
    second = context(repo, 'refreshAccessToken')
    assert first['index_generation'] != second['index_generation']
    assert second['items'][0]['line'] == 2


def test_candidate_limit_is_explicit(repo):
    (repo / 'many.py').write_text(''.join(f'def broad_{i}(): return {i}\n' for i in range(210)))
    results = list(pages(repo, task='anything', targets=['many.py']))
    assert results[-1]['truncated'] and results[-1]['limit_reason'] == 'candidate_limit'
    assert results[-1]['next_cursor'] is None


def test_incremental_lexical_matches_fresh_and_old_index_upgrades(repo):
    run_scan(repo)
    (repo / 'src/auth.py').unlink()
    (repo / 'settings.toml').write_text('[new_config]\nretry_limit=4\n')
    context(repo, 'retry limit')
    def rows():
        with sqlite3.connect(repo / '.ibwd/graph.db') as conn:
            return conn.execute('SELECT * FROM evidence_fts ORDER BY path,start_line').fetchall()
    incremental = rows()
    run_scan(repo)
    assert rows() == incremental
    with sqlite3.connect(repo / '.ibwd/graph.db') as conn:
        conn.execute("DELETE FROM index_metadata WHERE key='lexical_version'")
    assert context(repo, 'retry limit')['items']


def test_cli_and_reducer_preserve_failures(repo):
    runner = CliRunner()
    result = runner.invoke(main, ['context', 'refreshAccessToken', '--repo', str(repo)])
    assert result.exit_code == 0, result.output
    packet = json.loads(result.output)
    result = runner.invoke(main, ['read', 'src/auth.py', '--repo', str(repo), '--expected-hash',
                                  packet['files']['src/auth.py'], '--range', '1', '3'])
    assert result.exit_code == 0 and json.loads(result.output)['items'][0]['line'] == 1
    log = repo / 'pytest.log'
    log.write_text('....F [100%]\n================ FAILURES ================\n'
                   'test_auth\n> assert actual == expected\nE AssertionError: mismatch\n'
                   '================ 1 failed, 4 passed in 0.1s ================\n')
    reduced = reduce_output(log, 'pytest', 1)
    assert reduced['failure_count'] == 1 and 'assert actual == expected' in reduced['diagnostics']
    assert reduced['reduced_bytes'] < reduced['original_bytes']
    result = runner.invoke(main, ['reduce-output', str(log), '--format', 'pytest', '--exit-code', '1'])
    assert result.exit_code == 1 and json.loads(result.output)['full_output_path'] == str(log)
    log.write_text('compiler crashed unexpectedly\n')
    assert reduce_output(log, 'tsc', 2)['diagnostics'] == log.read_text()
    assert reduce_output(log, 'tsc', 2)['failure_count'] is None


@pytest.mark.parametrize('case', TASKS, ids=lambda c: c['id'])
def test_frozen_task_evidence_retention(repo, case, record_property):
    packets = list(pages(repo, task=case['task'], targets=case['targets'], scopes=case['scopes']))
    items = [i for p in packets for i in p['items']]
    required = case['required_file']
    if required:
        assert required in {i['file'] for i in items}
    else:
        assert items == []
    # Record cumulative output including source expansion, not only the first packet.
    size = sum(encoded_size(p) for p in packets)
    for item in items:
        ref = item['read']
        expanded = read(repo, ref['symbol_id_or_path'], ref['expected_hash'], ref['range'], budget_tokens=16000, max_bytes=65536)
        lines = (repo / item['file']).read_text().splitlines(keepends=True)
        assert expanded['items'][0]['text'] == ''.join(lines[item['line']-1:item['end_line']])
        size += encoded_size(expanded)
    record_property('cumulative_evidence_bytes', size)
    assert all(encoded_size(p) <= 8000 for p in packets)


def test_ambiguous_names_deduplicate_source_and_keep_identities(repo):
    (repo / 'other.py').write_text('def validate(user): return user is not None\n')
    packets = list(pages(repo, task='validate', detail='source'))
    exact = [i for p in packets for i in p['items'] if i.get('symbol_id')]
    assert {i['symbol_id'] for i in exact} == {'src/auth.py::validate', 'other.py::validate'}
    for packet in packets:
        bodies = [i for i in packet['items'] if 'text' in i]
        for a in bodies:
            for b in bodies:
                if a is not b and a['file'] == b['file']:
                    assert a['end_line'] < b['line'] or b['end_line'] < a['line']


def test_source_mutation_during_expansion_retries(repo, monkeypatch):
    import ibwd.retrieval.context as module
    original = module.source_text
    changed = False
    def mutate(root, path, expected):
        nonlocal changed
        if not changed and path == 'src/auth.py':
            changed = True
            (root / path).write_text('\ndef refreshAccessToken(): return 4\n')
        return original(root, path, expected)
    monkeypatch.setattr(module, 'source_text', mutate)
    packet = context(repo, 'refreshAccessToken')
    assert packet['items'][0]['line'] == 2


def test_unknown_failure_output_is_preserved_and_tsc_counts_diagnostics(repo):
    log = repo / 'output.log'
    for content in ('fatal crash before tests\n', 'KeyboardInterrupt\n', 'unrecognized assertion\n'):
        log.write_text(content)
        reduced = reduce_output(log, 'pytest', 2)
        assert reduced['diagnostics'] == content and reduced['failure_count'] is None
    log.write_text('src/a.ts(1,2): error TS2322: bad type\n  const value: number = "x";\n')
    assert reduce_output(log, 'tsc', 2)['failure_count'] == 1
    assert reduce_output(log, 'tsc', 2)['diagnostics'] == log.read_text()


def test_multiline_signatures_are_exact_and_exclude_bodies(repo):
    (repo / 'multiline.py').write_text('def typed(\n    user: str,\n    count: int = 1,\n) -> str:\n    return user * count\n')
    (repo / 'multiline.ts').write_text('export function typed(\n  user: {name: string},\n): string {\n  return user.name;\n}\n')
    packet = context(repo, 'typed')
    signatures = {i['file']: i['signature'] for i in packet['items'] if 'signature' in i}
    assert signatures['multiline.py'] == 'def typed(\n    user: str,\n    count: int = 1,\n) -> str:'
    assert signatures['multiline.ts'] == 'function typed(\n  user: {name: string},\n): string'


def test_context_work_budget_is_an_explicit_error(repo, monkeypatch):
    monkeypatch.setattr('ibwd.retrieval.context.MAX_SECONDS', -1)
    with pytest.raises(ValueError, match='work budget'):
        context(repo, 'refreshAccessToken')


def test_reducer_keeps_diagnostics_before_the_failure_section(repo):
    log = repo / 'output.log'
    log.write_text('WARNING: database unavailable\nF [100%]\n===== FAILURES =====\nE assert False\n'
                   '===== 1 failed in 0.1s =====\n')
    result = reduce_output(log, 'pytest', 1)
    assert 'WARNING: database unavailable' in result['diagnostics']
    assert 'E assert False' in result['diagnostics']
