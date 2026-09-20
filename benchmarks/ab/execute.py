"""Process execution: build the command and the filesystem boundary, run it, capture stdout/stderr/exit/elapsed. No parsing, no grading."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

BASE_TOOLS = ["Read", "Glob", "Grep"]
IBWD_TOOLS = ["mcp__ibwd__ibwd_find_symbol", "mcp__ibwd__ibwd_callers", "mcp__ibwd__ibwd_dependents", "mcp__ibwd__ibwd_trace_path"]
IBWD_BLOCKED = ["mcp__ibwd__ibwd_scan", "mcp__ibwd__ibwd_find_files", "mcp__ibwd__ibwd_list_symbols"]
COMMON_DISALLOWED = ["Bash", "Write", "Edit", "Task", "ToolSearch", "WebFetch", "WebSearch", "NotebookEdit"]


@dataclass
class ProcessResult:
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_s: float
    timed_out: bool = False


def real(p) -> str:
    return os.path.realpath(str(p))


def sandbox_profile(deny: list[str], allow_rw: list[str], allow_ro: list[str]) -> str:
    """macOS sandbox profile: everything is allowed except the denied trees; the run's own workspace (and, for the IBWD condition, the
    release runtime) are re-allowed after the denies (the last matching rule wins). Paths are resolved (`/tmp` -> `/private/tmp`)."""
    lines = ["(version 1)", "(allow default)"]
    for p in deny:
        lines.append(f'(deny file-read* file-write* (subpath "{real(p)}"))')
    for p in allow_rw:
        lines.append(f'(allow file-read* file-write* (subpath "{real(p)}"))')
    for p in allow_ro:
        lines.append(f'(allow file-read* (subpath "{real(p)}"))')
    return "\n".join(lines) + "\n"


def tools_for(condition: str) -> tuple[list[str], list[str]]:
    """(allowed, disallowed) tool names for a condition."""
    if condition == "ibwd":
        return BASE_TOOLS + IBWD_TOOLS, COMMON_DISALLOWED + IBWD_BLOCKED
    return list(BASE_TOOLS), list(COMMON_DISALLOWED)


def mcp_config(release_python: str) -> dict:
    return {"mcpServers": {"ibwd": {"type": "stdio", "command": release_python, "args": ["-m", "ibwd.mcp.server"]}}}


def build_command(cfg: dict, condition: str, prompt: str, mcp_config_path: str | None) -> list[str]:
    allowed, disallowed = tools_for(condition)
    cmd = [cfg["claude_bin"], "-p", prompt, "--model", cfg["model"], "--no-session-persistence", "--strict-mcp-config",
           "--tools", ",".join(BASE_TOOLS), "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(disallowed),
           "--output-format", "stream-json", "--verbose", "--max-budget-usd", str(cfg["max_budget_usd_per_session"])]
    if cfg.get("effort"):
        cmd += ["--effort", cfg["effort"]]
    if condition == "ibwd":
        cmd += ["--mcp-config", mcp_config_path]
    return cmd


class RealExecutor:
    """Runs `claude -p` inside a per-run workspace under a sandbox profile that denies every sensitive tree."""

    def __init__(self, cfg: dict, layout: dict):
        self.cfg, self.layout = cfg, layout

    def profile_for(self, condition: str, workspace: Path) -> str:
        deny = list(self.layout["deny"])
        allow_ro = [self.layout["release"]] if condition == "ibwd" else []
        return sandbox_profile(deny, [str(workspace)], allow_ro)

    def prepare_workspace(self, session: dict) -> Path:
        src = Path(self.layout["checkouts"]) / session["repo"] / session["condition"]
        ws = Path(self.layout["workspaces"]) / session["session_id"].replace("/", "__")
        if ws.exists():
            shutil.rmtree(ws)
        subprocess.run(["cp", "-cR", str(src), str(ws)], check=True)          # APFS clone: a fresh, private copy for every session
        return ws

    def __call__(self, session: dict, prompt: str) -> ProcessResult:
        ws = self.prepare_workspace(session)
        try:
            cfg_path = str(Path(self.layout["release"]) / "mcp_config.json") if session["condition"] == "ibwd" else None   # read-only, outside the workspace
            cmd = ["sandbox-exec", "-p", self.profile_for(session["condition"], ws)] + build_command(self.cfg, session["condition"], prompt, cfg_path)
            t0 = time.monotonic()
            try:
                p = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=self.cfg["timeout_s_per_session"])
                return ProcessResult(p.stdout, p.stderr, p.returncode, time.monotonic() - t0)
            except subprocess.TimeoutExpired as e:
                out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
                err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
                return ProcessResult(out, err, None, time.monotonic() - t0, timed_out=True)
        finally:
            shutil.rmtree(ws, ignore_errors=True)
