"""Deterministic task packets and hash-checked exact source expansion."""
from __future__ import annotations

import json
from pathlib import Path
import time

from ibwd.local_io import atomic_write
from ibwd.retrieval.bounded import MAX_SECONDS, compact, cursor_encode, cursor_offset, encoded_size, query_key
from ibwd.retrieval.lexical import SCOPES, declaration, source_text, words
from ibwd.retrieval.service import fresh_query

VERSION = 1
MAX_CANDIDATES = 200
GAPS = ('Lexical matches are relevance hints; test matches are not verified coverage. '
        'Graph links cover resolved production Python/JS/TS only. Ignored, secret-named, binary, '
        'non-UTF-8 and >1 MiB files are excluded from source evidence. Long lines index a prefix. '
        'Use source search when candidate/work limits are reached.')


def budget_bytes(budget_tokens, max_bytes):
    if type(budget_tokens) is not int or not 512 <= budget_tokens <= 16000:
        raise ValueError('budget_tokens must be 512..16000 (a byte-based estimate, not provider tokens).')
    if type(max_bytes) is not int or not 256 <= max_bytes <= 65536:
        raise ValueError('max_bytes must be 256..65536.')
    return min(budget_tokens * 4, max_bytes)


def envelope(generation):
    return dict(schema_version=2, retrieval_version=VERSION, index_generation=generation,
                scope='lexical source/test/doc/config; resolved production graph', items=[], files={},
                truncated=False, limit_reason=None, next_cursor=None)


def file_record(conn, path):
    row = conn.execute('SELECT * FROM evidence_files WHERE path=?', (path,)).fetchone()
    if row is None:
        raise ValueError('Path is not in the readable evidence inventory; use file discovery or source search.')
    if row['omission']:
        raise ValueError(row['omission'])
    return row


def read(root, symbol_id_or_path, expected_hash, line_range=None, budget_tokens=1000, max_bytes=16384):
    """Return an intact symbol body or explicitly requested inclusive line range."""
    root = Path(root).resolve()
    budget = budget_bytes(budget_tokens, max_bytes)
    if not isinstance(expected_hash, str) or not expected_hash:
        raise ValueError('expected_hash is required; obtain it from fresh context or discovery.')
    if line_range is not None and (not isinstance(line_range, list) or len(line_range) != 2
                                  or any(type(n) is not int for n in line_range)
                                  or not 1 <= line_range[0] <= line_range[1]):
        raise ValueError('range must be [start_line, end_line], inclusive and one-based.')

    def query(conn, generation):
        symbol = conn.execute("SELECT * FROM nodes WHERE qualified_name=? AND node_type IN ('Class','Function','Method')",
                              (symbol_id_or_path,)).fetchone()
        if symbol is None:
            symbol = conn.execute("SELECT * FROM scoped_nodes WHERE qualified_name=? AND node_type IN ('Class','Function','Method')",
                                  (symbol_id_or_path,)).fetchone()
        path = symbol['file_path'] if symbol else symbol_id_or_path
        file = file_record(conn, path)
        if file['content_hash'] != expected_hash:
            raise ValueError('Stale source hash; request fresh context before reading.')
        lines = source_text(root, path, expected_hash).splitlines(keepends=True)
        start, end = line_range or ([symbol['start_line'], symbol['end_line']] if symbol else [1, len(lines)])
        if end > len(lines) or (not lines and line_range is not None):
            raise ValueError('Requested range exceeds the source file.')
        result = envelope(generation)
        result['files'] = {path: expected_hash}
        result['items'] = [dict(file=path, scope=file['scope'], line=start, end_line=end,
                                text=''.join(lines[start-1:end]))]
        if symbol:
            result['items'][0]['symbol_id'] = symbol['qualified_name']
        if encoded_size(result) > budget:
            raise ValueError('Budget cannot fit the complete source span; increase budget or request an explicit smaller range.')
        return result
    return fresh_query(root, query)


def candidates(conn, task, targets, scopes):
    """Exact matches first; BM25 ranking followed by deterministic file diversity."""
    output, seen = [], set()
    deadline = time.monotonic() + MAX_SECONDS
    placeholders = ','.join('?' for _ in scopes)

    def add(path, start, end, reason, symbol=None, heading=None):
        if time.monotonic() > deadline:
            raise ValueError('Context work budget exceeded; narrow the query or use source search.')
        key = (path, start, end)
        if key not in seen:
            seen.add(key)
            item = dict(file=path, line=start, end_line=end, reason=reason)
            if symbol:
                item['symbol_id'] = symbol
            if heading:
                item['heading'] = heading
            output.append(item)

    missing = []
    for target in dict.fromkeys([*targets, task]):
        rows = conn.execute(f"SELECT n.* FROM nodes n JOIN evidence_files f ON f.path=n.file_path "
                            f"WHERE f.omission IS NULL AND f.scope IN ({placeholders}) "
                            "AND n.node_type IN ('Class','Function','Method') "
                            "AND (n.qualified_name=? OR n.name=? OR n.file_path=?) "
                            "ORDER BY n.file_path,n.start_line,n.qualified_name LIMIT ?",
                            (*scopes, target, target, target, MAX_CANDIDATES + 1)).fetchall()
        for row in rows:
            add(row['file_path'], row['start_line'], row['end_line'], 'exact', row['qualified_name'])
        if not rows:
            chunks = conn.execute(f'SELECT * FROM evidence_fts WHERE path=? AND scope IN ({placeholders}) '
                                  'ORDER BY CAST(start_line AS INTEGER) LIMIT ?',
                                  (target, *scopes, MAX_CANDIDATES + 1)).fetchall()
            for row in chunks:
                add(row['path'], int(row['start_line']), int(row['end_line']), 'exact', heading=row['heading'])
            if not chunks and target in targets:
                missing.append(target)
        if len(output) > MAX_CANDIDATES:
            return output[:MAX_CANDIDATES], True, missing
    terms = list(dict.fromkeys(words(task).split()))[:32]
    limited = False
    if terms:
        match = ' OR '.join('"' + term + '"' for term in terms)
        rows = conn.execute(f'SELECT *, bm25(evidence_fts) AS rank FROM evidence_fts '
                            f'WHERE evidence_fts MATCH ? AND scope IN ({placeholders}) '
                            'ORDER BY rank,path,CAST(start_line AS INTEGER) LIMIT ?',
                            (match, *scopes, MAX_CANDIDATES + 1)).fetchall()
        limited = len(rows) > MAX_CANDIDATES
        # Round-robin files within the lexical tier; exact targets keep precedence.
        buckets = {}
        for row in rows[:MAX_CANDIDATES]:
            buckets.setdefault(row['path'], []).append(row)
        while buckets:
            for path in list(buckets):
                row = buckets[path].pop(0)
                start, end = int(row['start_line']), int(row['end_line'])
                if not any(i['file'] == path and i['line'] <= start and i['end_line'] >= end for i in output):
                    add(path, start, end, 'lexical', heading=row['heading'])
                if not buckets[path]:
                    del buckets[path]
    return output[:MAX_CANDIDATES], limited or len(output) > MAX_CANDIDATES, missing


def enrich(conn, root, item, detail):
    item = dict(item)
    file = file_record(conn, item['file'])
    item['scope'] = file['scope']
    item['read'] = dict(symbol_id_or_path=item.get('symbol_id', item['file']),
                        expected_hash=file['content_hash'], range=[item['line'], item['end_line']])
    text = source_text(root, item['file'], file['content_hash'])
    lines = text.splitlines(keepends=True)
    # Outlines keep exact whole lines. Bodies are never cut to fit the packet.
    item['outline'] = lines[item['line']-1].rstrip('\r\n') if lines else ''
    if len(item['outline'].encode()) > 1024:
        del item['outline']
        item['outline_omitted'] = 'Long line; use an exact source read.'
    if item.get('symbol_id'):
        signature = declaration(text, item['file'], item['line'], item['end_line'])
        if signature is not None:
            item['signature'] = signature
        else:
            item['signature_omitted'] = 'No bounded declaration available; use exact source read.'
    symbols = conn.execute("SELECT qualified_name,start_line,end_line FROM nodes WHERE file_path=? "
                           "AND node_type IN ('Class','Function','Method') AND start_line BETWEEN ? AND ? "
                           "ORDER BY start_line,qualified_name LIMIT 7",
                           (item['file'], item['line'], item['end_line'])).fetchall()
    item['definitions'] = [dict(symbol_id=r[0], line=r[1], end_line=r[2]) for r in symbols[:6]]
    item['definitions_truncated'] = len(symbols) > 6
    # One-hop resolved evidence; test relevance comes only from lexical matching.
    seed = item.get('symbol_id') or (symbols[0][0] if symbols else None)
    if seed:
        rows = conn.execute("SELECT n.qualified_name AS symbol_id,n.file_path AS file,n.start_line AS line,"
                            "n.end_line,e.relation,"
                            "CASE WHEN e.source_id=s.id THEN 'outgoing' ELSE 'incoming' END AS direction "
                            "FROM nodes s JOIN edges e ON (e.source_id=s.id OR e.target_id=s.id) "
                            "JOIN nodes n ON n.id=CASE WHEN e.source_id=s.id THEN e.target_id ELSE e.source_id END "
                            "WHERE s.qualified_name=? AND e.resolution_status='resolved' "
                            "AND e.relation IN ('CALLS','IMPORTS','INHERITS','REFERENCES') "
                            "ORDER BY n.file_path,n.qualified_name,e.relation,direction LIMIT 7", (seed,)).fetchall()
        item['relationship_seed'] = seed
        item['relationships'] = [dict(r) for r in rows[:6]]
        item['relationships_truncated'] = len(rows) > 6
    if detail == 'source':
        item['text'] = ''.join(lines[item['line']-1:item['end_line']])
    return item, file['content_hash']


def context(root, task, targets=None, budget_tokens=2000, detail='outline', cursor=None,
            scopes=None, max_bytes=16384, semantic=None):
    root = Path(root).resolve()
    if semantic is None:
        from ibwd.retrieval.semantic import enabled
        semantic = enabled(root)
    budget = budget_bytes(budget_tokens, max_bytes)
    if not isinstance(task, str) or not task.strip() or len(task) > 4096:
        raise ValueError('task must contain 1..4096 characters.')
    targets = targets or []
    if len(targets) > 20 or any(not isinstance(t, str) or len(t) > 1024 for t in targets):
        raise ValueError('Use at most 20 targets, each at most 1024 characters.')
    scopes = sorted(set(SCOPES if scopes is None else scopes))
    if not scopes or any(s not in SCOPES for s in scopes):
        raise ValueError('scopes must select source, test, doc or config.')
    if detail not in ('outline', 'source'):
        raise ValueError('detail must be outline or source.')
    if type(semantic) is not bool:
        raise ValueError('semantic must be a boolean.')
    signature = query_key('context', [VERSION, task, targets, scopes, detail])

    def query(conn, generation):
        from ibwd.telemetry import retrieval_observation
        observation = retrieval_observation.get()
        if observation is not None:
            observation['embedding'] = {'mode': 'pending' if semantic else 'disabled',
                                        'model_digest': None, 'preprocessing_version': None,
                                        'inference_attempted': False, 'elapsed_ms': None if semantic else 0.0}
            observation.setdefault('embedding_attempts', []).append(observation['embedding'])
        semantic_status = None
        packet_signature = signature
        if semantic:
            choices, capped, missing = candidates(conn, task, targets, scopes)
            from ibwd.retrieval.semantic import ranked, fuse
            optional, semantic_status = ranked(root, conn, generation, task, scopes)
            choices, fusion_capped = fuse(choices, optional)
            capped = capped or fusion_capped or semantic_status.get('truncated', False)
            packet_signature = query_key('semantic-context', [signature, semantic_status.get('semantic_generation'),
                                                               semantic_status['status']])
        deadline = time.monotonic() + MAX_SECONDS
        # Optional inference has its own subprocess deadline; preserve the deterministic SQL budget.
        if semantic:
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        offset = cursor_offset(cursor, generation, packet_signature)
        # Cache is repository-local, generation/query/budget-bound, never client-bound.
        cache = root / '.ibwd/context-cache'
        cache.mkdir(exist_ok=True)
        key = query_key('packet', [generation, packet_signature, offset, budget])
        cached = cache / (key + '.json')
        if not semantic and cached.is_file():
            try:
                result = json.loads(cached.read_text())
                if result['index_generation'] == generation and encoded_size(result) <= budget:
                    return result
            except (ValueError, KeyError):
                pass
        if not semantic:
            choices, capped, missing = candidates(conn, task, targets, scopes)
        if offset > len(choices):
            raise ValueError('Invalid context cursor offset.')
        result = envelope(generation)
        if semantic_status is not None:
            result['semantic'] = semantic_status
        result.update(scope_gaps=GAPS, unresolved_targets=missing,
                      budget_basis='serialized MCP bytes <= min(max_bytes, 4 * budget_tokens); not provider token counts')
        # Surface ancestor instruction locations without replacing their authority.
        instruction_paths = set()
        for choice in choices:
            for parent in (Path(choice['file']).parent, *Path(choice['file']).parent.parents):
                for name in ('AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md'):
                    path = (parent / name).as_posix()
                    if conn.execute("SELECT 1 FROM nodes WHERE node_type='File' AND file_path=?", (path,)).fetchone():
                        instruction_paths.add(path)
        result['instructions'] = sorted(instruction_paths)[:20]
        result['instructions_truncated'] = len(instruction_paths) > 20
        for path in result['instructions']:
            result['files'][path] = conn.execute("SELECT content_hash FROM nodes WHERE node_type='File' AND file_path=?",
                                                (path,)).fetchone()[0]
        index = offset
        while index < len(choices):
            if time.monotonic() > deadline:
                raise ValueError('Context work budget exceeded; narrow the query or use source search.')
            item, hash_value = enrich(conn, root, choices[index], detail)
            proposed = json.loads(compact(result))
            proposed['items'].append(item)
            proposed['files'][item['file']] = hash_value
            for relation in item.get('relationships', []):
                row = conn.execute("SELECT content_hash FROM nodes WHERE node_type='File' AND file_path=?",
                                   (relation['file'],)).fetchone()
                if row:
                    proposed['files'][relation['file']] = row[0]
            if 'text' in item and any('text' in prior and prior['file'] == item['file']
                                      and prior['line'] <= item['end_line'] and item['line'] <= prior['end_line']
                                      for prior in result['items']):
                del item['text']
                item['source_omitted'] = 'Overlapping source already included; read the complete span if needed.'
            more = index + 1 < len(choices)
            proposed.update(truncated=more or capped, limit_reason='packet_budget' if more else ('candidate_limit' if capped else None),
                            next_cursor=cursor_encode(generation, packet_signature, index + 1) if more else None)
            if encoded_size(proposed) > budget and 'text' in item:
                del item['text']
                item['source_omitted'] = 'Complete span does not fit; use read with the supplied hash/range.'
                proposed['items'][-1] = item
            if encoded_size(proposed) > budget:
                if not result['items']:
                    raise ValueError('Budget cannot fit one context item; increase budget or narrow targets/scopes.')
                break
            result = proposed
            index += 1
        if not choices:
            result.update(truncated=capped, limit_reason='candidate_limit' if capped else None)
        if encoded_size(result) > budget:
            raise ValueError('Budget cannot fit the context envelope; increase budget.')
        if time.monotonic() > deadline:
            raise ValueError('Context work budget exceeded; narrow the query or use source search.')
        if not semantic:
            atomic_write(cached, compact(result))
        for old in sorted(cache.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[64:]:
            old.unlink()
        return result
    return fresh_query(root, query)
