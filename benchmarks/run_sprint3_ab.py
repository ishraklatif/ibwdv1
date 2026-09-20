#!/usr/bin/env python3
"""Sprint 3 A/B experiment runner (frozen 120-session design).

    run_sprint3_ab.py preflight                 validate every frozen input; print the planned sessions and spending controls; start NOTHING
    run_sprint3_ab.py schedule                  print the deterministic schedule
    run_sprint3_ab.py mock RUN_DIR              run the whole schedule against synthetic transcripts (no network, no model)
    run_sprint3_ab.py prepare                   build the isolated checkouts and the release runtime outside the repository
    run_sprint3_ab.py inventory                 what the model is actually given (CLI init event, MCP schemas, live call), no model call
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


HARNESS_FILES = ["benchmarks/run_sprint3_ab.py", "benchmarks/ab_prepare.py", "benchmarks/grade_sprint3_answers.py", "benchmarks/summarize_sprint3_ab.py",
                 "benchmarks/ab/__init__.py", "benchmarks/ab/config.py", "benchmarks/ab/schedule.py", "benchmarks/ab/transcript.py", "benchmarks/ab/execute.py",
                 "benchmarks/ab/store.py", "benchmarks/ab/run.py", "benchmarks/ab/mock.py", "benchmarks/tools/build_task_specs.py"]


def file_hashes(files):
    return {f: sha256_bytes((ROOT / f).read_bytes()) for f in files}


def freeze(cfg, tasks, experiment_id):
    """Write the frozen configuration. The implementation commit it names is the commit that is CHECKED OUT and clean when this runs."""
    if git("status", "--porcelain"):
        print("REFUSED: dirty tree"); return 1
    from ab.config import PROMPT_TEMPLATE
    prep = json.loads((ROOT / "benchmarks/preparation_record.json").read_text())
    frozen = dict(cfg)
    frozen.pop("_config_sha256", None); frozen.pop("_task_hashes", None)
    frozen.update({
        "experiment_id": experiment_id, "status": "frozen", "frozen_at": subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True).stdout.strip(),
        "ibwd_commit": git("rev-parse", "HEAD"), "edge_build_version": prep["edge_build_version"], "tool_contract_version": 2,
        "release_runtime_commit": (Path(cfg["layout"]["workspace_root"]) / "release" / "RELEASE_COMMIT").read_text().strip(),
        "release_dependency_freeze_sha256": sha256_bytes((Path(cfg["layout"]["workspace_root"]) / "release-freeze.txt").read_bytes()),
        "gate_definition_sha256": sha256_bytes(GATE.read_bytes()),
        "prompt_template_sha256": sha256_bytes(PROMPT_TEMPLATE.encode()),
        "task_specs_index_sha256": sha256_bytes((ROOT / cfg["tasks_index"]).read_bytes()),
        "grader_and_harness_sha256": file_hashes(HARNESS_FILES),
        "graph_inputs": {r: {"repo_sha": v["repo_sha"], "ibwd_export_sha256": v["ibwd_export_sha256"], "oracle_export_sha256": v["oracle_export_sha256"],
                             "manifest_sha256_file": v["manifest_sha256_file"], "ground_truth_yaml_sha256": v["ground_truth_yaml_sha256"]} for r, v in prep["repositories"].items()},
        "aggregation": "ratio of weighted medians of T = input+cache_creation+cache_read+output from the final result.usage; hierarchical equal weights (gate §2-3); Celery separate",
        "grading": "benchmarks/grade_sprint3_answers.py (deterministic; exact ids; Q3 accepts any minimum-cost path)",
        "retry_policy": "only infrastructure failures (non-zero exit with no model output) are retried, at most max_infra_retries; timeouts, budget exhaustion, missing usage, "
                        "malformed output are final invalid measurements, retained and counted as failed answers; nothing is silently rerun, dropped or replaced",
        "conditions_detail": {"baseline": {"tools": tools_for("baseline")[0], "mcp": "none"},
                              "ibwd": {"tools": tools_for("ibwd")[0], "hidden_mcp_tools": IBWD_BLOCKED, "include_candidates": "off (tool default)", "mcp": "release runtime, stdio"}},
        "isolation": "macOS sandbox-exec profile per session (benchmarks/ab/execute.py); per-session APFS clone of the checkout; evidence in benchmarks/evidence/isolation_check.json",
    })
    (ROOT / "benchmarks/experiment/experiment.json").write_text(json.dumps(frozen, indent=1) + "\n")
    print("frozen", experiment_id, frozen["ibwd_commit"])
    return 0


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
    if cfg.get("ibwd_commit"):
        if git("diff", "--name-only", cfg["ibwd_commit"], "HEAD", "--", "src", "pyproject.toml"):
            problems.append("implementation (src/) changed since the frozen commit")
        rc = (Path(cfg["layout"]["workspace_root"]) / "release" / "RELEASE_COMMIT")
        if not rc.exists() or git("diff", "--name-only", rc.read_text().strip(), cfg["ibwd_commit"], "--", "src", "pyproject.toml"):
            problems.append("the release runtime was built from different implementation sources than the frozen commit")
        fz = Path(cfg["layout"]["workspace_root"]) / "release-freeze.txt"
        if not fz.exists() or sha256_bytes(fz.read_bytes()) != cfg.get("release_dependency_freeze_sha256"):
            problems.append("release dependency freeze differs from the frozen hash")
        for f, h in cfg.get("grader_and_harness_sha256", {}).items():
            if sha256_bytes((ROOT / f).read_bytes()) != h:
                problems.append(f"harness file changed since the freeze: {f}")
        from ab.config import PROMPT_TEMPLATE
        if sha256_bytes(PROMPT_TEMPLATE.encode()) != cfg.get("prompt_template_sha256"):
            problems.append("prompt template differs from the frozen hash")
        if sha256_bytes((ROOT / cfg["tasks_index"]).read_bytes()) != cfg.get("task_specs_index_sha256"):
            problems.append("task specification index differs from the frozen hash")
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
    ap.add_argument("cmd", choices=["freeze", "preflight", "schedule", "mock", "prepare", "isolation", "inventory", "pilot", "run", "summarize"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-total-usd", type=float, default=None, help="hard ceiling over ALL attempts in the run directory, retries included")
    ap.add_argument("--i-authorise-spending", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config) if a.config else load_config()
    tasks = load_tasks(cfg)
    by_id = {t["task_id"]: t for t in tasks}
    for t in tasks:
        cfg.setdefault("_task_hashes", {})[t["task_id"]] = hashlib.sha256(json.dumps(t, sort_keys=True).encode()).hexdigest()
    if a.cmd == "freeze":
        return freeze(cfg, tasks, a.paths[0])
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
    if a.cmd in ("prepare", "isolation", "inventory"):
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
        cap = a.max_total_usd if a.max_total_usd is not None else cfg["pilot_max_total_usd"]
        print(run_schedule(ps, by_id, ex, store, cfg, max_total_usd=cap))
    else:
        if a.max_total_usd is None:
            print("REFUSED: `run` needs an explicit --max-total-usd ceiling.", file=sys.stderr)
            return 2
        print(run_schedule(sched, by_id, ex, store, cfg, limit=a.limit, max_total_usd=a.max_total_usd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
