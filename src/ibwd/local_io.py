"""Small local persistence helpers; no model or network dependencies."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import time


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ibwd-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def report_lock(path: Path, timeout: float = 1.0):
    """Serialize report publishers on supported POSIX hosts; OS releases on crash."""
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        until = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise TimeoutError("Another IBWD report is being published; retry on the next lifecycle event.")
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)
