"""Appendix D demo protocol driver for Sprint 2.

Same methodology as run_sprint1_demo.py: each demo question runs in a FRESH
`claude -p` subprocess, twice — once restricted to Read/Glob/Grep (baseline),
once with the ibwd MCP server's Sprint 1+2 tools also available. Uses
--strict-mcp-config so unrelated globally-configured MCP servers don't
pollute token counts. Saves raw stream-json transcripts + a summary JSON per
run; does NOT auto-grade correctness (hand-graded separately against the
ground truth in tasks/sprint_2_tasks.yaml).

Costs real API money. Not part of the test suite; run manually.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MCP_CONFIG = REPO / "benchmarks" / "ibwd_mcp_config.json"

# TARGET_REPO is the directory `claude` explores (cwd for the subprocess).
# Point this at an isolated snapshot (e.g. a git worktree at a fixed commit)
# so the benchmark script's own output files don't contaminate what later
# runs discover while scanning -- see Sprint 1's methodology note.
TARGET_REPO = Path(os.environ.get("IBWD_BENCH_TARGET_REPO", REPO)).resolve()

# OUT_DIR is where raw transcripts/summaries are written -- always OUTSIDE
# TARGET_REPO for the same contamination reason.
OUT_DIR = Path(os.environ.get("IBWD_BENCH_OUT_DIR", REPO / "benchmarks" / "raw" / "sprint_2")).resolve()
RAW_DIR = OUT_DIR
RAW_DIR.mkdir(parents=True, exist_ok=True)

TASKS = [
    {"id": "sprint2_q1", "question": "Where is the `connect` function defined?"},
    {"id": "sprint2_q2", "question": "List every function defined in `src/ibwd/scanner/python.py`."},
    {"id": "sprint2_q3", "question": "Find a function named `main` -- how many are there and where?"},
]

BASELINE_ALLOWED = "Read,Glob,Grep"
IBWD_ALLOWED = (
    "Read,Glob,Grep,"
    "mcp__ibwd__ibwd_find_files,mcp__ibwd__ibwd_scan,"
    "mcp__ibwd__ibwd_find_symbol,mcp__ibwd__ibwd_list_symbols"
)
COMMON_DISALLOWED = "Bash,Write,Edit,Task,ToolSearch,WebFetch,WebSearch"

REAL_TOOLS = {
    "Read", "Glob", "Grep",
    "mcp__ibwd__ibwd_find_files", "mcp__ibwd__ibwd_scan",
    "mcp__ibwd__ibwd_find_symbol", "mcp__ibwd__ibwd_list_symbols",
}


def run_condition(task_id: str, question: str, condition: str) -> dict:
    allowed = IBWD_ALLOWED if condition == "ibwd" else BASELINE_ALLOWED
    cmd = [
        "claude", "-p", question,
        "--allowedTools", allowed,
        "--disallowedTools", COMMON_DISALLOWED,
        "--strict-mcp-config",
        "--output-format", "stream-json", "--verbose",
    ]
    if condition == "ibwd":
        cmd += ["--mcp-config", str(MCP_CONFIG)]

    print(f"  running {task_id} [{condition}] ...", file=sys.stderr)
    proc = subprocess.run(cmd, cwd=TARGET_REPO, capture_output=True, text=True, timeout=180)

    raw_path = RAW_DIR / f"{task_id}_{condition}.jsonl"
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
    (RAW_DIR / f"{task_id}_{condition}_summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    results = []
    for task in TASKS:
        for condition in ("baseline", "ibwd"):
            results.append(run_condition(task["id"], task["question"], condition))

    print("\n=== Sprint 2 demo — raw results ===")
    for r in results:
        print(
            f"{r['task_id']:12} {r['condition']:9} "
            f"real_tool_calls={r['real_tool_call_count']:>2} {r['real_tool_calls']} "
            f"tokens_in={r['tokens_in']} tokens_out={r['tokens_out']} "
            f"cost=${r['cost_usd']:.4f}"
        )
        print(f"  -> {r['result_text']!r}\n")

    (RAW_DIR / "all_summaries.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
