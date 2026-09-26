"""Bounded exposure/dependency paths and explicitly unverified test relevance."""
from collections import deque
from pathlib import Path

from ibwd.graph.scoped import select_scope
from ibwd.retrieval.bounded import (RELATIONS, Work, LimitExceeded, adjacency, brief, matching,
                                    cursor_encode, cursor_offset, encoded_size, query_key)
from ibwd.retrieval.service import fresh_query

GAPS = ('Change exposure is not breakage. References and filename heuristics are not verified test coverage. '
        'Dynamic registration and unresolved receivers may be absent; empty results do not establish safe deletion.')


def walk(conn, seeds, direction, relations, depth, scopes, work, snapshot):
    items = []
    for seed in seeds:
        queue = deque([(seed, [])])
        seen = {seed['id']}
        while queue:
            node, path = queue.popleft()
            work.check(node['id'])
            if len(path) >= depth:
                continue
            for edge in adjacency(conn, node['id'], direction == 'incoming', relations, False, work):
                target = conn.execute('SELECT * FROM nodes WHERE id=?', (edge['neighbor'],)).fetchone()
                if target['id'] in seen:
                    continue
                seen.add(target['id'])
                hop = dict(source=brief(conn.execute('SELECT * FROM nodes WHERE id=?', (edge['source_id'],)).fetchone()),
                           target=brief(conn.execute('SELECT * FROM nodes WHERE id=?', (edge['target_id'],)).fetchone()),
                           relation=edge['relation'], resolution_status=edge['resolution_status'],
                           provenance=edge['source_type'], confidence=edge['confidence'])
                evidence = path + [hop]
                kind = conn.execute("SELECT kind FROM nodes WHERE node_type='File' AND file_path=?", (target['file_path'],)).fetchone()[0]
                if kind in scopes:
                    items.append(brief(target) | dict(of=seed['qualified_name'] or seed['file_path'],
                        snapshot=snapshot, scope=kind, direction=direction, distance=len(evidence), evidence_path=evidence,
                        relevance='reference' if kind == 'test' else 'graph', coverage='unknown'))
                queue.append((target, evidence))
    return items


def impact(root, targets=None, direction='incoming', relations=None, depth=2, scopes=None,
           diff=False, heuristics=True, limit=50, max_bytes=16384, cursor=None):
    targets = list(dict.fromkeys(targets or []))
    relations = list(dict.fromkeys(RELATIONS if relations is None else relations))
    scopes = list(dict.fromkeys(['source', 'test'] if scopes is None else scopes))
    if direction not in ('incoming', 'outgoing') or type(depth) is not int or not 1 <= depth <= 5:
        raise ValueError('direction must be incoming/outgoing; depth must be 1..5')
    if not relations or any(r not in RELATIONS for r in relations):
        raise ValueError('relations must select CALLS, IMPORTS, INHERITS or REFERENCES')
    if not scopes or any(s not in ('source', 'test') for s in scopes):
        raise ValueError('scopes must select source/test')
    if len(targets) > 20 or any(not isinstance(t, str) or not t or len(t) > 1024 for t in targets) or (not targets and not diff):
        raise ValueError('Supply 1..20 targets, or diff=true for changes since the previous indexed snapshot')
    if type(limit) is not int or not 1 <= limit <= 200 or type(max_bytes) is not int or not 256 <= max_bytes <= 65536:
        raise ValueError('limit must be 1..200 and max_bytes 256..65536')
    signature = query_key('impact', [targets, direction, relations, depth, scopes, diff, heuristics])

    def query(conn, generation):
        offset = cursor_offset(cursor, generation, signature)
        result = dict(schema_version=2, index_generation=generation, items=[], files={}, truncated=False,
                      limit_reason=None, next_cursor=None, scope_gaps=GAPS, unresolved_targets=[],
                      diff_basis='previous indexed snapshot vs current working tree' if diff else None)
        work = Work()
        items, hashes = [], {}
        try:
            changed = None
            if diff:
                select_scope(conn, 'previous')
                old = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT file_path,content_hash,kind FROM nodes WHERE node_type='File'")}
                select_scope(conn)
                new = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT file_path,content_hash,kind FROM nodes WHERE node_type='File'")}
                changed = {p for p in old.keys() | new.keys() if old.get(p) != new.get(p)}
            for snapshot in (('previous', 'scoped') if diff else ('scoped',)):
                select_scope(conn, snapshot)
                seeds = {}
                for target in targets:
                    matches = matching(conn, target)
                    if len(matches) > 10:
                        raise ValueError('Ambiguous target; supply an exact symbol_id or file')
                    if not matches:
                        result['unresolved_targets'].append(dict(target=target, snapshot=snapshot))
                    for row in matches:
                        seeds[row['id']] = row
                if not targets and changed is not None:
                    for path in sorted(changed):
                        work.check()
                        for row in conn.execute("SELECT * FROM nodes WHERE file_path=? AND node_type IN ('File','Class','Function','Method') ORDER BY id", (path,)):
                            work.check(row['id'])
                            seeds[row['id']] = row
                # File targets include both import exposure and symbol-level calls.
                for seed in list(seeds.values()):
                    if seed['node_type'] == 'File':
                        for row in conn.execute("SELECT * FROM nodes WHERE file_path=? AND node_type IN ('Class','Function','Method') ORDER BY id", (seed['file_path'],)):
                            work.check(row['id'])
                            seeds[row['id']] = row
                if diff:
                    for seed in seeds.values():
                        kind = conn.execute("SELECT kind FROM nodes WHERE node_type='File' AND file_path=?",
                                            (seed['file_path'],)).fetchone()[0]
                        if seed['file_path'] in changed and kind in scopes:
                            items.append(brief(seed) | dict(snapshot=snapshot, scope=kind, relevance='changed_identity',
                                                          evidence_path=[], coverage='unknown'))
                items.extend(walk(conn, seeds.values(), direction, relations, depth, scopes, work, snapshot))
                if heuristics and direction == 'incoming' and 'test' in scopes:
                    stems = {Path(s['file_path']).stem for s in seeds.values()}
                    for row in conn.execute("SELECT * FROM nodes WHERE node_type='File' AND kind='test' ORDER BY file_path"):
                        work.check(row['id'])
                        stem = Path(row['file_path']).stem.removeprefix('test_').removesuffix('_test').removesuffix('.test').removesuffix('.spec')
                        if stem in stems and not any(i['file'] == row['file_path'] and i['snapshot'] == snapshot for i in items):
                            items.append(brief(row) | dict(snapshot=snapshot, scope='test', relevance='filename_heuristic',
                                                          evidence_path=[], coverage='unknown'))
                hashes[snapshot] = dict(conn.execute("SELECT file_path,content_hash FROM nodes WHERE node_type='File'"))
        except LimitExceeded as exc:
            result.update(truncated=True, limit_reason=str(exc))
            items = []
        finally:
            # fresh_query's post-read validation uses the live inventory.
            for table in ('nodes', 'edges'):
                conn.execute(f'DROP VIEW IF EXISTS temp.{table}')
        selected = items[offset:offset + limit]
        while True:
            result['items'] = selected
            result['files'] = {}
            for item in selected:
                paths = {item['file']}
                for hop in item['evidence_path']:
                    paths.update([hop['source']['file'], hop['target']['file']])
                result['files'].setdefault(item['snapshot'], {}).update({p: hashes[item['snapshot']][p] for p in sorted(paths)})
            if offset + len(selected) < len(items):
                result.update(truncated=True, limit_reason='result_count' if len(selected) == limit else 'output_bytes',
                              next_cursor=cursor_encode(generation, signature, offset + len(selected)))
            if encoded_size(result) <= max_bytes:
                break
            if not selected:
                raise ValueError('Budget cannot fit the impact envelope; increase max_bytes')
            selected.pop()
        if items[offset:] and not selected:
            raise ValueError('Budget cannot fit one evidence path; increase max_bytes or reduce depth')
        return result
    return fresh_query(root, query)
