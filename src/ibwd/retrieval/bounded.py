"""Version 2 retrieval: bounded SQL, graph work, and generation-bound pages."""
from __future__ import annotations

import base64
import hashlib
import heapq
import json
import time

RELATIONS = ('CALLS', 'IMPORTS', 'INHERITS', 'REFERENCES')
SCOPE = 'production Python/JS/TS symbols and resolved edges; files include other categories; empty is not proof of no uses'
MAX_NODES = 2000
MAX_EDGES = 10000
MAX_SECONDS = 2.0
MAX_RESULTS = 200
MAX_BYTES = 65536


class LimitExceeded(ValueError):
    pass


class Work:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.nodes = set()
        self.edges = 0

    def check(self, node=None, edge=False):
        if time.monotonic() > self.deadline:
            raise LimitExceeded('elapsed_time')
        if node is not None:
            self.nodes.add(node)
        self.edges += int(edge)
        if len(self.nodes) > MAX_NODES:
            raise LimitExceeded('visited_nodes')
        if self.edges > MAX_EDGES:
            raise LimitExceeded('visited_edges')


def compact(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def encoded_size(payload):
    # Budget both MCP structured and text representations, including escaping.
    # Reserve for SDK fields, observation receipt, and outer result wrapper.
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    return len(compact({'structuredContent': {'result': payload}, 'content': [{'type': 'text', 'text': text}]}).encode()) + 1024


def query_key(operation, arguments):
    return hashlib.sha256(compact([operation, arguments]).encode()).hexdigest()


def cursor_encode(generation, query, offset):
    return base64.urlsafe_b64encode(compact([2, generation, query, offset]).encode()).decode()


def cursor_offset(cursor, generation, query):
    if cursor is None:
        return 0
    try:
        if len(cursor) > 1024:
            raise ValueError()
        version, saved, signature, offset = json.loads(base64.b64decode(cursor, altchars=b'-_', validate=True))
        if version != 2 or saved != generation or signature != query or type(offset) is not int or not 0 <= offset < 2**63:
            raise ValueError()
        return offset
    except (ValueError, TypeError, UnicodeError):
        raise ValueError('Invalid, stale, or mismatched cursor; restart this query.') from None


def brief(row):
    return {'name': row['name'], 'symbol_id': row['qualified_name'] or row['file_path'],
            'kind': row['node_type'], 'file': row['file_path'],
            'line': row['start_line'], 'end_line': row['end_line']}


def matching(conn, name, file=None, limit=11):
    row = conn.execute("SELECT * FROM nodes WHERE node_type='File' AND file_path=?", (name,)).fetchone()
    if row and file is None:
        return [row]
    clause = "node_type IN ('Class','Function','Method')"
    params = []
    if file is not None:
        clause += ' AND file_path=?'
        params.append(file)
    if '::' in name:
        clause += ' AND qualified_name=?'
        params.append(name)
    elif conn.execute('SELECT 1 FROM nodes WHERE ' + clause + ' AND name=? LIMIT 1', (*params, name)).fetchone():
        clause += ' AND name=?'
        params.append(name)
    else:
        clause += ' AND instr(lower(name),lower(?)) > 0'
        params.append(name)
    return conn.execute('SELECT * FROM nodes WHERE ' + clause + ' ORDER BY file_path,start_line,id LIMIT ?', (*params, limit)).fetchall()


def adjacency(conn, node, incoming, relations, candidates, work):
    start, end = ('target_id', 'source_id') if incoming else ('source_id', 'target_id')
    sql = f'SELECT *, {end} AS neighbor FROM edges WHERE {start}=? AND relation IN ({",".join("?" for _ in relations)})'
    if not candidates:
        sql += " AND resolution_status='resolved'"
    # Stream edges; never materialize an unbounded adjacency list.
    for row in conn.execute(sql + ' ORDER BY id', (node, *relations)):
        work.check(row['neighbor'], edge=True)
        yield row


def reach(conn, targets, args, incoming, work):
    output = []
    for target in targets:
        start = target['id']
        work.check(start)
        frontier = {start: (1.0, False)}
        seen = {start}
        for distance in range(1, max(1, min(5, args.get('depth', 1))) + 1):
            layer = {}
            for node, (confidence, candidate) in frontier.items():
                for edge in adjacency(conn, node, incoming, RELATIONS, args.get('include_candidates', False), work):
                    neighbor = edge['neighbor']
                    if neighbor in seen:
                        continue
                    score = confidence * edge['confidence']
                    cand = candidate or edge['resolution_status'] == 'candidate'
                    old = layer.setdefault(neighbor, [score, cand, edge['relation'], set()])
                    old[3].add(edge['relation'])
                    if score > old[0]:
                        old[:3] = [score, cand, edge['relation']]
            for node, (score, cand, relation, relations) in layer.items():
                work.check()
                row = conn.execute('SELECT * FROM nodes WHERE id=?', (node,)).fetchone()
                item = brief(row) | {'distance': distance, 'confidence': round(score, 4), 'relation': relation,
                                     'relations': sorted(relations), 'resolution_status': 'candidate' if cand else 'resolved'}
                if len(targets) > 1:
                    item['of'] = target['qualified_name'] or target['file_path']
                output.append(item)
            frontier = {node: (data[0], data[1]) for node, data in layer.items()}
            seen.update(layer)
            if not frontier:
                break
    output.sort(key=lambda r: (r['distance'], -r['confidence'], r['file'], r['line'] or 0, r['symbol_id'], r.get('of', '')))
    return output


def trace(conn, args, work):
    sources = matching(conn, args['source'])
    targets = matching(conn, args['target'])
    if len(sources) > 10 or len(targets) > 10:
        raise ValueError('Ambiguous endpoints; no path search performed. Use exact symbol_id values.')
    relations = args.get('edge_types') or ['CALLS']
    if any(r not in RELATIONS for r in relations):
        raise ValueError('Unsupported edge_types')
    target_ids = {r['id'] for r in targets}
    distance = {r['id']: 0.0 for r in sources}
    heap = [(0.0, n) for n in distance]
    heapq.heapify(heap)
    previous = {}
    while heap:
        cost, node = heapq.heappop(heap)
        work.check(node)
        if cost != distance[node]:
            continue
        if node in target_ids:
            ids = [node]
            while node in previous:
                node = previous[node][0]
                ids.append(node)
            ids.reverse()
            hops = []
            for node in ids:
                hop = brief(conn.execute('SELECT * FROM nodes WHERE id=?', (node,)).fetchone())
                if node in previous:
                    _, info = previous[node]
                    hop.update(info)
                hops.append(hop)
            return [{'path': hops, 'cost': round(cost, 4), 'hops': len(ids)-1, 'cost_kind': 'sum of inverse heuristic scores'}]
        neighbors = {}
        for edge in adjacency(conn, node, False, relations, args.get('include_candidates', False), work):
            other = edge['neighbor']
            info = {'edge_type': edge['relation'], 'edge_types': [edge['relation']], 'confidence': edge['confidence'],
                    'resolution_status': edge['resolution_status']}
            if other not in neighbors:
                neighbors[other] = info
            else:
                old = neighbors[other]
                names = sorted(set(old['edge_types'] + info['edge_types']))
                if info['confidence'] > old['confidence']:
                    neighbors[other] = info
                neighbors[other]['edge_types'] = names
        for other, info in neighbors.items():
            candidate = cost + 1.0 / max(info['confidence'], 0.01)
            if candidate < distance.get(other, float('inf')):
                distance[other] = candidate
                previous[other] = (node, info)
                heapq.heappush(heap, (candidate, other))
    return [{'path': None, 'reason': 'No matching path in indexed scope; runtime paths may exist.'}]


def execute(conn, operation, args, generation, limit=50, max_bytes=16384, cursor=None):
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f'limit must be 1..{MAX_RESULTS}')
    if type(max_bytes) is not int or not 256 <= max_bytes <= MAX_BYTES:
        raise ValueError(f'max_bytes must be 256..{MAX_BYTES}')
    signature = query_key(operation, args)
    offset = cursor_offset(cursor, generation, signature)
    envelope = {'schema_version': 2, 'index_generation': generation, 'scope': SCOPE,
                'items': [], 'files': {}, 'truncated': False, 'limit_reason': None, 'next_cursor': None}
    work = Work()
    conn.set_progress_handler(lambda: int(time.monotonic() > work.deadline), 1000)
    try:
        if operation in ('callers', 'dependents'):
            targets = matching(conn, args['symbol'], args.get('file'))
            if len(targets) > 10:
                raise ValueError('Ambiguous targets; narrow with file or exact symbol_id.')
            rows = reach(conn, targets, args, operation == 'callers', work)
            items = rows[offset:offset + limit + 1]
        elif operation == 'trace_path':
            if offset:
                raise ValueError('Path responses are indivisible; restart with a larger byte budget.')
            items = trace(conn, args, work)
        else:
            params = []
            if operation == 'find_files':
                sql = "SELECT * FROM nodes WHERE node_type='File'"
                if args.get('kind'):
                    sql += ' AND kind=?'
                    params.append(args['kind'])
                if args.get('name_pattern'):
                    sql += ' AND instr(file_path,?)>0'
                    params.append(args['name_pattern'])
            else:
                sql = "SELECT * FROM nodes WHERE node_type IN ('Class','Function','Method')"
                if operation == 'list_symbols':
                    sql += ' AND file_path=?'
                    params.append(args['file'])
                else:
                    name = args['name']
                    if '::' in name:
                        sql += ' AND qualified_name=?'
                    elif conn.execute(sql + ' AND name=? LIMIT 1', (name,)).fetchone():
                        sql += ' AND name=?'
                    else:
                        sql += ' AND instr(lower(name),lower(?))>0'
                    params.append(name)
            rows = conn.execute(sql + ' ORDER BY file_path,start_line,id LIMIT ? OFFSET ?', (*params, limit+1, offset))
            items = [({'path': r['file_path'], 'kind': r['kind']} if operation == 'find_files' else brief(r)) for r in rows]
    except LimitExceeded as exc:
        envelope.update(truncated=True, limit_reason=str(exc))
        items = []  # Incomplete graph layers cannot establish shortest-distance results.
    finally:
        conn.set_progress_handler(None, 0)

    def files_for(items):
        paths = set()
        for item in items:
            for part in item.get('path', []) if isinstance(item.get('path'), list) else [item]:
                path = part.get('file') or part.get('path')
                if isinstance(path, str):
                    paths.add(path)
        return {path: conn.execute("SELECT content_hash FROM nodes WHERE node_type='File' AND file_path=?", (path,)).fetchone()[0]
                for path in sorted(paths)}

    selected = items[:limit]
    more = len(items) > limit
    while True:
        envelope['items'] = selected
        envelope['files'] = files_for(selected)
        if more:
            envelope.update(truncated=True, limit_reason='result_count' if len(selected) == limit else 'output_bytes',
                            next_cursor=cursor_encode(generation, signature, offset + len(selected)))
        if encoded_size(envelope) <= max_bytes:
            break
        if not selected:
            raise ValueError('Byte budget cannot fit the response envelope; increase max_bytes.')
        selected = selected[:-1]
        more = True
    if items and not selected:
        raise ValueError('Byte budget cannot fit one complete item; increase max_bytes or narrow the query.')
    return envelope
