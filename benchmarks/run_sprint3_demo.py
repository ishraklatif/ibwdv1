"""Appendix D demo protocol driver for Sprint 3 (call graph + path finding).

Same methodology as run_sprint1_demo.py / run_sprint2_demo.py: each demo question runs in a FRESH
`claude -p` subprocess, twice — once restricted to Read/Glob/Grep (baseline),
once with the ibwd MCP server's Sprint 1-3 tools also available. Uses
--strict-mcp-config so unrelated globally-configured MCP servers don't
pollute token counts. Saves raw stream-json transcripts + a summary JSON per
run; does NOT auto-grade correctness (hand-graded separately against the
ground truth in tasks/sprint_3_tasks.yaml).

Costs real API money. Not part of the test suite; run manually.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MCP_CONFIG = Path(os.environ.get("IBWD_BENCH_MCP_CONFIG", REPO / "benchmarks" / "ibwd_mcp_config.json"))

# TARGET_REPO is the directory `claude` explores (cwd for the subprocess).
# Point this at an isolated snapshot (e.g. a git worktree at a fixed commit)
# so the benchmark script's own output files don't contaminate what later
# runs discover while scanning -- see Sprint 1's methodology note.
TARGET_REPO = Path(os.environ.get("IBWD_BENCH_TARGET_REPO", REPO)).resolve()

# The baseline condition can run in its own snapshot so it never sees the IBWD
# graph (.ibwd/) that the ibwd condition's snapshot needs to be pre-scanned with.
BASELINE_REPO = Path(os.environ.get("IBWD_BENCH_BASELINE_REPO", TARGET_REPO)).resolve()

# Optional per-run spend cap (passed to `claude --max-budget-usd`).
MAX_BUDGET_USD = os.environ.get("IBWD_BENCH_MAX_BUDGET_USD")

# OUT_DIR is where raw transcripts/summaries are written -- always OUTSIDE
# TARGET_REPO for the same contamination reason.
OUT_DIR = Path(os.environ.get("IBWD_BENCH_OUT_DIR", REPO / "benchmarks" / "raw" / "sprint_3")).resolve()
RAW_DIR = OUT_DIR
RAW_DIR.mkdir(parents=True, exist_ok=True)

TASKS = [
    {"id": "sprint3_q1", "question": "Which functions in src/ directly call `upsert_edge`?"},
    {
        "id": "sprint3_q2",
        "question": "Which functions defined in this repo's src/ does `run_scan` (src/ibwd/scan.py) directly call?",
    },
    # Supplementary (Sprint 3 gate investigation, NOT part of the gate): same shape as q2
    # on a larger function, to test whether q2's low ratio is specific to a small function.
    {
        "id": "sprint3_q2b",
        "question": "Which functions and classes defined in this repo's src/ does `rebuild_reference_edges` (src/ibwd/graph/resolution.py) directly call?",
    },
    {"id": "sprint3_q3", "question": "Trace the call chain from `ibwd_scan` to `upsert_edge` if one exists."},
    {
        "id": "sprint3_q4",
        "question": "Is there a call chain from `upsert_edge` to `ibwd_scan`? If so, trace it.",
    },
]

BASELINE_ALLOWED = "Read,Glob,Grep"
BUILTIN_TOOLS = "Read,Glob,Grep"
IBWD_ALLOWED = (
    "Read,Glob,Grep,"
    "mcp__ibwd__ibwd_find_files,mcp__ibwd__ibwd_scan,"
    "mcp__ibwd__ibwd_find_symbol,mcp__ibwd__ibwd_list_symbols,"
    "mcp__ibwd__ibwd_callers,mcp__ibwd__ibwd_dependents,mcp__ibwd__ibwd_trace_path"
)
COMMON_DISALLOWED = "Bash,Write,Edit,Task,ToolSearch,WebFetch,WebSearch"

REAL_TOOLS = {
    "Read", "Glob", "Grep",
    "mcp__ibwd__ibwd_find_files", "mcp__ibwd__ibwd_scan",
    "mcp__ibwd__ibwd_find_symbol", "mcp__ibwd__ibwd_list_symbols",
    "mcp__ibwd__ibwd_callers", "mcp__ibwd__ibwd_dependents", "mcp__ibwd__ibwd_trace_path",
}


def run_condition(task_id: str, question: str, condition: str, run: int = 0, tag: str = "") -> dict:
    allowed = IBWD_ALLOWED if condition == "ibwd" else BASELINE_ALLOWED
    cmd = [
        "claude", "-p", question,
        "--tools", BUILTIN_TOOLS,  # limits built-in tool *schemas* too (~-79% fixed startup tokens vs allowedTools alone)
        "--allowedTools", allowed,
        "--disallowedTools", COMMON_DISALLOWED,
        "--strict-mcp-config",
        "--output-format", "stream-json", "--verbose",
    ]
    if condition == "ibwd":
        cmd += ["--mcp-config", str(MCP_CONFIG)]
    if MAX_BUDGET_USD:
        cmd += ["--max-budget-usd", MAX_BUDGET_USD]

    print(f"  running {task_id} [{condition}] ...", file=sys.stderr)
    cwd = TARGET_REPO if condition == "ibwd" else BASELINE_REPO
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300)

    raw_path = RAW_DIR / f"{task_id}_{condition}{tag}.jsonl"
    raw_path.write_text(proc.stdout)
    if proc.returncode != 0:
        print(f"    !! exit {proc.returncode}: {proc.stderr[:500]}", file=sys.stderr)

    tool_calls: list[str] = []
    result_text = None
    usage: dict = {}
    cost = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") == "assistant":
            for block in d.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls.append(block.get("name"))
        if d.get("type") == "result":
            result_text = d.get("result")
            usage = d.get("usage", {})
            cost = d.get("total_cost_usd")

    real_calls = [t for t in tool_calls if t in REAL_TOOLS]
    summary = {
        "task_id": task_id,
        "condition": condition,
        "run": run,
        "question": question,
        "all_tool_calls": tool_calls,
        "real_tool_calls": real_calls,
        "real_tool_call_count": len(real_calls),
        "tokens_in": usage.get("input_tokens"),
        "tokens_out": usage.get("output_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cost_usd": cost,
        "result_text": result_text,
    }
    (RAW_DIR / f"{task_id}_{condition}{tag}_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    # IBWD_BENCH_TASKS="sprint3_q4,sprint3_q1" runs just those tasks (saves money on re-runs).
    only = {t for t in os.environ.get("IBWD_BENCH_TASKS", "").split(",") if t}
    # IBWD_BENCH_REPEATS=5 repeats each task/condition pair (files get an _rN suffix).
    repeats = int(os.environ.get("IBWD_BENCH_REPEATS", "1"))
    tasks_by_repeat = {t["id"]: repeats for t in TASKS}
    for spec in os.environ.get("IBWD_BENCH_REPEATS_BY_TASK", "").split(","):  # e.g. "sprint3_q2b=3"
        if "=" in spec:
            tid, n = spec.split("=")
            tasks_by_repeat[tid] = int(n)
    results = []
    for task in TASKS:
        if only and task["id"] not in only:
            continue
        n = tasks_by_repeat[task["id"]]
        for run in range(n):
            for condition in ("baseline", "ibwd"):
                tag = f"_r{run + 1}" if n > 1 else ""
                results.append(run_condition(task["id"], task["question"], condition, run + 1, tag))

    print("\n=== Sprint 3 demo — raw results ===")
    for r in results:
        print(
            f"{r['task_id']:12} {r['condition']:9} "
            f"real_tool_calls={r['real_tool_call_count']:>2} {r['real_tool_calls']} "
            f"tokens_in={r['tokens_in']} tokens_out={r['tokens_out']} "
            f"cost=${(r['cost_usd'] or 0):.4f}"
        )
        print(f"  -> {r['result_text']!r}\n")

    (RAW_DIR / ("all_summaries.json" if not only else "partial_summaries.json")).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
