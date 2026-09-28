"""Incremental transcript parsing; persist counters, not conversation content."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ibwd.usage import analyze_log

STATE_VERSION = 3
MAX_LINE_BYTES = 8 * 1024 * 1024


def _digest(stream, start, size):
    stream.seek(start)
    return hashlib.sha256(stream.read(size)).hexdigest()


def incremental_report(path: Path, client: str, checkpoint: Path, *, rebuild=False):
    try:
        old = json.loads(checkpoint.read_text())
    except (OSError, ValueError):
        old = {}
    if not isinstance(old, dict):
        old = {}
    if rebuild:
        old = {}
    with path.open("rb") as stream:
        import os
        stat = os.fstat(stream.fileno())
        offset = old.get("offset", 0)
        valid = (old.get("version") == STATE_VERSION and old.get("client") == client
                 and old.get("file_id") == [stat.st_dev, stat.st_ino]
                 and type(offset) is int and 0 <= offset <= stat.st_size
                 and isinstance(old.get("parser"), dict))
        if valid:
            # Detect replacement, truncation, same-size rewrites and boundary rewrites.
            valid = not (stat.st_size == old.get("size") and stat.st_mtime_ns != old.get("mtime_ns"))
            valid = valid and old.get("head") == _digest(stream, 0, min(offset, 4096))
            valid = valid and old.get("tail") == _digest(stream, max(0, offset - 4096), min(offset, 4096))
        state = old["parser"] if valid else {}
        offset = offset if valid else 0
        stream.seek(offset)
        processed = 0
        pending = False

        def lines():
            nonlocal offset, processed, pending
            while True:
                line = stream.readline(min(MAX_LINE_BYTES + 1, max(0, stat.st_size - stream.tell())))
                if not line:
                    break
                processed += len(line)
                if len(line) > MAX_LINE_BYTES:
                    # Drop one oversize record without retaining its private contents.
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(min(MAX_LINE_BYTES + 1, max(0, stat.st_size - stream.tell())))
                        processed += len(line)
                    if not line:
                        pending = True
                        break
                    offset = stream.tell()
                    yield "invalid oversized JSONL record"
                    continue
                if not line.endswith(b"\n"):
                    # Buffer only in this process; next event rereads from the last complete boundary.
                    pending = True
                    break
                offset = stream.tell()
                yield line.decode("utf-8", errors="replace")

        try:
            report = analyze_log(path, client, state=state, lines=lines())
        except (KeyError, TypeError, AttributeError):
            if valid:
                # A corrupt/incompatible saved reducer state is disposable. Retry from source.
                return incremental_report(path, client, checkpoint, rebuild=True)
            raise
        new = {"version": STATE_VERSION, "client": client, "file_id": [stat.st_dev, stat.st_ino],
               "offset": offset, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
               "head": _digest(stream, 0, min(offset, 4096)),
               "tail": _digest(stream, max(0, offset - 4096), min(offset, 4096)), "parser": state}
    if pending:
        report["warnings"].append("Incomplete final JSONL line buffered until a later lifecycle event.")
        report["observations"]["usage_incomplete"] = {"value": True, "source": "Incomplete final JSONL line."}
        report["comparable"] = False
    report["parser"] = {"version": STATE_VERSION, "resumed": valid, "bytes_read": processed,
                        "complete_offset": offset, "pending_line": pending}
    return report, new
