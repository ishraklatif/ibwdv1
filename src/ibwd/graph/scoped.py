"""Separate source+test graph; production resolution remains unchanged."""
import sqlite3

from ibwd.graph.database import _init_schema
from ibwd.graph.queries import sync_symbols
from ibwd.graph.resolution import rebuild_reference_edges
from ibwd.scanner.symbols import extract_symbols


def rebuild_scoped(conn, root, scanned, previous, previous_kinds, force=False):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='scoped_nodes'").fetchone()
    changed = previous != {f.path: f.content_hash for f in scanned} or previous_kinds != {f.path: f.kind for f in scanned}
    if exists and not changed and not force:
        return
    # The old scope survives deletions and no-op freshness checks. Publication of
    # both snapshots happens inside the existing staged database transaction.
    for table in ('nodes', 'edges'):
        conn.execute(f'DROP TABLE IF EXISTS previous_{table}')
        if exists:
            conn.execute(f'CREATE TABLE previous_{table} AS SELECT * FROM scoped_{table}')
    conn.commit()
    work = sqlite3.connect(':memory:')
    work.row_factory = sqlite3.Row
    try:
        _init_schema(work)
        # Copy only graph inputs, not lexical caches or the retained old graph.
        for table in ('nodes', 'edges', 'file_refs'):
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
            work.executemany(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})',
                             conn.execute(f'SELECT * FROM {table}'))
        for file in scanned:
            if file.kind == 'test':
                row = work.execute("SELECT id FROM nodes WHERE node_type='File' AND file_path=?", (file.path,)).fetchone()
                sync_symbols(work, row['id'], file.path, extract_symbols(root / file.path, file.path))
        rebuild_reference_edges(work, root, include_tests=True)
        for table in ('nodes', 'edges'):
            conn.execute(f'DROP TABLE IF EXISTS scoped_{table}')
            conn.execute(f'CREATE TABLE scoped_{table} AS SELECT * FROM {table} WHERE 0')
            columns = len(work.execute(f'PRAGMA table_info({table})').fetchall())
            conn.executemany(f'INSERT INTO scoped_{table} VALUES ({",".join("?" for _ in range(columns))})',
                             work.execute(f'SELECT * FROM {table}'))
        conn.execute('CREATE INDEX scoped_identity ON scoped_nodes(qualified_name)')
        conn.execute('CREATE INDEX scoped_file ON scoped_nodes(file_path)')
        conn.execute('CREATE INDEX scoped_incoming ON scoped_edges(target_id)')
        conn.execute('CREATE INDEX scoped_outgoing ON scoped_edges(source_id)')
        conn.commit()
    finally:
        work.close()


def select_scope(conn, snapshot='scoped'):
    """Connection-local views reuse bounded navigation without changing storage."""
    if snapshot not in ('scoped', 'previous'):
        raise ValueError('Unknown snapshot')
    if not conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (snapshot + '_nodes',)).fetchone():
        raise ValueError('No previous indexed snapshot. Scan a baseline before editing to use diff impact.')
    for table in ('nodes', 'edges'):
        conn.execute(f'DROP VIEW IF EXISTS temp.{table}')
        conn.execute(f'CREATE TEMP VIEW {table} AS SELECT * FROM main.{snapshot}_{table}')
