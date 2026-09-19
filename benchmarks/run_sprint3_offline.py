"""Offline (no-API, no-LLM) Sprint 3 check: payload size + correctness + edge audit.

What this measures: how many characters/tokens of *retrieval output* land in
the agent's context for each demo task — the IBWD tool's raw response vs. the
grep/Read output a grep-only agent would need to gather the same facts — plus
whether IBWD's answers match an independent ground truth computed with Python's
`ast` module (a different parser from the tree-sitter one IBWD uses).

What it does NOT measure: the agent's own reasoning/output tokens, or how many
extra reads a real agent does. Those are what the `claude -p` benchmark
(run_sprint3_demo.py) captures. Baselines here are deliberately *minimal* grep
sessions, so the ratios are conservative for IBWD; treat them as a floor on
retrieval-side savings, not a stand-in for the Appendix D result.

Usage: uv run python benchmarks/run_sprint3_offline.py   (from the repo root)
"""

from __future__ import annotations

import ast
import asyncio
import csv
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)  # the MCP tools index the current working directory

from ibwd.graph.database import connect  # noqa: E402
from ibwd.mcp.server import mcp  # noqa: E402
from ibwd.scan import run_scan  # noqa: E402

CSV_PATH = REPO / "benchmarks" / "sprint_3_offline_results.csv"


def tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Same yardstick on both sides."""
    return round(len(text) / 4)


def sh(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True).stdout


async def tool(name: str, args: dict) -> tuple[object, str]:
    """Call an IBWD MCP tool; return (parsed result, raw text the agent would see)."""
    result = await mcp.call_tool(name, args)
    raw = result.content[0].text
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"], raw
    return (structured if structured else json.loads(raw)), raw


# -- independent ground truth via ast ------------------------------------------


def ast_index(src_dir: Path):
    """{qualname_key: set(call names)} and defs {name: [qualname_key]} for Python under src/."""
    calls_by_fn: dict[str, set[str]] = {}
    defs: dict[str, list[str]] = defaultdict(list)

    for path in sorted(src_dir.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        tree = ast.parse(path.read_text())

        def visit(node: ast.AST, stack: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qual = ".".join([*stack, child.name])
                    key = f"{rel}::{qual}"
                    defs[child.name].append(key)
                    if isinstance(child, ast.ClassDef):
                        visit(child, [*stack, child.name])
                    else:
                        names: set[str] = set()
                        for sub in ast.walk(child):  # includes nested functions, like IBWD's owner attribution
                            if isinstance(sub, ast.Call):
                                fn = sub.func
                                if isinstance(fn, ast.Name):
                                    names.add(fn.id)
                                elif isinstance(fn, ast.Attribute):
                                    names.add(fn.attr)
                        calls_by_fn[key] = names
                else:
                    visit(child, stack)

        visit(tree, [])
    return calls_by_fn, defs


def name_of(key: str) -> str:
    return key.split("::", 1)[1].rsplit(".", 1)[-1]


# -- the demo tasks -----------------------------------------------------------


async def main() -> int:
    print(json.dumps(run_scan(REPO)))
    calls_by_fn, defs = ast_index(REPO / "src")
    rows: list[dict] = []
    failures: list[str] = []

    def record(task, ibwd_calls, ibwd_text, base_calls, base_text, correct, note=""):
        i_tok, b_tok = tokens(ibwd_text), tokens(base_text)
        rows.append(
            {
                "task": task,
                "ibwd_calls": ibwd_calls,
                "ibwd_chars": len(ibwd_text),
                "ibwd_tokens_est": i_tok,
                "baseline_calls": base_calls,
                "baseline_chars": len(base_text),
                "baseline_tokens_est": b_tok,
                "token_ratio": round(b_tok / i_tok, 2) if i_tok else 0,
                "correct": correct,
                "note": note,
            }
        )
        if not correct:
            failures.append(task)

    # q1: direct callers of upsert_edge -------------------------------------
    truth = {name_of(k) for k, called in calls_by_fn.items() if "upsert_edge" in called}
    got, raw = await tool("ibwd_callers", {"symbol": "upsert_edge"})
    got_names = {r["name"] for r in got}
    g1 = sh('grep -rn "upsert_edge" src')
    hit_files = sorted({line.split(":")[0] for line in g1.splitlines() if "def upsert_edge" not in line})
    g2 = sh("grep -n 'def ' " + " ".join(hit_files))  # to name the enclosing function of each hit
    record("q1 callers of upsert_edge", 1, raw, 2, g1 + g2, got_names == truth, f"truth={sorted(truth)}")

    # q2: what run_scan directly calls (repo-defined names) ------------------
    run_scan_key = "src/ibwd/scan.py::run_scan"
    truth = {n for n in calls_by_fn[run_scan_key] if n in defs and not n.startswith("__")}
    got, raw = await tool("ibwd_dependents", {"symbol": "run_scan", "file": "src/ibwd/scan.py"})
    got_names = {r["name"] for r in got}
    body = sh("sed -n '/^def run_scan/,$p' src/ibwd/scan.py")
    names_re = "|".join(sorted(truth))
    defs_grep = sh(f"grep -rn -E 'def ({names_re})\\b|class ({names_re})\\b' src")
    record("q2 dependents of run_scan", 1, raw, 2, body + defs_grep, got_names == truth, f"truth={sorted(truth)}")

    # q3: trace ibwd_scan -> upsert_edge (has a path) ------------------------
    got, raw = await tool("ibwd_trace_path", {"source": "ibwd_scan", "target": "upsert_edge"})
    hops = got["path"] or []
    edges_ok = bool(hops) and all(
        hops[i + 1]["name"] in calls_by_fn.get(f"{hops[i]['file']}::{hops[i]['name']}", set())
        for i in range(len(hops) - 1)
    )
    correct = edges_ok and hops[0]["name"] == "ibwd_scan" and hops[-1]["name"] == "upsert_edge"
    chain_greps = []
    for hop, follow in (("upsert_edge", "src/ibwd/graph/queries.py"), ("sync_files", "src/ibwd/scan.py"), ("run_scan", "src/ibwd/mcp/server.py")):
        chain_greps.append(sh(f'grep -rn "{hop}" src'))
        chain_greps.append(sh(f"grep -n 'def ' {follow}"))
    record(
        "q3 trace ibwd_scan->upsert_edge",
        1,
        raw,
        len(chain_greps),
        "".join(chain_greps),
        correct,
        " -> ".join(h["name"] for h in hops) + f" (every hop verified against ast: {edges_ok})",
    )

    # q4: no path in the reverse direction (true negative) --------------------
    got, raw = await tool("ibwd_trace_path", {"source": "upsert_edge", "target": "ibwd_scan"})
    upsert_body = sh("sed -n '/^def upsert_edge/,/^def get_node_by_path/p' src/ibwd/graph/database.py")
    record("q4 no path upsert_edge->ibwd_scan", 1, raw, 1, upsert_body, got.get("path") is None, "true negative")

    # -- report -----------------------------------------------------------------
    print("\n=== Payload + correctness (tokens are chars/4 estimates) ===")
    print(f"{'task':40} {'IBWD':>10} {'baseline':>14} {'ratio':>7}  ok")
    for r in rows:
        print(
            f"{r['task']:40} {r['ibwd_tokens_est']:>5}t/{r['ibwd_calls']}c {r['baseline_tokens_est']:>8}t/{r['baseline_calls']}c "
            f"{r['token_ratio']:>6}x  {'PASS' if r['correct'] else 'FAIL'}"
        )
        print(f"    {r['note']}")
    with CSV_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    # -- whole-repo edge audit ----------------------------------------------------
    conn = connect(REPO / ".ibwd" / "graph.db")
    edge_rows = conn.execute(
        """
        SELECT s.qualified_name AS src, s.file_path AS sfile, s.start_line AS sline,
               t.qualified_name AS tgt, t.name AS tname, e.confidence
        FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id
        WHERE e.relation = 'CALLS' AND s.file_path LIKE 'src/%' AND s.file_path LIKE '%.py'
        """
    ).fetchall()
    conn.close()

    checkable = [r for r in edge_rows if r["src"] in calls_by_fn]
    supported = [r for r in checkable if r["tname"] in calls_by_fn[r["src"]]]
    wrong = [r for r in checkable if r["tname"] not in calls_by_fn[r["src"]]]
    print("\n=== Precision audit (does the caller's ast body actually contain a call to the target's name?) ===")
    print(f"{len(supported)}/{len(checkable)} symbol-sourced CALLS edges supported "
          f"({len(edge_rows) - len(checkable)} module-level/file-sourced edges not checked)")
    for r in wrong:
        print(f"  UNSUPPORTED: {r['src']} -> {r['tgt']} ({r['confidence']})")

    have = {(r["src"], r["tgt"]) for r in edge_rows}
    expected, missed = 0, []
    for caller, names in calls_by_fn.items():
        for name in names:
            if name.startswith("__") or len(defs.get(name, [])) != 1:
                continue
            (target,) = defs[name]
            if target == caller:
                continue
            expected += 1
            if (caller, target) not in have:
                missed.append((caller, target))
    print("\n=== Recall audit (calls whose name matches exactly one repo definition) ===")
    print(f"{expected - len(missed)}/{expected} found")
    for caller, target in missed:
        print(f"  MISSED: {caller} -> {target}")

    low = [r for r in edge_rows if r["confidence"] < 0.9]
    print(f"\n=== Low-confidence (<0.90) edges for manual review: {len(low)} ===")
    for r in low:
        print(f"  {r['confidence']}: {r['src']} -> {r['tgt']}")

    print(f"\nwrote {CSV_PATH.relative_to(REPO)}")
    if failures:
        print("FAILED:", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
