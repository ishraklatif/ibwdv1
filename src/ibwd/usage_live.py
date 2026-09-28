"""Refresh registered or identity-verified local transcripts without ending a session."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

_discovered = {}


def identity(path: Path, client: str):
    """Inspect bounded headers only; never select a transcript by modification time."""
    with path.open('rb') as stream:
        read = 0
        candidate = (None, None)
        for _ in range(128):
            line = stream.readline(1024 * 1024)
            read += len(line)
            if not line or read > 2 * 1024 * 1024:
                break
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get('isSidechain') or event.get('agentId') or event.get('agent_id'):
                continue
            data = event.get('payload', {}) if client == 'codex' else event
            if not isinstance(data, dict):
                continue
            sid = data.get('id', data.get('session_id')) if event.get('type') == 'session_meta' else (
                data.get('sessionId') if client == 'claude' else None)
            if isinstance(sid, str) and sid:
                candidate = sid, data.get('cwd')
                if isinstance(candidate[1], str):
                    return candidate
    return candidate


def session_key(client, sid):
    return hashlib.sha256((client + ':' + sid).encode()).hexdigest()[:20]


def refresh_reports(repo: Path, client='auto', selected=None):
    """Refresh exact registered identities; discover only matching repository headers."""
    from ibwd.usage_hooks import capture_session
    repo = repo.resolve()
    folder = repo / '.ibwd/usage'
    sources = {}
    warnings = []
    for path in (folder / 'sources').glob('*.json'):
        try:
            source = json.loads(path.read_text())
            c, sk = source['client'], source['session_key']
            if c in {'codex', 'claude'} and (client == 'auto' or c == client) and (not selected or sk == selected):
                sources[(c, sk)] = source
        except (OSError, ValueError, KeyError, TypeError):
            warnings.append('A local transcript registration is unreadable.')
    cache_key = (str(repo), client, selected)
    if not sources or time.monotonic() - _discovered.get(cache_key, 0) > 30:
        roots = {'codex': Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'sessions',
                 'claude': Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude')) / 'projects'}
        for c, root in roots.items():
            if client != 'auto' and c != client:
                continue
            for path in root.glob('**/*.jsonl'):
                if 'subagents' in path.parts:
                    continue
                try:
                    sid, cwd = identity(path, c)
                    if not sid or not isinstance(cwd, str) or not Path(cwd).is_absolute():
                        continue
                    if not Path(cwd).resolve().is_relative_to(repo):
                        continue
                    sk = session_key(c, sid)
                    if selected and sk != selected:
                        continue
                    sources.setdefault((c, sk), dict(client=c, session_key=sk, transcript_path=str(path), cwd=cwd))
                except (OSError, ValueError):
                    continue
        _discovered[cache_key] = time.monotonic()
    updated = []
    for (c, sk), source in sources.items():
        try:
            path = Path(source['transcript_path'])
            sid, _ = identity(path, c)
            if not sid or session_key(c, sid) != sk:
                raise ValueError('Transcript identity changed')
            ended = source.get('ended_size') == path.stat().st_size
            saved = capture_session(dict(hook_event_name='Refresh', session_id=sid,
                                         cwd=source['cwd'], transcript_path=str(path), session_ended=ended), c, repo)
            if saved:
                updated.append(saved)
        except (OSError, ValueError, KeyError, TypeError, TimeoutError):
            warnings.append(f'{c} session {sk}: transcript could not be refreshed; showing saved data.')
    return dict(refreshed=len(updated), warnings=warnings)
