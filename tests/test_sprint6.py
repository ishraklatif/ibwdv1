"""Deterministic adapter fixtures, not model quality or performance measurements."""
import json
import sqlite3
import subprocess

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.local_io import report_lock
from ibwd.retrieval import semantic
from ibwd.retrieval.context import context
from ibwd.retrieval.bounded import encoded_size


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'store.py').write_text('def persist(value):\n    return disk.write(value)\n')
    (repo / 'other.py').write_text('def paint():\n    return "green"\n')
    (repo / 'guide.md').write_text('# Durability\nData survives a restart.\n')
    (repo / '.env').write_text('PRIVATE_KEY=not-for-embedding\n')
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'weights').write_bytes(b'fixture-only')
    calls = []

    def fake(path, texts, dimensions, timeout):
        calls.append(list(texts))
        assert all('PRIVATE_KEY' not in t for t in texts)
        return [[1.0, 0.0] if any(w in t for w in ('persist', 'save', 'Durability')) else [0.0, 1.0] for t in texts]

    monkeypatch.setattr(semantic, 'embed', fake)
    return repo, model, calls


def test_default_never_embeds_and_missing_index_falls_back(setup):
    repo, _, calls = setup
    default = context(repo, 'persist')
    optional = context(repo, 'persist', semantic=True)
    assert calls == []
    assert default['items'] == optional['items']
    assert 'semantic' not in default
    assert optional['semantic']['status'] == 'fallback'


def test_refresh_reuses_content_and_removes_deleted_chunks(setup):
    repo, model, calls = setup
    first = semantic.build(repo, model, 2)
    assert first['embedded_chunks'] == 3
    calls.clear()
    noop = semantic.build(repo, model, 2)
    assert calls == [] and noop['embedded_chunks'] == 0 and noop['reused_chunks'] == 3
    (repo / 'other.py').write_text('def paint():\n    return "blue"\n')
    changed = semantic.build(repo, model, 2)
    assert changed['embedded_chunks'] == 1 and changed['reused_chunks'] == 2
    assert len(calls) == 1 and len(calls[0]) == 1
    calls.clear()
    (repo / 'guide.md').unlink()
    deleted = semantic.build(repo, model, 2)
    assert deleted['chunks'] == 2 and calls == []
    with semantic.open_store(repo) as db:
        assert not db.execute("SELECT 1 FROM chunks WHERE file='guide.md'").fetchall()
        assert db.execute('SELECT count(*) FROM vectors').fetchone()[0] == 2


def test_fusion_keeps_exact_and_adds_vague_evidence(setup):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    assert context(repo, 'save')['items'] == []
    result = context(repo, 'save', semantic=True, budget_tokens=8000, max_bytes=65536)
    assert result['semantic']['status'] == 'ready'
    assert result['items'][0]['file'] in ('guide.md', 'store.py')
    assert all(i['reason'] == 'semantic' for i in result['items'])
    exact = context(repo, 'paint', semantic=True)
    assert exact['items'][0]['symbol_id'] == 'other.py::paint'
    assert exact['items'][0]['reason'] == 'exact'
    docs = context(repo, 'save', scopes=['doc'], semantic=True)
    assert {i['file'] for i in docs['items']} == {'guide.md'}
    assert encoded_size(result) <= 32000


def test_stale_sources_and_model_changes_never_serve_old_vectors(setup):
    repo, model, calls = setup
    semantic.build(repo, model, 2)
    (repo / 'store.py').write_text('def fresh(): pass\n')
    calls.clear()
    result = context(repo, 'fresh', semantic=True)
    assert result['semantic']['status'] == 'fallback' and calls == []
    assert result['items'][0]['symbol_id'] == 'store.py::fresh'
    semantic.build(repo, model, 2)
    (model / 'weights').write_bytes(b'changed-fixture')
    calls.clear()
    assert context(repo, 'fresh', semantic=True)['semantic']['status'] == 'fallback'
    assert calls == []
    assert semantic.build(repo, model, 2)['embedded_chunks'] == 3


@pytest.mark.parametrize('failure', ['timeout', 'nan', 'dimensions', 'count'])
def test_failed_build_preserves_published_generation_and_query_falls_back(setup, monkeypatch, failure):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    old = (repo / '.ibwd/semantic.db').read_bytes()

    def broken(*args, **kwargs):
        if failure == 'timeout':
            raise ValueError('Local embedding deadline exceeded')
        if failure == 'nan':
            return [[float('nan'), 0]]
        if failure == 'dimensions':
            return [[1]]
        return []

    monkeypatch.setattr(semantic, 'embed', broken)
    result = context(repo, 'persist', semantic=True)
    assert result['semantic']['status'] == 'fallback'
    assert result['items'][0]['reason'] == 'exact'
    (repo / 'other.py').write_text('def new(): pass\n')
    with pytest.raises(ValueError):
        semantic.build(repo, model, 2)
    assert (repo / '.ibwd/semantic.db').read_bytes() == old
    assert list((repo / '.ibwd').glob('semantic-*.db')) == []


def test_busy_and_corrupt_store_preserve_deterministic_path(setup):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    with report_lock(repo / '.ibwd/semantic.lock'):
        result = context(repo, 'persist', semantic=True)
    assert result['semantic']['status'] == 'fallback'
    (repo / '.ibwd/semantic.db').write_bytes(b'corrupt')
    assert context(repo, 'persist', semantic=True)['items'][0]['reason'] == 'exact'
    assert semantic.build(repo, model, 2)['embedded_chunks'] == 3


def test_invalid_stored_evidence_falls_back(setup):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    with sqlite3.connect(repo / '.ibwd/semantic.db') as db:
        db.execute("UPDATE chunks SET end_line=99999 WHERE file='store.py'")
    result = context(repo, 'persist', semantic=True)
    assert result['semantic']['status'] == 'fallback'
    assert result['items'][0]['symbol_id'] == 'store.py::persist'


def test_incompatible_preprocessing_and_dimensions_rebuild(setup, monkeypatch):
    repo, model, calls = setup
    semantic.build(repo, model, 2)
    monkeypatch.setattr(semantic, 'VERSION', 'next')
    calls.clear()
    assert context(repo, 'save', semantic=True)['semantic']['status'] == 'fallback'
    assert semantic.build(repo, model, 2)['embedded_chunks'] == 3
    monkeypatch.setattr(semantic, 'embed', lambda path, texts, dimensions, timeout: [[1, 0, 0] for _ in texts])
    assert semantic.build(repo, model, 3)['embedded_chunks'] == 3


def test_semantic_pages_bind_vector_generation_and_mode(setup):
    repo, model, _ = setup
    for n in range(12):
        (repo / f'doc{n}.md').write_text(f'# Item {n}\nDurability persists.\n')
    semantic.build(repo, model, 2)
    result = context(repo, 'save', semantic=True, budget_tokens=2000)
    cursor = result['next_cursor']
    assert cursor
    seen = list(result['items'])
    while result['next_cursor']:
        result = context(repo, 'save', semantic=True, budget_tokens=2000, cursor=result['next_cursor'])
        seen.extend(result['items'])
        assert encoded_size(result) <= 8000
    assert len(seen) == 15
    with pytest.raises(ValueError, match='cursor'):
        context(repo, 'save', budget_tokens=2000, cursor=cursor)
    semantic.build(repo, model, 2)
    with pytest.raises(ValueError, match='cursor'):
        context(repo, 'save', semantic=True, budget_tokens=2000, cursor=cursor)


def test_cli_and_mcp_opt_in(setup, monkeypatch):
    from ibwd.mcp.server import ibwd_context
    repo, model, _ = setup
    result = CliRunner().invoke(main, ['semantic-index', '--repo', str(repo), '--model-path', str(model), '--dimensions', '2'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['embedded_chunks'] == 3
    result = CliRunner().invoke(main, ['context', 'save', '--repo', str(repo), '--semantic'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['semantic']['status'] == 'ready'
    monkeypatch.chdir(repo)
    assert ibwd_context('save', semantic=True)['semantic']['status'] == 'ready'


def test_repository_opt_in_and_explicit_override(setup, monkeypatch):
    from ibwd.mcp.server import ibwd_context
    repo, model, calls = setup
    semantic.build(repo, model, 2)
    calls.clear()
    assert 'semantic' not in context(repo, 'persist')
    assert calls == []
    runner = CliRunner()
    result = runner.invoke(main, ['semantic-config', '--repo', str(repo), '--enabled'])
    assert result.exit_code == 0, result.output
    assert semantic.enabled(repo)
    assert context(repo, 'persist')['semantic']['status'] == 'ready'
    assert 'semantic' not in context(repo, 'persist', semantic=False)
    monkeypatch.chdir(repo)
    assert ibwd_context('persist')['semantic']['status'] == 'ready'
    result = runner.invoke(main, ['context', 'persist', '--repo', str(repo), '--no-semantic'])
    assert result.exit_code == 0 and 'semantic' not in json.loads(result.output)
    result = runner.invoke(main, ['semantic-config', '--repo', str(repo), '--disabled'])
    assert result.exit_code == 0 and not semantic.enabled(repo)
    assert 'semantic' not in context(repo, 'persist')


@pytest.mark.parametrize('text', ['null', '[]', '{"enabled":"true"}', 'broken'])
def test_invalid_setting_fails_closed(setup, text):
    repo, _, calls = setup
    (repo / '.ibwd').mkdir(exist_ok=True)
    (repo / '.ibwd/semantic-settings.json').write_text(text)
    assert not semantic.enabled(repo)
    assert 'semantic' not in context(repo, 'persist')
    assert calls == []


def test_worker_protocol_is_offline_bounded_and_optional(tmp_path, monkeypatch):
    # A fake installed runtime exercises the actual subprocess without loading weights.
    (tmp_path / 'sentence_transformers.py').write_text('''
import os
class SentenceTransformer:
    max_seq_length = 4096
    def __init__(self, path, **kwargs):
        assert kwargs == dict(device='cpu', local_files_only=True, trust_remote_code=False)
        assert os.environ['HF_HUB_OFFLINE'] == os.environ['TRANSFORMERS_OFFLINE'] == '1'
    def get_sentence_embedding_dimension(self): return 2
    def encode(self, texts, **kwargs):
        assert self.max_seq_length == 512
        assert kwargs['batch_size'] == 8 and kwargs['prompt'] == ''
        class Result:
            def tolist(self): return [[3.0, 4.0] for _ in texts]
        return Result()
''')
    monkeypatch.setenv('PYTHONPATH', str(tmp_path))
    assert semantic.embed(tmp_path, ['test'], 2, 5) == [[0.6, 0.8]]
    with pytest.raises(ValueError, match='unavailable'):
        semantic.embed(tmp_path, ['test'], 3, 5)


def test_adapter_deadline(tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 1
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(semantic.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='deadline'):
        semantic.embed(tmp_path, ['test'], 2, 1)


def test_source_changes_during_build_are_retried_before_publication(setup, monkeypatch):
    repo, model, _ = setup
    original = semantic.embed
    changed = False

    def mutate(path, texts, dimensions, timeout):
        nonlocal changed
        if not changed:
            (repo / 'other.py').write_text('def after_edit(): pass\n')
            changed = True
        return original(path, texts, dimensions, timeout)

    monkeypatch.setattr(semantic, 'embed', mutate)
    result = semantic.build(repo, model, 2)
    packet = context(repo, 'after_edit', semantic=True)
    assert packet['semantic']['status'] == 'ready'
    assert packet['index_generation'] == result['index_generation']
    assert packet['items'][0]['symbol_id'] == 'other.py::after_edit'
    assert list((repo / '.ibwd').glob('semantic-*.db')) == []


def test_deterministic_queries_remain_usable_during_indexing(setup, monkeypatch):
    repo, model, _ = setup
    original = semantic.embed

    def while_building(path, texts, dimensions, timeout):
        assert context(repo, 'persist')['items'][0]['symbol_id'] == 'store.py::persist'
        optional = context(repo, 'persist', semantic=True)
        assert optional['semantic']['status'] == 'fallback'
        return original(path, texts, dimensions, timeout)

    monkeypatch.setattr(semantic, 'embed', while_building)
    assert semantic.build(repo, model, 2)['status'] == 'ready'


def test_model_change_during_build_keeps_previous_generation(setup, monkeypatch):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    previous = (repo / '.ibwd/semantic.db').read_bytes()
    original = semantic.embed
    (repo / 'other.py').write_text('def changed(): pass\n')

    def mutate(path, texts, dimensions, timeout):
        (model / 'weights').write_bytes(b'changed-during-build')
        return original(path, texts, dimensions, timeout)

    monkeypatch.setattr(semantic, 'embed', mutate)
    with pytest.raises(ValueError, match='changed during indexing'):
        semantic.build(repo, model, 2)
    assert (repo / '.ibwd/semantic.db').read_bytes() == previous


def test_oversized_corpus_and_model_paths_rejected(setup, monkeypatch):
    repo, model, _ = setup
    monkeypatch.setattr(semantic, 'MAX_CHUNKS', 1)
    with pytest.raises(ValueError, match='chunk limit'):
        semantic.build(repo, model, 2)
    (model / 'outside').symlink_to(repo / 'store.py')
    with pytest.raises(ValueError, match='inside'):
        semantic.model_digest(model)
    with pytest.raises(ValueError, match='dimensions'):
        semantic.build(repo, model, 0)
