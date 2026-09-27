"""Opt-in local embeddings. Disposable generations never replace lexical evidence."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from ibwd.local_io import atomic_write, report_lock
from ibwd.retrieval.lexical import source_text, declaration
from ibwd.retrieval.service import fresh_query

VERSION = '1'
MAX_CHUNKS = 4096
MAX_DIMENSIONS = 2048
MAX_INPUT_BYTES = 6144
MAX_STORE_BYTES = 256 * 1024 * 1024


def enabled(root):
    """A repository-local opt-in; absent or invalid settings keep models off."""
    try:
        path = Path(root) / '.ibwd/semantic-settings.json'
        if path.stat().st_size > 1024:
            return False
        value = json.loads(path.read_text())
        return isinstance(value, dict) and value.get('enabled') is True
    except (OSError, ValueError):
        return False


def configure(root, enable):
    if type(enable) is not bool:
        raise ValueError('enabled must be a boolean')
    result = dict(enabled=enable)
    atomic_write(Path(root).resolve() / '.ibwd/semantic-settings.json', json.dumps(result) + '\n')
    return result


def model_digest(path, timeout=60):
    """Hash local weights and configuration, never resolve a remote model name."""
    deadline = time.monotonic() + timeout
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError('model_path must be an installed local model directory')
    digest = hashlib.sha256()
    files = sorted(p for p in path.rglob('*') if p.is_file())
    if not files or len(files) > 10000:
        raise ValueError('Local model is empty or exceeds the file limit')
    for file in files:
        if not file.resolve().is_relative_to(path):
            raise ValueError('Model files must resolve inside the model directory')
        digest.update(file.relative_to(path).as_posix().encode() + b'\0')
        digest.update(str(file.stat().st_size).encode() + b'\0')
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                if time.monotonic() > deadline:
                    raise ValueError('Local model fingerprint deadline exceeded')
                digest.update(block)
    return digest.hexdigest()


def vectors_checked(vectors, count, dimensions):
    if not isinstance(vectors, list) or len(vectors) != count:
        raise ValueError('Embedding adapter returned the wrong vector count')
    output = []
    for vector in vectors:
        if (not isinstance(vector, list) or len(vector) != dimensions
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
            raise ValueError('Embedding adapter returned invalid dimensions or non-finite values')
        norm = math.hypot(*vector)
        if not norm or not math.isfinite(norm):
            raise ValueError('Embedding adapter returned an invalid vector norm')
        output.append([v / norm for v in vector])
    return output


def embed(model_path, texts, dimensions, timeout):
    """Run an optional installed runtime out of process with a hard deadline."""
    if not texts:
        return []
    worker = Path(__file__).parents[1] / 'resources/local-embeddings.py'
    with tempfile.TemporaryDirectory(prefix='ibwd-embeddings-') as directory:
        request, response = Path(directory) / 'in.json', Path(directory) / 'out.json'
        request.write_text(json.dumps(dict(model_path=str(model_path), texts=texts, dimensions=dimensions)))
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
        try:
            result = subprocess.run([sys.executable, str(worker), str(request), str(response)],
                                    env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ValueError('Local embedding deadline exceeded') from None
        if result.returncode or not response.is_file():
            raise ValueError('Local embedding runtime unavailable or model incompatible')
        if response.stat().st_size > MAX_STORE_BYTES:
            raise ValueError('Local embedding output exceeded its budget')
        return vectors_checked(json.loads(response.read_text()), len(texts), dimensions)


def open_store(root):
    path = root / '.ibwd/semantic.db'
    if not path.is_file() or path.stat().st_size > MAX_STORE_BYTES:
        raise ValueError('No usable semantic index; run semantic-index explicitly')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def metadata(db):
    return json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])


def chunks(conn, root):
    rows = conn.execute("SELECT e.*,f.content_hash FROM evidence_fts e JOIN evidence_files f ON f.path=e.path "
                        "WHERE f.omission IS NULL AND e.scope IN ('source','doc') "
                        "ORDER BY e.path,CAST(e.start_line AS INTEGER) LIMIT ?", (MAX_CHUNKS + 1,)).fetchall()
    if len(rows) > MAX_CHUNKS:
        raise ValueError('Semantic chunk limit exceeded; deterministic retrieval remains available')
    result, previous_path, lines = [], None, []
    for row in rows:
        if row['path'] != previous_path:
            text = source_text(root, row['path'], row['content_hash'])
            lines, previous_path = text.splitlines(keepends=True), row['path']
        start, end = int(row['start_line']), int(row['end_line'])
        body = ''.join(lines[start - 1:end]).encode()[:4096].decode('utf-8', errors='ignore')
        signature = declaration(text, row['path'], start, end) or ''
        payload = (row['path'] + '\n' + signature + '\n' + body).encode()[:MAX_INPUT_BYTES].decode('utf-8', errors='ignore')
        result.append(dict(file=row['path'], line=start, end_line=end, scope=row['scope'],
                           content_hash=row['content_hash'], key=hashlib.sha256(payload.encode()).hexdigest(), text=payload))
    return result


def build(root, model_path, dimensions, timeout=120, query_timeout=2):
    """Explicit maintenance only. Reuse unchanged inputs; publish after full success."""
    root = Path(root).resolve()
    model_path = Path(model_path).expanduser().resolve()
    if type(dimensions) is not int or not 1 <= dimensions <= MAX_DIMENSIONS:
        raise ValueError(f'dimensions must be 1..{MAX_DIMENSIONS}')
    if not 1 <= timeout <= 600:
        raise ValueError('timeout must be 1..600 seconds')
    if not 1 <= query_timeout <= 30:
        raise ValueError('query_timeout must be 1..30 seconds')
    started = time.monotonic()
    digest = model_digest(model_path)
    identity = dict(model_digest=digest, dimensions=dimensions, preprocessing_version=VERSION)

    def stage(evidence, generation):
        cached = {}
        try:
            old = open_store(root)
        except (OSError, ValueError, sqlite3.Error):
            old = None
        if old is not None:
            try:
                meta = metadata(old)
                if all(meta.get(k) == v for k, v in identity.items()):
                    for row in old.execute('SELECT key,vector FROM vectors LIMIT ?', (MAX_CHUNKS + 1,)):
                        cached[row[0]] = vectors_checked([json.loads(row[1])], 1, dimensions)[0]
            except (ValueError, TypeError, KeyError, sqlite3.Error):
                cached = {}
            finally:
                old.close()
        pending = {item['key']: item['text'] for item in evidence if item['key'] not in cached}
        produced = embed(model_path, list(pending.values()), dimensions, timeout) if pending else []
        cached.update(zip(pending, vectors_checked(produced, len(pending), dimensions)))
        if model_digest(model_path) != digest:
            raise ValueError('Local model changed during indexing; retry')
        meta = dict(identity, model_path=str(model_path), index_generation=generation,
                    semantic_generation=uuid.uuid4().hex, chunks=len(evidence), query_timeout=query_timeout)
        fd, temporary = tempfile.mkstemp(prefix='semantic-', suffix='.db', dir=root / '.ibwd')
        os.close(fd)
        try:
            db = sqlite3.connect(temporary)
            try:
                db.execute('CREATE TABLE metadata(value TEXT NOT NULL)')
                db.execute('INSERT INTO metadata VALUES(?)', (json.dumps(meta),))
                db.execute('CREATE TABLE vectors(key TEXT PRIMARY KEY, vector TEXT NOT NULL)')
                for key in sorted({item['key'] for item in evidence}):
                    db.execute('INSERT INTO vectors VALUES(?,?)', (key, json.dumps(cached[key])))
                db.execute('CREATE TABLE chunks(file TEXT,line INTEGER,end_line INTEGER,scope TEXT,content_hash TEXT,key TEXT)')
                db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?)',
                               [(i['file'], i['line'], i['end_line'], i['scope'], i['content_hash'], i['key']) for i in evidence])
                db.commit()
            finally:
                db.close()
            if Path(temporary).stat().st_size > MAX_STORE_BYTES:
                raise ValueError('Semantic index exceeded its storage budget')
            return temporary, dict(status='ready', **meta, embedded_chunks=len(pending),
                                   reused_chunks=len(evidence) - sum(i['key'] in pending for i in evidence),
                                   elapsed_seconds=round(time.monotonic() - started, 4),
                                   index_bytes=Path(temporary).stat().st_size)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    with report_lock(root / '.ibwd/semantic.lock', timeout=1):
        for attempt in range(2):
            generation, evidence = fresh_query(root, lambda conn, gen: (gen, chunks(conn, root)))
            # Inference must not hold the graph lock: deterministic queries remain usable.
            temporary, result = stage(evidence, generation)
            try:
                if fresh_query(root, lambda conn, gen: gen == generation):
                    os.replace(temporary, root / '.ibwd/semantic.db')
                    return result
            finally:
                Path(temporary).unlink(missing_ok=True)
        raise ValueError('Sources changed during semantic indexing twice; retry when edits settle')


def ranked(root, conn, generation, task, scopes):
    """Return bounded vector candidates or an explicit deterministic fallback."""
    started = time.monotonic()
    try:
        # Do not queue behind maintenance or another optional query.
        with report_lock(root / '.ibwd/semantic.lock', timeout=0):
            db = open_store(root)
            try:
                meta = metadata(db)
                if meta['index_generation'] != generation or meta['preprocessing_version'] != VERSION:
                    raise ValueError('Semantic index is stale; run semantic-index explicitly')
                valid = {(r[0], int(r[1]), int(r[2]), r[3], r[4]) for r in conn.execute(
                    "SELECT e.path,e.start_line,e.end_line,e.scope,f.content_hash FROM evidence_fts e "
                    "JOIN evidence_files f ON f.path=e.path WHERE f.omission IS NULL "
                    "AND e.scope IN ('source','doc') LIMIT ?", (MAX_CHUNKS + 1,))}
                if len(valid) > MAX_CHUNKS:
                    raise ValueError('Semantic evidence exceeds work limit')
                dimensions = meta['dimensions']
                if type(dimensions) is not int or not 1 <= dimensions <= MAX_DIMENSIONS:
                    raise ValueError('Invalid semantic dimensions')
                query_timeout = meta['query_timeout']
                if not 1 <= query_timeout <= 30:
                    raise ValueError('Invalid semantic query deadline')
                if model_digest(meta['model_path'], timeout=1) != meta['model_digest']:
                    raise ValueError('Local model changed; rebuild semantic index')
                vector = vectors_checked(embed(meta['model_path'], [task], dimensions, timeout=query_timeout), 1, dimensions)[0]
                if model_digest(meta['model_path'], timeout=1) != meta['model_digest']:
                    raise ValueError('Local model changed during query')
                rows = db.execute('SELECT c.*,v.vector FROM chunks c JOIN vectors v USING(key) '
                                  'ORDER BY file,line LIMIT ?', (MAX_CHUNKS + 1,))
                scored = []
                deadline = time.monotonic() + 2
                for number, row in enumerate(rows):
                    if number >= MAX_CHUNKS or time.monotonic() > deadline:
                        raise ValueError('Semantic index exceeds candidate work limit')
                    if (row['file'], row['line'], row['end_line'], row['scope'], row['content_hash']) not in valid:
                        raise ValueError('Semantic chunk does not match current source evidence')
                    if row['scope'] not in scopes:
                        continue
                    candidate = vectors_checked([json.loads(row['vector'])], 1, dimensions)[0]
                    score = sum(a * b for a, b in zip(vector, candidate))
                    scored.append((score, dict(file=row['file'], line=row['line'], end_line=row['end_line'], reason='semantic')))
                scored.sort(key=lambda pair: (-pair[0], pair[1]['file'], pair[1]['line']))
                return [item for _, item in scored[:200]], dict(status='ready', semantic_generation=meta['semantic_generation'],
                    model_digest=meta['model_digest'], dimensions=dimensions, preprocessing_version=VERSION,
                    fusion='reciprocal_rank_60; exact targets first; bounded resolved expansion',
                    truncated=len(scored) > 200, elapsed_seconds=round(time.monotonic() - started, 4))
            finally:
                db.close()
    except (OSError, ValueError, TypeError, KeyError, IndexError, sqlite3.Error) as exc:
        return [], dict(status='fallback', reason=str(exc)[:240])


def fuse(lexical, semantic, limit=200):
    """Reciprocal rank fusion is a retrieval heuristic, never a reranker score."""
    exact = [i for i in lexical if i['reason'] == 'exact']
    scores, items = {}, {}
    for ranked_items in ([i for i in lexical if i['reason'] != 'exact'], semantic):
        for rank, item in enumerate(ranked_items, 1):
            if any(e['file'] == item['file'] and e['line'] <= item['line'] and e['end_line'] >= item['end_line'] for e in exact):
                continue
            key = (item['file'], item['line'], item['end_line'])
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            if key in items:
                items[key] = dict(items[key], reason='lexical+semantic')
            else:
                items[key] = dict(item)
    result = exact + [items[k] for k in sorted(scores, key=lambda k: (-scores[k], k))]
    return result[:limit], len(result) > limit
