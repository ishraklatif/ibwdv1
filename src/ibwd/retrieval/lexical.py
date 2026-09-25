"""Generation-local FTS5 evidence, separate from the production symbol graph."""
from __future__ import annotations

from pathlib import Path
import re

import xxhash

VERSION = '1'
MAX_FILE_BYTES = 1024 * 1024
CHUNK_LINES = 32
CHUNK_BYTES = 4096
SCOPES = ('source', 'test', 'doc', 'config')


class SourceChanged(ValueError):
    pass


def words(text):
    split = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    split = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', split)
    return ' '.join(re.findall(r'[^\W_]+', split.lower()))


def excluded(path):
    """Conservative filename exclusions, not a secret detector."""
    p = Path(path)
    return (any(part.lower() in {'.ssh', '.aws', '.azure', '.gnupg', 'secrets'} for part in p.parts)
            or p.name.lower().startswith('.env')
            or p.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.keystore'}
            or p.name.lower() in {'credentials', 'credentials.json', 'secrets.json', 'id_rsa', 'id_ed25519'})


def source_text(root, path, expected_hash):
    relative = Path(path)
    if relative.is_absolute() or '..' in relative.parts or excluded(path):
        raise ValueError('Source path is excluded or outside the repository.')
    target = root / relative
    if any(p.is_symlink() for p in (target, *target.parents) if p != root and root in p.parents):
        raise ValueError('Source symlinks are excluded.')
    with target.open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('Source exceeds the 1 MiB evidence limit; use local source search.')
    if xxhash.xxh3_64(data).hexdigest() != expected_hash:
        raise SourceChanged('Stale source hash; request fresh context before reading.')
    if b'\0' in data:
        raise ValueError('Binary source is excluded.')
    try:
        return data.decode('utf-8')
    except UnicodeError:
        raise ValueError('Source is not UTF-8; use local source tools.') from None


def declaration(text, path, start, end):
    """Exact declaration prefix through the parser's body boundary, if bounded."""
    from tree_sitter import Parser
    from ibwd.scanner.symbols import EXTENSION_DIALECTS
    dialect = EXTENSION_DIALECTS.get(Path(path).suffix.lower())
    if dialect == 'python':
        from ibwd.scanner.python import _LANGUAGE
        language = _LANGUAGE
    elif dialect:
        from ibwd.scanner.javascript import _LANGUAGES
        language = _LANGUAGES[dialect]
    else:
        return None
    data = text.encode()
    tree = Parser(language).parse(data)
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.end_point.row < start - 1 or node.start_point.row > end - 1:
            continue
        body = node.child_by_field_name('body')
        if body and node.start_point.row >= start - 1 and node.end_point.row <= end - 1:
            prefix = data[node.start_byte:body.start_byte].decode().rstrip()
            return prefix if len(prefix.encode()) <= 1024 else None
        stack.extend(reversed(node.named_children))
    return None


def rebuild(conn, root, scanned):
    """Update changed evidence privately, then publish it with the graph."""
    conn.execute('CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    version = conn.execute("SELECT value FROM index_metadata WHERE key='lexical_version'").fetchone()
    if not version or version[0] != VERSION:
        conn.execute('DROP TABLE IF EXISTS evidence_fts')
        conn.execute('DROP TABLE IF EXISTS evidence_files')
    conn.execute('CREATE TABLE IF NOT EXISTS evidence_files(path TEXT PRIMARY KEY, scope TEXT, content_hash TEXT, omission TEXT)')
    conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(path UNINDEXED, scope UNINDEXED, '
                 'start_line UNINDEXED, end_line UNINDEXED, heading UNINDEXED, terms, tokenize="unicode61")')
    previous = {r[0]: (r[1], r[2]) for r in conn.execute('SELECT path,scope,content_hash FROM evidence_files')}
    current = {f.path: (f.kind, f.content_hash) for f in scanned if f.kind in SCOPES}
    for path in previous:
        if current.get(path) != previous[path]:
            conn.execute('DELETE FROM evidence_fts WHERE path=?', (path,))
            conn.execute('DELETE FROM evidence_files WHERE path=?', (path,))
    for file in scanned:
        if file.kind not in SCOPES or previous.get(file.path) == (file.kind, file.content_hash):
            continue
        omission = None
        try:
            content = source_text(root, file.path, file.content_hash)
        except ValueError as exc:
            omission = str(exc)
        conn.execute('INSERT INTO evidence_files VALUES(?,?,?,?)',
                     (file.path, file.kind, file.content_hash, omission))
        if omission:
            continue
        lines = content.splitlines(keepends=True)
        heading, start, chunk, size = '', 1, [], 0

        def save():
            if chunk:
                conn.execute('INSERT INTO evidence_fts VALUES(?,?,?,?,?,?)',
                             (file.path, file.kind, start, start + len(chunk) - 1, heading,
                              words(file.path + ' ' + heading + ' ' + ''.join(chunk))))

        for number, line in enumerate(lines, 1):
            new_heading = file.kind == 'doc' and line.startswith('#')
            if chunk and (len(chunk) >= CHUNK_LINES or size + len(line.encode()) > CHUNK_BYTES or new_heading):
                save()
                chunk, size, start = [], 0, number
            if new_heading:
                heading = line.strip()[:200]
            # An oversized line is indexed in a bounded prefix, but reads remain exact.
            chunk.append(line.encode()[:CHUNK_BYTES].decode('utf-8', errors='ignore'))
            size += len(line.encode())
        save()
    conn.execute("INSERT OR REPLACE INTO index_metadata VALUES('lexical_version', ?)", (VERSION,))
    conn.commit()
