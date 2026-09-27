"""Durable evidence contracts; no inference or benchmark runs."""
import json

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.retrieval.context import context
from ibwd.retrieval.durable import retrieve, save


@pytest.fixture
def evidence(tmp_path):
    (tmp_path / 'app.py').write_text('from dep import value\ndef run():\n    return value\n')
    (tmp_path / 'dep.py').write_text('value = 1\n')
    packet = context(tmp_path, 'run value', targets=['app.py', 'dep.py'], semantic=False)
    return tmp_path, dict(evidence=[dict(file='app.py', hash=packet['files']['app.py'], range=[2, 3])],
                          dependencies=[dict(file='dep.py', hash=packet['files']['dep.py'], range=[1, 1])])


def test_extracts_reused_across_clients_and_unrelated_edits(evidence, monkeypatch):
    from ibwd.mcp.server import ibwd_artifact_read
    root, payload = evidence
    first = save(root, 'summary', payload)
    assert save(root, 'summary', payload) == first
    assert first['artifact']['extracts'][0]['text'] == 'def run():\n    return value\n'
    assert first['artifact']['model_digest'] is None
    (root / 'unrelated.py').write_text('x = 2\n')
    monkeypatch.chdir(root)
    loaded = ibwd_artifact_read(first['artifact_id'])
    assert loaded['status'] == 'ready'
    assert loaded['validated_generation'] != first['artifact']['index_generation']


@pytest.mark.parametrize('mutation', ['source', 'dependency', 'delete', 'ignore'])
def test_stale_artifacts_never_return_obsolete_contents(evidence, mutation):
    root, payload = evidence
    saved = save(root, 'summary', payload)
    if mutation == 'source':
        (root / 'app.py').write_text('def new(): pass\n')
    elif mutation == 'dependency':
        (root / 'dep.py').write_text('value = 2\n')
    elif mutation == 'delete':
        (root / 'dep.py').unlink()
    else:
        (root / '.gitignore').write_text('dep.py\n')
    result = retrieve(root, saved['artifact_id'])
    assert result['status'] == 'stale' and 'artifact' not in result


def test_bad_ranges_hashes_paths_and_budgets_cannot_publish(evidence):
    root, payload = evidence
    for patch in ({'range': [2, 99]}, {'range': [True, 2]}, {'hash': 'old'}, {'file': '../outside'}):
        modified = dict(payload, evidence=[dict(payload['evidence'][0], **patch)])
        with pytest.raises(ValueError):
            save(root, 'summary', modified)
    with pytest.raises(ValueError, match='Budget'):
        save(root, 'summary', payload, max_bytes=256)
    assert not (root / '.ibwd/durable').exists()


def test_corrupt_missing_and_unsafe_ids(evidence):
    root, payload = evidence
    assert retrieve(root, 'a' * 64)['status'] == 'missing'
    with pytest.raises(ValueError, match='artifact_id'):
        retrieve(root, '../../escape')
    saved = save(root, 'summary', payload)
    path = root / '.ibwd/durable' / (saved['artifact_id'] + '.json')
    path.write_text('{"schema_version":1}')
    assert retrieve(root, saved['artifact_id'])['status'] == 'invalid'


def test_handoff_records_without_executing_and_checks_deleted_paths(evidence):
    root, evidence_payload = evidence
    payload = dict(goal='Fix run', user_decisions=['Keep API'], changed_paths=['app.py', 'deleted.py'],
                   commands=[dict(command='touch should-not-exist', exit_code=0, result='User reported success')],
                   unresolved_questions=['Check runtime dispatch'], evidence=evidence_payload['evidence'])
    saved = save(root, 'handoff', payload)
    assert not (root / 'should-not-exist').exists()
    assert saved['artifact']['validation_status'] == 'caller_reported'
    assert saved['artifact']['files']['deleted.py'] is None
    assert retrieve(root, saved['artifact_id'])['status'] == 'ready'
    (root / 'deleted.py').write_text('x = 1\n')
    assert retrieve(root, saved['artifact_id'])['status'] == 'stale'


def test_cli_roundtrip_and_mcp_save(evidence, monkeypatch):
    from ibwd.mcp.server import ibwd_artifact_save
    root, payload = evidence
    monkeypatch.chdir(root)
    saved = ibwd_artifact_save('summary', payload)
    runner = CliRunner()
    response = runner.invoke(main, ['artifact-read', saved['artifact_id'], '--repo', str(root)])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)['status'] == 'ready'
    source = root / '.ibwd/payload.json'
    source.write_text(json.dumps(payload))
    response = runner.invoke(main, ['artifact-save', 'summary', str(source), '--repo', str(root)])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)['artifact_id'] == saved['artifact_id']


def test_secret_and_excluded_changed_paths_rejected(evidence):
    root, payload = evidence
    (root / '.env').write_text('SECRET=hidden\n')
    handoff = dict(goal='task', user_decisions=[], changed_paths=['.env'], commands=[],
                   unresolved_questions=[], evidence=[])
    with pytest.raises(ValueError):
        save(root, 'handoff', handoff)
    with pytest.raises(ValueError):
        save(root, 'summary', dict(payload, dependencies=[dict(file='.env', hash='x', range=[1, 1])]))


def test_shared_evidence_dependency_does_not_duplicate_extract(evidence):
    root, payload = evidence
    saved = save(root, 'summary', dict(payload, dependencies=payload['evidence']))
    assert len(saved['artifact']['extracts']) == 1
    assert retrieve(root, saved['artifact_id'])['status'] == 'ready'


def test_unsupported_generated_provenance_rejected_even_with_valid_digest(evidence):
    import hashlib
    from ibwd.retrieval.durable import _json
    root, payload = evidence
    artifact = save(root, 'summary', payload)['artifact']
    artifact['model_digest'] = 'unapproved-model'
    raw = _json(artifact)
    identifier = hashlib.sha256(raw.encode()).hexdigest()
    (root / '.ibwd/durable' / (identifier + '.json')).write_text(raw)
    assert retrieve(root, identifier)['status'] == 'invalid'


def test_large_extract_fails_without_publication(evidence):
    root, _ = evidence
    (root / 'large.md').write_text('x' * 24001 + '\n')
    from ibwd.retrieval.service import fresh_query
    digest = fresh_query(root, lambda conn, generation: conn.execute(
        "SELECT content_hash FROM evidence_files WHERE path='large.md'").fetchone()[0])
    payload = dict(evidence=[dict(file='large.md', hash=digest, range=[1, 1])], dependencies=[])
    with pytest.raises(ValueError, match='Extracts exceed'):
        save(root, 'summary', payload, max_bytes=65536)
    assert not (root / '.ibwd/durable').exists()
