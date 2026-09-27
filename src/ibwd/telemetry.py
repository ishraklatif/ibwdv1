"""Bounded, content-free server observations. Connection IDs are not session IDs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextvars import ContextVar

# The mutable request record also reaches synchronous tools in worker threads.
retrieval_observation: ContextVar[dict | None] = ContextVar('ibwd_retrieval_observation', default=None)

TOOLS = frozenset({"ibwd_scan", "ibwd_find_files", "ibwd_find_symbol", "ibwd_list_symbols",
                   "ibwd_callers", "ibwd_dependents", "ibwd_trace_path", "ibwd_context", "ibwd_read", "ibwd_impact", "ibwd_compiler_evidence", "ibwd_artifact_read"})
MAX_EVENTS = 10000


def key(value) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def tool_name(name):
    if not isinstance(name, str):
        return None
    if name in TOOLS:
        return name
    for prefix in ("mcp__ibwd__", "mcp_ibwd.", "ibwd."):
        if name.startswith(prefix) and name[len(prefix):] in TOOLS:
            return name[len(prefix):]
    return None


def _connect(repo: Path):
    folder = repo / ".ibwd/usage"
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "events.sqlite3", timeout=0.2)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, observation_id TEXT, phase TEXT, data TEXT)")
    return conn


def append_event(repo: Path, event: dict) -> None:
    conn = _connect(repo)
    try:
        with conn:
            conn.execute("INSERT INTO events(observation_id, phase, data) VALUES(?,?,?)",
                         (event["observation_id"], event["phase"], json.dumps(event, separators=(",", ":"))))
            conn.execute("DELETE FROM events WHERE seq <= (SELECT COALESCE(MAX(seq),0)-? FROM events)", (MAX_EVENTS,))
    finally:
        conn.close()


def read_events(repo: Path) -> list[dict]:
    path = repo / ".ibwd/usage/events.sqlite3"
    if not path.exists():
        return []
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    try:
        return [json.loads(row[0]) for row in conn.execute("SELECT data FROM events ORDER BY seq")]
    finally:
        conn.close()


class ObservedServerMixin:
    """Wrap the SDK dispatch method, including argument validation and tool failures.

    stdio has one connection per server process. SDK 2 supplies request context;
    SDK 1 compatibility uses get_context when available. Missing metadata stays null.
    """

    async def call_tool(self, name, arguments, *args, **kwargs):
        if name not in TOOLS:
            return await super().call_tool(name, arguments, *args, **kwargs)
        context = kwargs.get("context") or (args[0] if args else None)
        if context is None and hasattr(self, "get_context"):
            context = self.get_context()
        # Direct in-process calls are tests/library use, not an observed MCP connection.
        try:
            request = context.request_context
        except (AttributeError, ValueError, LookupError):
            return await super().call_tool(name, arguments, *args, **kwargs)
        if not hasattr(self, "_ibwd_connection"):
            self._ibwd_connection = uuid.uuid4().hex
        peer = getattr(request.session, "client_params", None)
        info = getattr(peer, "client_info", None) or getattr(peer, "clientInfo", None)
        client_name = getattr(info, "name", None)
        client_version = getattr(info, "version", None)
        event = {"schema_version": 1, "observation_id": uuid.uuid4().hex,
                 "phase": "invoked", "timestamp": time.time(), "process_id": os.getpid(),
                 "connection_id": self._ibwd_connection,
                 "request_id_hash": key(request.request_id) if request.request_id is not None else None,
                 "client_identity_hash": key((client_name, client_version)) if client_name else None,
                 "tool": name, "index_generation": None, "duration_ms": None,
                 "response_bytes": None, "result_count": None, "truncated": None,
                 "status": "pending", "session_key": None}
        root = Path.cwd()

        def record(value):
            try:
                append_event(root, value)
            except (OSError, sqlite3.Error, ValueError):
                pass  # Observability must never turn a successful tool into a failure.

        record(event)
        started = time.monotonic()
        freshness = {}
        token = retrieval_observation.set(freshness)
        try:
            result = await super().call_tool(name, arguments, *args, **kwargs)
        except BaseException:
            record(event | freshness | {"phase": "completed", "status": "error",
                            "duration_ms": round((time.monotonic() - started) * 1000, 3)})
            raise
        finally:
            retrieval_observation.reset(token)
        event.update(freshness)
        status = "error" if getattr(result, "is_error", getattr(result, "isError", False)) else "success"
        try:
            if hasattr(result, "meta"):
                result.meta = dict(result.meta or {}) | {"ibwd": {"schema_version": 1, "observation_id": event["observation_id"]}}
            serialized = result.model_dump(mode="json", by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else None
        except (TypeError, ValueError, AttributeError):
            serialized = None  # SDK variants must not break a successful retrieval.
        data = serialized.get("structuredContent", serialized.get("structured_content")) if serialized else None
        if data is None and serialized:
            content = serialized.get('content', [])
            if len(content) == 1 and content[0].get('type') == 'text':
                try:
                    data = json.loads(content[0]['text'])
                except (ValueError, KeyError, TypeError):
                    pass
        if isinstance(data, dict) and "result" in data:
            data = data["result"]
        if isinstance(data, dict):
            event['index_generation'] = data.get('index_generation') or event['index_generation']
            semantic = data.get('semantic')
            if isinstance(semantic, dict) and semantic.get('status') in {'ready', 'fallback', 'disabled'}:
                # Reasons may contain local paths/runtime output; retain only the category.
                event['semantic_status'] = semantic['status']
            event['evidence_files'] = len(data['files']) if isinstance(data.get('files'), dict) else None
        if isinstance(data, dict) and data.get('schema_version') == 2:
            budget = (arguments or {}).get('max_bytes', 16384)
            if name in {'ibwd_context', 'ibwd_read'}:
                budget = min(budget, 4 * (arguments or {}).get('budget_tokens', 2000 if name == 'ibwd_context' else 1000))
            if len(json.dumps(serialized, separators=(',', ':'), ensure_ascii=False).encode()) > budget:
                record(event | {'phase': 'completed', 'status': 'error',
                                'duration_ms': round((time.monotonic() - started) * 1000, 3)})
                raise ValueError('Serialized MCP response exceeds max_bytes; narrow the query or increase the budget.')
        count = len(data) if isinstance(data, list) else (len(data['items']) if isinstance(data, dict) and isinstance(data.get('items'), list) else None)
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and data[0].get("empty_result"):
            count = 0
        record(event | {"phase": "completed", "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 3),
                        "response_bytes": len(json.dumps(serialized, separators=(",", ":"), ensure_ascii=False).encode()) if serialized else None,
                        "result_count": count,
                        "truncated": data.get("truncated", data.get("examples_truncated")) if isinstance(data, dict) else None})
        return result
