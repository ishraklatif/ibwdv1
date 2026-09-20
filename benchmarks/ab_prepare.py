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
    from ab.execute import mcp_config
    (rel / "mcp_config.json").write_text(json.dumps(mcp_config(str(rel / "bin" / "python")), indent=1))
    (rel / "RELEASE_COMMIT").write_text(run(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip() + "\n")
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
        "scratch directory": (Path("/private/tmp/claude-501/-Users-ishraklatif-Documents-claude-agent-ibwd") / ".iso_sentinel", False),
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
        p = subprocess.run(["sandbox-exec", "-p", ex.profile_for("baseline", fake_ws), cfg["claude_bin"], "--version"], capture_output=True, text=True, cwd=fake_ws)
        results.append({"condition": "baseline", "target": "claude --version under sandbox", "readable": p.returncode == 0, "expected_readable": True, "pass": p.returncode == 0 and cfg["cli_version"] in p.stdout})
    finally:
        for p in created:
            p.unlink(missing_ok=True)
        shutil.rmtree(fake_ws, ignore_errors=True)
        shutil.rmtree(other_ws, ignore_errors=True)
    print(json.dumps(results, indent=1))
    (ROOT / "benchmarks" / "evidence" / "isolation_check.json").write_text(json.dumps({"mechanism": "macOS sandbox-exec profile generated by ab.execute.sandbox_profile (the runner's real mechanism)", "results": results}, indent=1) + "\n")
    return 0 if all(r["pass"] for r in results) else 1


def inventory(cfg, tasks, layout) -> int:
    """What the model is actually given, without any model call: the CLI's init event (network denied, killed on sight) and the MCP server's
    own schemas and a live call over the release runtime."""
    import asyncio
    import select
    import time

    from ab.execute import IBWD_TOOLS, build_command, tools_for

    ex = RealExecutor(cfg, layout)
    ws = Path(layout["workspaces"]) / "iso__inventory"
    if ws.exists():
        shutil.rmtree(ws)
    subprocess.run(["cp", "-cR", str(Path(layout["checkouts"]) / "scrapy" / "ibwd"), str(ws)], check=True)
    out = {"cli_init": {}}
    try:
        for cond in ("baseline", "ibwd"):
            cmd = build_command(cfg, cond, "Say hi.", str(Path(layout["release"]) / "mcp_config.json") if cond == "ibwd" else None)
            p = subprocess.Popen(["sandbox-exec", "-p", ex.profile_for(cond, ws) + "(deny network*)\n"] + cmd, cwd=ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            t0, init = time.time(), None
            while time.time() - t0 < 90 and init is None:
                if select.select([p.stdout], [], [], 1)[0]:
                    line = p.stdout.readline()
                    if not line:
                        break
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") == "system" and d.get("subtype") == "init":
                        init = d
            p.kill(); p.wait()
            allowed, _ = tools_for(cond)
            out["cli_init"][cond] = {"advertised": sorted(init["tools"]) if init else None, "expected": sorted(allowed),
                                     "exact_match": bool(init) and sorted(init["tools"]) == sorted(allowed), "mcp_servers": init.get("mcp_servers") if init else None}

        async def mcp_side():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(command=str(Path(layout["release"]) / "bin" / "python"), args=["-m", "ibwd.mcp.server"], cwd=str(ws))
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = {t.name: getattr(t, "inputSchema", None) or t.input_schema for t in (await s.list_tools()).tools}
                    defaults = {n: tools[n]["properties"].get("include_candidates", {}).get("default") for n in ("ibwd_callers", "ibwd_dependents", "ibwd_trace_path")}
                    live = await s.call_tool("ibwd_callers", {"symbol": "no_such_symbol_xyz"})
                    hit = await s.call_tool("ibwd_callers", {"symbol": "add_http_if_no_scheme"})
                    return {"server_tools": sorted(tools), "include_candidates_defaults": defaults,
                            "live_empty_call_has_scope": "indexed production scope" in json.dumps([c.text for c in live.content]),
                            "live_call_returns_resolved_only": all('"resolution_status": "resolved"' in c.text or "empty_result" in c.text for c in hit.content)}

        out["mcp_server"] = asyncio.run(mcp_side())
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    ok = (all(v["exact_match"] for v in out["cli_init"].values()) and all(d is False for d in out["mcp_server"]["include_candidates_defaults"].values())
          and out["mcp_server"]["live_empty_call_has_scope"] and out["mcp_server"]["live_call_returns_resolved_only"]
          and len(out["mcp_server"]["server_tools"]) == 7)
    out["pass"] = ok
    print(json.dumps(out, indent=1))
    (ROOT / "benchmarks" / "evidence" / "tool_inventory_check.json").write_text(json.dumps(out, indent=1) + "\n")
    return 0 if ok else 1
