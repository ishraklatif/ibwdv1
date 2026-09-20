"""Runtime call tracer, loaded in EVERY Python process (and thread) via PYTHONPATH=<this dir>.

Records observed production->production call edges as (caller symbol, callee symbol) using the profiler's `call` events, then writes one
JSON per process at exit. Inactive unless IBWD_TRACE_OUT is set. Environment:
  IBWD_TRACE_OUT    directory for trace-<pid>.json and start-<pid> markers (a start marker without a trace file = an uninstrumented exit)
  IBWD_TRACE_ROOT   absolute path of the repository root inside the container (e.g. /work)
  IBWD_TRACE_SCOPE  path of the manifest JSON: only included_files are production
Symbol ids follow the static graph: `file::qualname`; nested functions keep their qualname in `original` and are projected to the
enclosing symbol (text before the first `.<locals>.`) in `edges`, like the Sprint 3 comparison view. Module-level code is the file.
"""
import atexit
import json
import os
import sys
import threading

OUT = os.environ.get("IBWD_TRACE_OUT")
if OUT:
    ROOT = os.environ["IBWD_TRACE_ROOT"].rstrip("/") + "/"
    with open(os.environ["IBWD_TRACE_SCOPE"]) as fh:
        SCOPE = set(json.load(fh)["included_files"])
    _ids = {}
    edges, originals, seen = {}, {}, set()

    def _sym(code):
        key = code
        hit = _ids.get(key, 0)
        if hit != 0:
            return hit
        name = code.co_filename
        result = None
        if name.startswith(ROOT):
            rel = name[len(ROOT):]
            if rel in SCOPE:
                q = code.co_qualname
                if q == "<module>":
                    result = (rel, rel)
                else:
                    if q.startswith("<") and "<locals>" not in q:      # <lambda>, <genexpr> at module level
                        result = (rel, rel)
                    else:
                        proj = q.split(".<locals>.")[0]
                        result = (f"{rel}::{proj}", f"{rel}::{q}")
        _ids[key] = result
        return result

    def _profile(frame, event, arg):
        if event != "call":
            return
        callee = _sym(frame.f_code)
        if callee is None:
            return
        back = frame.f_back
        if back is None:
            return
        caller = _sym(back.f_code)
        seen.add(callee[0])
        if caller is None:
            return
        k = (caller[0], callee[0])
        edges[k] = edges.get(k, 0) + 1
        if caller[0] != caller[1] or callee[0] != callee[1]:
            originals.setdefault(k, (caller[1], callee[1]))

    def _install():
        sys.setprofile(_profile)
        threading.setprofile(_profile)

    def _flush():
        sys.setprofile(None)
        try:
            with open(os.path.join(OUT, f"trace-{os.getpid()}.json"), "w") as fh:
                json.dump({"pid": os.getpid(), "argv": sys.argv[:3],
                           "edges": [{"source": a, "target": b, "count": n, "original": originals.get((a, b))} for (a, b), n in edges.items()],
                           "observed_symbols": sorted(seen)}, fh)
        except Exception:
            pass

    _orig_exit = os._exit

    def _exit_with_flush(code):          # forked workers (multiprocessing, parallel builds) leave through os._exit, skipping atexit
        _flush()
        _orig_exit(code)

    os._exit = _exit_with_flush

    def _after_fork():
        edges.clear(); originals.clear(); seen.clear()
        open(os.path.join(OUT, f"start-{os.getpid()}"), "w").close()
        _install()

    open(os.path.join(OUT, f"start-{os.getpid()}"), "w").close()
    atexit.register(_flush)
    os.register_at_fork(after_in_child=_after_fork)
    _install()
