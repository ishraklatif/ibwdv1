"""Orchestration: schedule -> execute -> parse -> grade -> save; resumable. The executor is injected (real, or mock without network)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grade_sprint3_answers import grade  # noqa: E402

from .config import build_prompt
from .store import FINAL_INVALID, RETRYABLE, Store
from .transcript import parse_transcript


def classify(proc, parsed: dict) -> str:
    """One status per attempt. A measurement is `ok` only with a final result event carrying all four usage fields."""
    if proc.timed_out:
        return "timeout"
    if parsed["status"] in ("ok", "budget_exhausted", "error_result", "usage_missing", "conflicting_results"):
        return parsed["status"]
    if proc.exit_code not in (0, None) and not parsed["tool_calls"] and parsed["events"] <= 1:
        return "infra_failure"                 # crashed / no network before any model output: the only kind that may be rerun
    return "nonzero_exit" if proc.exit_code not in (0, None) else parsed["status"]


def run_attempt(session: dict, spec: dict, executor, store: Store, cfg: dict) -> dict:
    n = store.next_attempt_number(session["session_id"])
    store.mark_started(session["session_id"], n)
    proc = executor(session, build_prompt(spec))
    parsed = parse_transcript(proc.stdout)
    status = classify(proc, parsed)
    grading = grade(spec, parsed["result_text"] or "") if status == "ok" else None
    rec = {
        "session_id": session["session_id"], "experiment_id": session["experiment_id"], "repo": session["repo"], "task_id": session["task_id"],
        "stratum": session["stratum"], "condition": session["condition"], "repeat": session["repeat"], "scope": session["scope"], "attempt": n,
        "status": status, "valid_measurement": status == "ok", "counts_as_failed_answer": status != "ok" or not grading["correct"],
        "tokens": parsed["tokens"] if status == "ok" else None, "usage": parsed["usage"], "cost_usd": parsed["cost_usd"],
        "tokens_unavailable_reason": None if status == "ok" else status,
        "exit_code": proc.exit_code, "timed_out": proc.timed_out, "elapsed_s": round(proc.elapsed_s, 3), "duration_ms": parsed["duration_ms"],
        "tool_calls": parsed["tool_calls"], "n_tool_calls": len(parsed["tool_calls"]), "tool_inventory": parsed["tool_inventory"],
        "mcp_servers": parsed["mcp_servers"], "file_accesses": parsed["file_accesses"], "malformed_lines": parsed["malformed_lines"],
        "grade": grading, "config_sha256": cfg["_config_sha256"], "task_spec_sha256": cfg.get("_task_hashes", {}).get(session["task_id"]),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    store.save(rec, proc.stdout, proc.stderr)
    return rec


def run_schedule(schedule: list[dict], tasks_by_id: dict, executor, store: Store, cfg: dict, limit: int | None = None, on_event=print,
                 max_total_usd: float | None = None) -> dict:
    """`max_total_usd` is a hard ceiling over ALL attempts in the run directory, retries included: an attempt starts only if the worst-case money already
    committed plus this attempt's own per-session cap fits under it; otherwise the run stops (nothing is skipped or replaced)."""
    done = skipped = attempts = 0
    per = float(cfg["max_budget_usd_per_session"])
    for session in schedule:
        if store.is_complete(session["session_id"], cfg["max_infra_retries"]):
            skipped += 1
            continue
        if limit is not None and done >= limit:
            break
        while not store.is_complete(session["session_id"], cfg["max_infra_retries"]):
            if max_total_usd is not None:
                c = store.committed_spend(per)
                if c["committed_usd"] + per > max_total_usd + 1e-9:
                    on_event(f"STOP: total budget ${max_total_usd:.2f} would be exceeded (committed ${c['committed_usd']:.2f} + next attempt cap ${per:.2f})")
                    return {"completed_now": done, "skipped_already_complete": skipped, "attempts_made": attempts, "stopped_by_total_budget": True, "committed": c}
            rec = run_attempt(session, tasks_by_id[session["task_id"]], executor, store, cfg)
            attempts += 1
            on_event(f"[{session['position']:>3}/{len(schedule)}] {session['session_id']} a{rec['attempt']} {rec['status']}"
                     + (f" T={rec['tokens']}" if rec["tokens"] is not None else ""))
        done += 1
    return {"completed_now": done, "skipped_already_complete": skipped, "attempts_made": attempts,
            **({"committed": store.committed_spend(per)} if max_total_usd is not None else {})}
