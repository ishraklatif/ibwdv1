"""Repository-locked freshness and versioned MCP retrieval boundary."""
from functools import wraps
import inspect
import json
from pathlib import Path
import sqlite3
import time

from ibwd.health import inspect_index
from ibwd.local_io import report_lock
from ibwd.scan import scan_locked
from ibwd.scanner.filesystem import scan_files
from ibwd.retrieval.bounded import execute, MAX_BYTES, MAX_SECONDS
from ibwd.index_inputs import config_digest
from ibwd.retrieval.lexical import SourceChanged
from ibwd.telemetry import retrieval_observation


def readonly(root):
    conn = sqlite3.connect((root / '.ibwd/graph.db').as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + MAX_SECONDS
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return conn


def fresh_query(root, query):
    """Run a read against one published generation; validate again before return."""
    root = Path(root).resolve()
    refreshed = False
    with report_lock(root / '.ibwd/index.lock', timeout=30):
        for attempt in range(2):
            if inspect_index(root)['status'] != 'ready':
                scan_locked(root)
                refreshed = True
            conn = readonly(root)
            try:
                generation = conn.execute("SELECT value FROM index_metadata WHERE key='generation'").fetchone()[0]
                inputs = conn.execute("SELECT value FROM index_metadata WHERE key='config_digest'").fetchone()[0]
                expected = {r[0]: (r[1], r[2]) for r in conn.execute(
                    "SELECT file_path,content_hash,kind FROM nodes WHERE node_type='File'")}
                result = query(conn, generation)
            except SourceChanged:
                if attempt == 0:
                    continue
                raise ValueError('Sources changed during retrieval twice; retry when edits settle.') from None
            except sqlite3.OperationalError as exc:
                if 'interrupt' in str(exc):
                    raise ValueError('Query work budget exceeded; narrow the query or use source search.') from None
                raise
            finally:
                conn.close()
            scanned = scan_files(root)
            if {f.path: (f.content_hash, f.kind) for f in scanned} != expected or config_digest(root, scanned) != inputs:
                if attempt == 0:
                    continue
                raise ValueError('Sources changed during retrieval twice; retry when edits settle.')
            observation = retrieval_observation.get()
            if observation is not None:
                observation.update(freshness='validated', refreshed=refreshed,
                                   index_generation=generation)
            return result


def retrieval(operation):
    """Expose explicit version/budget controls without changing v1 response shapes."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def guarded(*args, **kwargs):
            version = kwargs.pop('response_version', 1)
            limit = kwargs.pop('limit', 50)
            max_bytes = kwargs.pop('max_bytes', 16384)
            cursor = kwargs.pop('cursor', None)
            if version not in (1, 2):
                raise ValueError('response_version must be 1 or 2')
            if version == 1 and (cursor is not None or limit != 50 or max_bytes != 16384):
                raise ValueError('Pagination and custom budgets require response_version=2')
            scope = kwargs.pop('scope', 'source')
            if scope not in ('source', 'test', 'all'):
                raise ValueError('scope must be source/test/all')
            if scope != 'source' and (version != 2 or operation not in ('find_symbol', 'list_symbols')):
                raise ValueError('Explicit symbol scope requires response_version=2 discovery')
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            root = Path.cwd().resolve()
            def query(conn, generation):
                arguments = dict(bound.arguments)
                if scope != 'source':
                    from ibwd.graph.scoped import select_scope
                    select_scope(conn)
                    arguments['scope'] = scope
                result = (execute(conn, operation, arguments, generation, limit, max_bytes, cursor)
                          if version == 2 else function(*args, **kwargs))
                if version == 1 and len(json.dumps(result).encode()) * 6 + 2048 > MAX_BYTES:
                    raise ValueError('Legacy output budget exceeded; use response_version=2 with pagination.')
                return result
            return fresh_query(root, query)

        parameters = list(signature.parameters.values()) + [
            inspect.Parameter('response_version', inspect.Parameter.KEYWORD_ONLY, default=1, annotation=int),
            inspect.Parameter('limit', inspect.Parameter.KEYWORD_ONLY, default=50, annotation=int),
            inspect.Parameter('max_bytes', inspect.Parameter.KEYWORD_ONLY, default=16384, annotation=int),
            inspect.Parameter('cursor', inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str | None),
        ]
        if operation in ('find_symbol', 'list_symbols'):
            parameters.append(inspect.Parameter('scope', inspect.Parameter.KEYWORD_ONLY, default='source', annotation=str))
        guarded.__signature__ = signature.replace(parameters=parameters, return_annotation=dict | list[dict])
        guarded.__annotations__ = dict(function.__annotations__, scope=str, response_version=int, limit=int, max_bytes=int, cursor=str | None)
        guarded.__annotations__['return'] = dict | list[dict]
        guarded.__doc__ = (function.__doc__ or '') + '\nFreshness is checked automatically. response_version=2 returns a bounded envelope with generation, items, files (source hashes), truncation and next_cursor. Use the same query and cursor for the next page. limit=1..200, max_bytes=256..65536. Version 1 preserves legacy shapes but rejects oversized results.'
        if operation in ('find_symbol', 'list_symbols'):
            guarded.__doc__ += ' Explicit scope=test/all with response_version=2 includes separately indexed test symbols; source remains the default.'
        return guarded
    return decorate
