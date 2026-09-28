"""Embedding observations and human feedback, using deterministic adapters only."""
import json

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.retrieval.context import context
from ibwd.retrieval import semantic
from ibwd.telemetry import retrieval_observation
from ibwd.usage_evidence import retrieval_evidence, embedding_profile
from ibwd.usage_hooks import capture_session, refresh_comparison
from tests.test_sprint6 import setup
from tests.test_usage_hooks import transcript


def observed_context(repo, **kwargs):
    observation = {}
    token = retrieval_observation.set(observation)
    try:
        context(repo, 'persist', **kwargs)
    finally:
        retrieval_observation.reset(token)
    return observation['embedding']


def test_modes_default_override_cache_and_fallback(setup, monkeypatch):
    repo, model, calls = setup
    assert observed_context(repo)['mode'] == 'disabled'
    assert observed_context(repo)['mode'] == 'disabled'  # cached response
    missing = observed_context(repo, semantic=True)
    assert missing['mode'] == 'fallback'
    assert missing['model_digest'] is None and not missing['inference_attempted']
    built = semantic.build(repo, model, 2)
    semantic.configure(repo, True)
    used = observed_context(repo)
    assert used['mode'] == 'used' and used['inference_attempted']
    assert used['model_digest'] == built['model_digest']
    assert used['preprocessing_version'] == semantic.VERSION and used['elapsed_ms'] >= 0
    calls.clear()
    assert observed_context(repo, semantic=False)['mode'] == 'disabled'
    assert not calls

    def fail(*args, **kwargs):
        raise ValueError('private runtime detail')

    monkeypatch.setattr(semantic, 'embed', fail)
    cached = observed_context(repo)
    assert cached['mode'] == 'used' and cached['query_cache_hit'] and not cached['inference_attempted']
    semantic._query_cache.clear()
    failed = observed_context(repo)
    assert failed['mode'] == 'fallback' and failed['inference_attempted']
    assert failed['model_digest'] == built['model_digest'] and failed['elapsed_ms'] >= 0
    assert 'private' not in json.dumps(failed) and str(model) not in json.dumps(failed)
    (repo / 'other.py').write_text('def changed(): pass\n')
    stale = observed_context(repo)
    assert stale['mode'] == 'fallback' and not stale['inference_attempted']
    assert stale['model_digest'] == built['model_digest']
    assert retrieval_observation.get() is None


def test_profiles_preserve_modes_models_partial_and_legacy():
    def event(oid, mode, digest):
        return dict(observation_id=oid, phase='completed', tool='ibwd_context', status='success',
                    embedding=dict(mode=mode, model_digest=digest, preprocessing_version='1',
                                   inference_attempted=mode == 'used', elapsed_ms=5))

    events = [event('a', 'used', 'a' * 64), event('b', 'disabled', None), event('c', 'fallback', 'b' * 64)]
    evidence = retrieval_evidence(events + [events[0]])
    assert evidence['embedding']['modes'] == {'disabled': 1, 'fallback': 1, 'used': 1}
    assert evidence['embedding']['timed_optional_attempts'] == 2
    assert evidence['embedding']['optional_elapsed_ms'] == 10
    assert len(embedding_profile(dict(server_evidence=evidence, observed_ibwd_calls=3))) == 3
    assert embedding_profile(dict(server_evidence=evidence, observed_ibwd_calls=4))[0] == 'incomplete_attribution'
    assert embedding_profile({'server_evidence': {'linked_requests': 1}, 'observed_ibwd_calls': 1}) == ('unknown',)
    legacy = retrieval_evidence([dict(observation_id='old', tool='ibwd_context', phase='completed')])
    assert legacy['embedding']['modes'] == {'unknown': 1}
    retried = dict(events[0], embedding_attempts=[events[2]['embedding'], events[0]['embedding']])
    result = retrieval_evidence([retried])['embedding']
    assert result['context_requests'] == 1 and result['context_attempts'] == 2
    assert result['modes'] == {'fallback': 1, 'used': 1}
    assert result['optional_elapsed_ms'] == 10


def test_freshness_retry_retains_both_embedding_attempts(setup, monkeypatch):
    repo, model, _ = setup
    semantic.build(repo, model, 2)
    original = semantic.ranked
    calls = 0

    def change_after_ranking(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            (repo / 'other.py').write_text('def changed(): pass\n')
        return result

    monkeypatch.setattr(semantic, 'ranked', change_after_ranking)
    observation = {}
    token = retrieval_observation.set(observation)
    try:
        context(repo, 'persist', semantic=True)
    finally:
        retrieval_observation.reset(token)
    assert [a['mode'] for a in observation['embedding_attempts']] == ['used', 'fallback']
    event = dict(observation, tool='ibwd_context', observation_id='retry', phase='completed', status='success')
    evidence = retrieval_evidence([event])['embedding']
    assert evidence['context_attempts'] == 2 and evidence['inference_attempts'] == 1
    assert evidence['timed_optional_attempts'] == 2


@pytest.mark.parametrize('client', ['codex', 'claude'])
def test_usefulness_capture_label_expiry_and_legacy_labels(tmp_path, client):
    log, hook = transcript(tmp_path, client)
    path = capture_session(hook, client, tmp_path)
    saved = json.loads(path.read_text())
    args = ['usage-label', saved['session_key'], '--client', client, '--repo', str(tmp_path),
            '--outcome', 'passed', '--task-kind', 'implementation', '--rework', 'no',
            '--retrieval-usefulness', 'partly-useful']
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    capture_session(hook, client, tmp_path)
    assert json.loads(path.read_text())['retrieval_usefulness'] == 'partly-useful'
    rows = json.loads((path.parent.parent / 'comparison.json').read_text())['groups']
    assert rows[0]['retrieval_usefulness'] == {'partly-useful': 1}
    assert rows[0]['usefulness_known_sessions'] == 1
    assert 'partly-useful' in (path.parent.parent / f'latest-{client}.md').read_text()
    labels_path = path.parent.parent / 'labels' / path.name
    labels = json.loads(labels_path.read_text())
    del labels['retrieval_usefulness']
    labels_path.write_text(json.dumps(labels))
    capture_session(hook, client, tmp_path)
    assert json.loads(path.read_text())['retrieval_usefulness'] == 'unknown'
    assert CliRunner().invoke(main, args).exit_code == 0
    with log.open('a') as stream:
        stream.write('{"type":"new_activity"}\n')
    capture_session(hook, client, tmp_path)
    updated = json.loads(path.read_text())
    assert updated.get('retrieval_usefulness', 'unknown') == 'unknown'
    assert updated['label_evidence']['status'] == 'stale'


def test_comparisons_separate_embedding_modes_and_model_versions(tmp_path):
    _, hook = transcript(tmp_path)
    path = capture_session(hook, 'codex', tmp_path)
    original = json.loads(path.read_text())
    for n, (mode, digest) in enumerate([('disabled', None), ('used', 'a' * 64),
                                       ('used', 'b' * 64), ('fallback', 'a' * 64)]):
        event = dict(observation_id=str(n), tool='ibwd_context', phase='completed', status='success',
                     embedding=dict(mode=mode, model_digest=digest, preprocessing_version='1', elapsed_ms=10))
        report = dict(original, server_evidence=retrieval_evidence([event]), server_observation_ids=[str(n)],
                      retrieval_usefulness='useful', observed_ibwd_calls=1)
        (path.parent / f'fixture-{n}.json').write_text(json.dumps(report))
    refresh_comparison(path.parent.parent, [])  # eviction must not merge known profiles
    rows = json.loads((path.parent.parent / 'comparison.json').read_text())['groups']
    assert len(rows) == 5
    assert sum(r['usefulness_known_sessions'] for r in rows) == 4
