"""Deterministic, balanced schedule. Pure functions: no I/O, no clock, no randomness beyond the frozen seed."""
from __future__ import annotations

import hashlib

CONDITIONS = ("baseline", "ibwd")


def session_id(experiment_id: str, repo: str, task_id: str, condition: str, repeat: int) -> str:
    return f"{experiment_id}/{repo}/{task_id}/{condition}/r{repeat}"


def build_schedule(experiment_id: str, tasks: list[dict], repeats: int, seed: str) -> list[dict]:
    """One entry per (task, condition, repeat).

    Runs are grouped in blocks (task, repeat) whose two condition runs are adjacent; block order is a seeded hash order, and the first
    condition of each block alternates by a seeded hash so that each condition goes first in (almost exactly) half the blocks. This
    spreads time-dependent effects (cache warmth, load) over both conditions.
    """
    blocks = [(t, r) for t in tasks for r in range(1, repeats + 1)]
    h = lambda t, r, tag: hashlib.sha256(f"{seed}:{tag}:{t['task_id']}:{r}".encode()).hexdigest()
    blocks.sort(key=lambda b: h(b[0], b[1], "order"))
    first_flags = sorted(range(len(blocks)), key=lambda i: h(blocks[i][0], blocks[i][1], "first"))
    baseline_first = set(first_flags[: len(blocks) // 2])          # exactly floor(n/2) blocks start with the baseline
    out = []
    for i, (t, r) in enumerate(blocks):
        order = CONDITIONS if i in baseline_first else CONDITIONS[::-1]
        for cond in order:
            out.append({"session_id": session_id(experiment_id, t["repository"], t["task_id"], cond, r), "experiment_id": experiment_id,
                        "repo": t["repository"], "task_id": t["task_id"], "stratum": t["stratum"], "condition": cond, "repeat": r,
                        "scope": t["condition_scope"], "position": len(out) + 1})
    return out
