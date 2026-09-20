#!/usr/bin/env python3
"""Sprint 3 A/B experiment runner (frozen 120-session design).

    run_sprint3_ab.py preflight                 validate every frozen input; print the planned sessions and spending controls; start NOTHING
    run_sprint3_ab.py schedule                  print the deterministic schedule
    run_sprint3_ab.py mock RUN_DIR              run the whole schedule against synthetic transcripts (no network, no model)
    run_sprint3_ab.py prepare                   build the isolated checkouts and the release runtime outside the repository
    run_sprint3_ab.py isolation                 prove the filesystem boundary with sentinel files through the real sandbox mechanism
    run_sprint3_ab.py pilot RUN_DIR             4 paid sessions (needs --i-authorise-spending)
    run_sprint3_ab.py run RUN_DIR               the frozen schedule, resumable (needs --i-authorise-spending)
    run_sprint3_ab.py summarize RUN_DIR OUT_DIR

Scheduling (ab/schedule.py), process execution (ab/execute.py), transcript parsing (ab/transcript.py), grading (grade_sprint3_answers.py)
and orchestration/resume (ab/run.py, ab/store.py) are separate modules; only `pilot` and `run` call the model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ab.config import ROOT, build_prompt, load_config, load_tasks, sha256_bytes  # noqa: E402
from ab.execute import IBWD_BLOCKED, IBWD_TOOLS, RealExecutor, real, sandbox_profile, tools_for  # noqa: E402
from ab.mock import MockExecutor  # noqa: E402
from ab.run import run_schedule  # noqa: E402
from ab.schedule import build_schedule  # noqa: E402
from ab.store import Store  # noqa: E402

GATE = ROOT / "benchmarks" / "SPRINT3_gate_definition.md"


def git(*a, cwd=ROOT):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def pilot_sessions(cfg, tasks, schedule):
    """Recorded rule (chosen before any result exists): the alphabetically first HEADLINE repository of each language, its `Q1` task, both
    conditions, repeat 1. Not chosen by expected savings; outside the 120-session dataset (its own experiment id, its own run dir)."""
    rule = cfg["pilot_rule"]
    picks = []
    for lang in ("python", "typescript"):
        t = sorted((t for t in tasks if t["language"] == lang and t["condition_scope"] == "headline" and t["stratum"] == rule["stratum"]), key=lambda t: t["repository"])[0]
        picks.append(t)
    out = []
    for t in picks:
        for cond in ("baseline", "ibwd"):
            out.append({"session_id": f"{cfg['experiment_id']}-pilot/{t['repository']}/{t['task_id']}/{cond}/r1", "experiment_id": cfg["experiment_id"] + "-pilot",
                        "repo": t["repository"], "task_id": t["task_id"], "stratum": t["stratum"], "condition": cond, "repeat": 1, "scope": t["condition_scope"], "position": len(out) + 1})
    return out


def layout(cfg):
    root = Path(cfg["layout"]["workspace_root"])
    return {"checkouts": str(root / "checkouts"), "workspaces": str(root / "workspaces"), "release": str(root / "release"),
            "deny": [str(Path(p)) for p in cfg["layout"]["deny"]] + [str(root)]}


def preflight(cfg, tasks, strict=True) -> int:
    problems = []
    sched = build_schedule(cfg["experiment_id"], tasks, cfg["repeats"], cfg["seed"])
    n = {"headline": sum(s["scope"] == "headline" for s in sched), "control": sum(s["scope"] != "headline" for s in sched)}
    if (len(sched), n["headline"], n["control"]) != (120, 96, 24):
        problems.append(f"schedule shape {len(sched)}/{n} is not 120/96/24")
    if cfg.get("status") != "frozen":
        problems.append("configuration is not frozen")
    gate_sha = sha256_bytes(GATE.read_bytes())
    if cfg.get("gate_definition_sha256") != gate_sha:
        problems.append("gate definition differs from the frozen hash")
    for t in tasks:
        b = build_prompt(t)
        if any(x in b for x in ("accepted_paths", "expected", "ground_truth")):
            problems.append(f"prompt of {t['task_id']} mentions an answer/ground-truth field")
    cli = subprocess.run([cfg["claude_bin"], "--version"], capture_output=True, text=True).stdout.strip()
    if not cli.startswith(cfg["cli_version"]):
        problems.append(f"CLI version {cli!r} != frozen {cfg['cli_version']}")
    if cfg.get("ibwd_commit") and git("cat-file", "-t", cfg["ibwd_commit"]) != "commit":
        problems.append("frozen ibwd_commit not found")
    if cfg.get("ibwd_commit") and git("status", "--porcelain"):
        problems.append("working tree is dirty (freeze requires a clean tree)")
    lay = layout(cfg)
    for p in (lay["release"], lay["checkouts"]):
        if not Path(p).exists():
            problems.append(f"missing {p}: run `prepare`")
    allowed_b, dis_b = tools_for("baseline")
    allowed_i, dis_i = tools_for("ibwd")
    per = cfg["max_budget_usd_per_session"]
    print(json.dumps({
        "experiment_id": cfg["experiment_id"], "config_sha256": cfg["_config_sha256"], "gate_definition_sha256": gate_sha, "model": cfg["model"],
        "effort": cfg.get("effort") or "CLI default (flag omitted)", "cli_version": cli, "seed": cfg["seed"],
        "planned_sessions": len(sched), "headline_sessions": n["headline"], "celery_control_sessions": n["control"], "repeats": cfg["repeats"],
        "tasks": len(tasks), "baseline_tools": allowed_b, "ibwd_tools": allowed_i, "ibwd_hidden_tools": IBWD_BLOCKED,
        "spending_controls": {"max_budget_usd_per_session": per, "max_infra_retries": cfg["max_infra_retries"], "timeout_s_per_session": cfg["timeout_s_per_session"],
                              "worst_case_usd_no_retries": round(len(sched) * per, 2), "worst_case_usd_with_all_retries": round(len(sched) * per * (1 + cfg["max_infra_retries"]), 2),
                              "expected_usd_at_historical_mean_0.096": round(len(sched) * 0.096, 2), "pilot_worst_case_usd": 4 * per},
        "problems": problems}, indent=1))
    return 1 if problems and strict else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["preflight", "schedule", "mock", "prepare", "isolation", "pilot", "run", "summarize"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--i-authorise-spending", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config) if a.config else load_config()
    tasks = load_tasks(cfg)
    by_id = {t["task_id"]: t for t in tasks}
    for t in tasks:
        cfg.setdefault("_task_hashes", {})[t["task_id"]] = hashlib.sha256(json.dumps(t, sort_keys=True).encode()).hexdigest()
    if a.cmd == "preflight":
        return preflight(cfg, tasks)
    sched = build_schedule(cfg["experiment_id"], tasks, cfg["repeats"], cfg["seed"])
    if a.cmd == "schedule":
        for s in sched:
            print(s["position"], s["session_id"])
        return 0
    if a.cmd == "mock":
        store = Store(Path(a.paths[0]))
        print(run_schedule(sched, by_id, MockExecutor(by_id), store, cfg, limit=a.limit))
        return 0
    if a.cmd == "summarize":
        from summarize_sprint3_ab import main as smain
        sys.argv = ["summarize", a.paths[0], a.paths[1]] + (["--"] if False else []) + ([str(a.config)] if a.config else [])
        return smain()
    if a.cmd in ("prepare", "isolation"):
        import ab_prepare
        return getattr(ab_prepare, a.cmd)(cfg, tasks, layout(cfg))
    if not a.i_authorise_spending:
        print("REFUSED: this command spends money; re-run with --i-authorise-spending after the owner authorises it.", file=sys.stderr)
        return 2
    if preflight(cfg, tasks) != 0:
        return 1
    ex = RealExecutor(cfg, layout(cfg))
    store = Store(Path(a.paths[0]))
    if a.cmd == "pilot":
        ps = pilot_sessions(cfg, tasks, sched)
        print(run_schedule(ps, by_id, ex, store, cfg))
    else:
        print(run_schedule(sched, by_id, ex, store, cfg, limit=a.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
