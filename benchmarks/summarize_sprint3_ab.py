#!/usr/bin/env python3
"""Sprint 3 A/B summarizer: applies the FROZEN gate (benchmarks/SPRINT3_gate_definition.md) to the saved attempts. No model calls.

    T[i,c,r] = input + cache_creation + cache_read + output   (final result.usage only)
    m[i,c]   = median over the 3 repeats
    R        = sum_i w[i]*m[i,baseline] / sum_i w[i]*m[i,ibwd]        (ratio of weighted medians; NOT an average of per-task ratios)
    w[i]     = 1 / (#languages x #repos in language x #strata in repo x #tasks in stratum)     over the HEADLINE repositories

Celery (the negative control) is computed with the same formula and reported separately. The verdict is GO only for a COMPLETE dataset
(every scheduled session has a valid measurement); an invalid measurement (missing usage, timeout, budget exhaustion, ...) is retained as
a failed answer and makes the dataset INCOMPLETE: a provisional ratio is still printed, clearly labelled, and no passing verdict is issued.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab.config import load_config, load_tasks  # noqa: E402
from ab.schedule import build_schedule  # noqa: E402
from ab.store import Store  # noqa: E402

GATE_RATIO = 3.0


def weights(tasks: list[dict]) -> dict[str, float]:
    """Hierarchical equal weights over the headline tasks (language > repository > stratum > task)."""
    by = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for t in tasks:
        by[t["language"]][t["repository"]][t["stratum"]].append(t["task_id"])
    w = {}
    for lang, repos in by.items():
        for repo, strata in repos.items():
            for stratum, ids in strata.items():
                for tid in ids:
                    w[tid] = 1.0 / (len(by) * len(repos) * len(strata) * len(ids))
    return w


def _terminal(attempts: list[dict]) -> dict:
    ok = [a for a in attempts if a["status"] == "ok"]
    return ok[-1] if ok else attempts[-1]


def wmedian_ratio(tasks: list[dict], med: dict, w: dict, field: str) -> float | None:
    num = sum(w[t["task_id"]] * med[(t["task_id"], "baseline")][field] for t in tasks if (t["task_id"], "baseline") in med)
    den = sum(w[t["task_id"]] * med[(t["task_id"], "ibwd")][field] for t in tasks if (t["task_id"], "ibwd") in med)
    return num / den if den else None


def summarize(cfg: dict, tasks: list[dict], attempts: list[dict]) -> dict:
    schedule = build_schedule(cfg["experiment_id"], tasks, cfg["repeats"], cfg["seed"])
    expected = {s["session_id"] for s in schedule}
    by_session = defaultdict(list)
    for a in attempts:
        by_session[a["session_id"]].append(a)
    problems = []
    unknown = sorted(set(by_session) - expected)
    if unknown:
        problems.append(f"{len(unknown)} attempt(s) for sessions outside the schedule")
    missing = sorted(expected - set(by_session))
    if missing:
        problems.append(f"{len(missing)} scheduled session(s) have no attempt")
    terminal = {sid: _terminal(v) for sid, v in by_session.items() if sid in expected}
    invalid = sorted(sid for sid, a in terminal.items() if a["status"] != "ok")
    if invalid:
        problems.append(f"{len(invalid)} session(s) without a valid measurement: " + ", ".join(f"{s} ({terminal[s]['status']})" for s in invalid[:10]))
    complete = not problems

    def tokens_table(subset: list[dict], field_fn):
        med, counts = {}, {}
        for t in subset:
            for cond in ("baseline", "ibwd"):
                vals = [field_fn(terminal[s["session_id"]]) for s in schedule if s["task_id"] == t["task_id"] and s["condition"] == cond
                        and s["session_id"] in terminal and terminal[s["session_id"]]["status"] == "ok"]
                counts[(t["task_id"], cond)] = len(vals)
                if vals:
                    med[(t["task_id"], cond)] = {"total": statistics.median(vals)}
        return med, counts

    tok = lambda a: a["tokens"]
    outtok = lambda a: a["usage"]["output_tokens"]
    headline = [t for t in tasks if t["condition_scope"] == "headline"]
    celery = [t for t in tasks if t["condition_scope"] != "headline"]
    out = {"experiment_id": cfg["experiment_id"], "sessions_expected": len(expected), "sessions_with_attempts": len(terminal), "attempts_total": len(attempts),
           "complete": complete, "problems": problems, "gate_ratio": GATE_RATIO}

    def block(subset):
        w = weights(subset)
        med, counts = tokens_table(subset, tok)
        omed, _ = tokens_table(subset, outtok)
        med = {k: {"total": v["total"]} for k, v in med.items()}
        for k, v in omed.items():
            med.setdefault(k, {})["output"] = v["total"]
        cover = all(counts.get((t["task_id"], c), 0) == cfg["repeats"] for t in subset for c in ("baseline", "ibwd"))
        R = wmedian_ratio(subset, med, w, "total") if all("total" in med.get(k, {}) for k in [(t["task_id"], c) for t in subset for c in ("baseline", "ibwd")]) else None
        Ro = wmedian_ratio(subset, med, w, "output") if R is not None else None
        rows = []
        for t in subset:
            b, i = med.get((t["task_id"], "baseline"), {}), med.get((t["task_id"], "ibwd"), {})
            rows.append({"task_id": t["task_id"], "repo": t["repository"], "stratum": t["stratum"], "weight": w[t["task_id"]], "median_tokens_baseline": b.get("total"),
                         "median_tokens_ibwd": i.get("total"), "paired_ratio": (b["total"] / i["total"]) if b.get("total") and i.get("total") else None,
                         "valid_repeats_baseline": counts.get((t["task_id"], "baseline")), "valid_repeats_ibwd": counts.get((t["task_id"], "ibwd"))})
        strata = {}
        for stratum in sorted({t["stratum"] for t in subset}):
            sub = [t for t in subset if t["stratum"] == stratum]
            ws = {t["task_id"]: 1.0 for t in sub}
            strata[stratum] = wmedian_ratio(sub, med, ws, "total") if all("total" in med.get(k, {}) for k in [(t["task_id"], c) for t in sub for c in ("baseline", "ibwd")]) else None
        repos = {}
        for repo in sorted({t["repository"] for t in subset}):
            sub = [t for t in subset if t["repository"] == repo]
            ws = {t["task_id"]: 1.0 for t in sub}
            repos[repo] = wmedian_ratio(sub, med, ws, "total") if all("total" in med.get(k, {}) for k in [(t["task_id"], c) for t in sub for c in ("baseline", "ibwd")]) else None
        sess = [terminal[s["session_id"]] for s in schedule if s["task_id"] in {t["task_id"] for t in subset} and s["session_id"] in terminal]
        allatt = [a for a in attempts if a["task_id"] in {t["task_id"] for t in subset}]

        def cond_stats(cond):
            ss = [a for a in sess if a["condition"] == cond]
            at = [a for a in allatt if a["condition"] == cond]
            n_ok = sum(1 for a in ss if a["status"] == "ok" and a["grade"]["correct"])
            return {"sessions": len(ss), "correct": n_ok, "correctness": (n_ok / len(ss)) if ss else None,
                    "failed_attempts": sum(1 for a in at if a["status"] != "ok"), "cost_usd_all_attempts": round(sum(a["cost_usd"] or 0 for a in at), 4),
                    "mean_tool_calls": statistics.mean(a["n_tool_calls"] for a in ss) if ss else None,
                    "median_latency_s": statistics.median(a["elapsed_s"] for a in ss) if ss else None}
        small = [t for t in subset if t["stratum"] == "small"]
        return {"ratio": R, "output_token_ratio": Ro, "all_medians_from_3_valid_repeats": cover, "tasks": rows, "by_stratum": strata, "by_repo": repos,
                "baseline": cond_stats("baseline"), "ibwd": cond_stats("ibwd"), "small_function_ratio": strata.get("small") if small else None}

    out["headline"], out["celery_control"] = block(headline), block(celery)
    R = out["headline"]["ratio"]
    hb, hi = out["headline"]["baseline"]["correctness"], out["headline"]["ibwd"]["correctness"]
    out["provisional_ratio_note"] = None if complete else "PROVISIONAL: the dataset is incomplete or contains invalid measurements; no passing verdict is issued."
    if not complete:
        out["verdict"] = "INCOMPLETE"
    elif R is None:
        out["verdict"] = "INCOMPLETE"
    elif hi is not None and hb is not None and hi < hb:
        out["verdict"] = "STOP (IBWD correctness below baseline)"
    elif R < GATE_RATIO:
        out["verdict"] = f"STOP (R = {R:.3f} < {GATE_RATIO})"
    else:
        out["verdict"] = "GO"
    return out


def write_outputs(summary: dict, attempts: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sprint_3_summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    with open(out_dir / "sprint_3_results.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_id", "repo", "task_id", "stratum", "condition", "repeat", "attempt", "status", "valid", "correct", "tokens", "input_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens", "cost_usd", "tool_calls", "elapsed_s"])
        for a in attempts:
            u = a.get("usage") or {}
            w.writerow([a["session_id"], a["repo"], a["task_id"], a["stratum"], a["condition"], a["repeat"], a["attempt"], a["status"], a["valid_measurement"],
                        (a["grade"] or {}).get("correct"), a["tokens"], u.get("input_tokens"), u.get("cache_creation_input_tokens"), u.get("cache_read_input_tokens"),
                        u.get("output_tokens"), a["cost_usd"], a["n_tool_calls"], a["elapsed_s"]])


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: summarize_sprint3_ab.py RUN_DIR OUT_DIR [CONFIG.json]")
    cfg = load_config(Path(sys.argv[3])) if len(sys.argv) > 3 else load_config()
    tasks = load_tasks(cfg)
    attempts = Store(Path(sys.argv[1])).all_attempts()
    s = summarize(cfg, tasks, attempts)
    write_outputs(s, attempts, Path(sys.argv[2]))
    print(json.dumps({k: s[k] for k in ("verdict", "complete", "problems", "sessions_expected", "sessions_with_attempts")}, indent=1))
    print("headline ratio (weighted medians):", s["headline"]["ratio"], "| celery control:", s["celery_control"]["ratio"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
