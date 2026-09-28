"""One optional process-local embedding worker, stopped on timeout or shutdown."""
import atexit
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time

_lock = threading.Lock()
_process = None
_identity = None


def close():
    global _process, _identity
    if _process is not None:
        if _process.poll() is None:
            _process.kill()
        _process.wait()
        _process.stdin.close()
        _process.stdout.close()
    _process, _identity = None, None


atexit.register(close)


def encode(model_path, digest, task, dimensions, timeout):
    global _process, _identity
    if not _lock.acquire(blocking=False):
        raise ValueError('Local embedding worker busy')
    try:
        identity = (str(model_path), digest, dimensions)
        if _identity != identity or _process is None or _process.poll() is not None:
            close()
            worker = Path(__file__).parents[1] / 'resources/local-embeddings.py'
            env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false',
                       OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
            _process = subprocess.Popen([sys.executable, str(worker), '--serve', str(model_path)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env)
            os.set_blocking(_process.stdin.fileno(), False)
            os.set_blocking(_process.stdout.fileno(), False)
            _identity = identity
        deadline = time.monotonic() + timeout
        request = json.dumps(dict(texts=[task], dimensions=dimensions)).encode() + b'\n'
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(_process.stdin, selectors.EVENT_WRITE)
            while request:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise ValueError('Local embedding deadline exceeded')
                request = request[os.write(_process.stdin.fileno(), request):]
            selector.unregister(_process.stdin)
            selector.register(_process.stdout, selectors.EVENT_READ)
            while b'\n' not in output:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise ValueError('Local embedding deadline exceeded')
                block = os.read(_process.stdout.fileno(), 65536)
                if not block or len(output) + len(block) > 256000:
                    raise ValueError('Invalid local embedding worker response')
                output.extend(block)
        response = json.loads(output)
        if not isinstance(response, list):
            raise ValueError('Local embedding runtime unavailable or model incompatible')
        return response
    except (OSError, ValueError):
        close()
        raise
    finally:
        _lock.release()
