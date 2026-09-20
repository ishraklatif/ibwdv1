"""Environment preparation and the filesystem-boundary proof for the A/B experiment (no model calls)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ab.config import ROOT
from ab.execute import RealExecutor, real, sandbox_profile

CORPUS = Path("/Users/ishraklatif/Documents/IBWD_TEST_REPOS/corpus")
EVIDENCE = Path("/Users/ishraklatif/Documents/IBWD_TEST_REPOS/evidence")
UV = "/opt/homebrew/bin/uv"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def prepare(cfg, tasks, layout) -> int:
    if run(["git", "status", "--porcelain"], cwd=ROOT).stdout.strip():
        print("REFUSED: the working tree is dirty; the release runtime must be built from a committed tree")
        return 1
    ws = Path(layout["checkouts"]).parent
    ws.mkdir(parents=True, exist_ok=True)
    rel = Path(layout["release"])
    if not (rel / "bin" / "python").exists():
        py = run([str(ROOT / ".venv" / "bin" / "python"), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"]).stdout.strip()
        assert run([UV, "venv", str(rel), "--python", py]).returncode == 0
        r = run([UV, "pip", "install", "--python", str(rel / "bin" / "python"), str(ROOT)])
        assert r.returncode == 0, r.stderr
    freeze = run([UV, "pip", "freeze", "--python", str(rel / "bin" / "python")]).stdout
    (ws / "release-freeze.txt").write_text(freeze)
    report = {}
    for repo in sorted({t["repository"] for t in tasks}):
        sha = next(t["repo_sha"] for t in tasks if t["repository"] == repo)
        base = Path(layout["checkouts"]) / repo / "baseline"
        ib = Path(layout["checkouts"]) / repo / "ibwd"
        for d in (base, ib):
            if d.exists():
                shutil.rmtree(d)
        base.mkdir(parents=True)
        arch = subprocess.Popen(["git", "-C", str(CORPUS / repo), "archive", sha], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        subprocess.run(["tar", "-x", "-C", str(base)], stdin=arch.stdout, stderr=subprocess.DEVNULL)
        arch.wait()
        assert run(["cp", "-cR", str(base), str(ib)]).returncode == 0
        r = run([str(rel / "bin" / "python"), "-m", "ibwd.cli", "scan"], cwd=ib)
        assert r.returncode == 0, r.stderr
        out = ws / f"{repo}.export.json"
        r = run([str(rel / "bin" / "python"), "-m", "ibwd.cli", "export", str(out), "--repo-sha", sha], cwd=ib)
        assert r.returncode == 0, r.stderr
        mine = {(e["source"], e["target"], e["relation"], e["tier"], e["resolution_status"]) for e in json.loads(out.read_text())["edges"]}
        ev = json.loads((EVIDENCE / f"{repo}_ibwd.json").read_text())
        ref = {(e["source"], e["target"], e["relation"], e["tier"], e["resolution_status"]) for e in ev["edges"]}
        report[repo] = {"repo_sha": sha, "edges": len(mine), "identical_to_build27_evidence_export": mine == ref, "baseline_has_ibwd_dir": (base / ".ibwd").exists()}
        out.unlink()
    print(json.dumps(report, indent=1))
    return 0 if all(v["identical_to_build27_evidence_export"] and not v["baseline_has_ibwd_dir"] for v in report.values()) else 1


def _try_read(profile: str, path: str) -> tuple[bool, str]:
    p = subprocess.run(["sandbox-exec", "-p", profile, "/bin/cat", path], capture_output=True, text=True)
    return p.returncode == 0, p.stdout.strip()


def isolation(cfg, tasks, layout) -> int:
    ex = RealExecutor(cfg, layout)
    ws_root = Path(layout["checkouts"]).parent
    repo = "scrapy"
    token = "SENTINEL-" + uuid.uuid4().hex
    fake_ws = Path(layout["workspaces"]) / "iso__own"
    other_ws = Path(layout["workspaces"]) / "iso__other"
    for d in (fake_ws, other_ws):
        d.mkdir(parents=True, exist_ok=True)
    sentinels = {
        "own workspace file": (fake_ws / "own.txt", True),
        "repository ground truth": (ROOT / "benchmarks" / "ground_truth" / ".iso_sentinel", False),
        "oracle/evidence export": (EVIDENCE / ".iso_sentinel", False),
        "previous answers (run dir)": (ROOT / "benchmarks" / "experiment" / ".iso_sentinel", False),
        "other run's workspace": (other_ws / "answer.json", False),
        "other condition checkout (.ibwd graph)": (Path(layout["checkouts"]) / repo / "ibwd" / ".ibwd" / ".iso_sentinel", False),
        "scratch directory": (Path("/private/tmp/claude-501") / ".iso_sentinel", False),
    }
    created = []
    results = []
    try:
        for name, (p, _) in sentinels.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(token)
            created.append(p)
        for cond in ("baseline", "ibwd"):
            profile = ex.profile_for(cond, fake_ws)
            for name, (p, should_read) in sentinels.items():
                ok, out = _try_read(profile, str(p))
                leaked = ok and token in out
                results.append({"condition": cond, "target": name, "readable": leaked, "expected_readable": should_read, "pass": leaked == should_read})
        # the release runtime is readable only in the IBWD condition
        rel_py = str(Path(layout["release"]) / "bin" / "python")
        for cond, should in (("baseline", False), ("ibwd", True)):
            ok, _ = _try_read(ex.profile_for(cond, fake_ws), str(Path(layout["release"]) / "pyvenv.cfg"))
            results.append({"condition": cond, "target": "release runtime (pyvenv.cfg)", "readable": ok, "expected_readable": should, "pass": ok == should})
        # the CLI itself must still start under the profile
        p = subprocess.run(["sandbox-exec", "-p", ex.profile_for("baseline", fake_ws), cfg["claude_bin"], "--version"], capture_output=True, text=True)
        results.append({"condition": "baseline", "target": "claude --version under sandbox", "readable": p.returncode == 0, "expected_readable": True, "pass": p.returncode == 0 and cfg["cli_version"] in p.stdout})
    finally:
        for p in created:
            p.unlink(missing_ok=True)
        shutil.rmtree(fake_ws, ignore_errors=True)
        shutil.rmtree(other_ws, ignore_errors=True)
    print(json.dumps(results, indent=1))
    (ROOT / "benchmarks" / "evidence" / "isolation_check.json").write_text(json.dumps({"mechanism": "macOS sandbox-exec profile generated by ab.execute.sandbox_profile (the runner's real mechanism)", "results": results}, indent=1) + "\n")
    return 0 if all(r["pass"] for r in results) else 1
