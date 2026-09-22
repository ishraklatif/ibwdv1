"""Local, opt-in session reports driven by client lifecycle hooks.

No model calls, transcript copies, daemon, or client launches. Hook stdout stays
empty so reporting cannot inject instructions or trigger another agent turn.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

from ibwd.usage import analyze_log


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ibwd-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def hook_config(client: str, repo: Path) -> dict:
    """Generate POSIX command hooks using the installed interpreter."""
    command = shlex.join([str(Path(sys.executable).absolute()), "-m", "ibwd.cli",
                          "usage-hook", "--client", client, "--repo", str(repo.resolve())])
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command,
                                         "timeout": timeout}]}]
                      for event, timeout in (("Stop", 30), ("SessionEnd", 3))}}


def merge_hook_config(config: dict, client: str, repo: Path) -> dict:
    """Merge command entries without replacing unrelated settings or hooks."""
    if not isinstance(config, dict) or not isinstance(config.get("hooks", {}), dict):
        raise ValueError("Existing hook configuration must contain a hooks object.")
    hooks = config.setdefault("hooks", {})
    for event, groups in hook_config(client, repo)["hooks"].items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError(f"Existing {event} hooks must be an array.")
        if groups[0] not in existing:
            existing.append(groups[0])
    return config


def install_hooks(client: str, repo: Path) -> Path:
    """Merge only our hooks; preserve existing settings and do not grant trust."""
    repo = repo.resolve()
    relative = ".codex/hooks.json" if client == "codex" else ".claude/settings.local.json"
    path = repo / relative
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Refusing to install hooks through a symlink.")
    original = path.read_text(encoding="utf-8") if path.exists() else None
    config = merge_hook_config(json.loads(original) if original is not None else {}, client, repo)
    updated = json.dumps(config, indent=2) + "\n"
    if original != updated:
        if original is not None:
            # Retain the first pre-install configuration, never overwrite it.
            backup = path.with_name(path.name + ".ibwd-backup")
            try:
                with backup.open("x", encoding="utf-8") as stream:
                    stream.write(original)
            except FileExistsError:
                pass
        _atomic_write(path, updated)
    return path


def render_report(report: dict) -> str:
    usage = report["usage"]
    tokens = f"{usage['total_tokens']:,}" if usage else "not recorded"
    evidence = report.get("instruction_evidence", {})
    guidance = "observed" if evidence.get("ibwd_mentioned") else "not observed (not proof it was absent)"
    lines = ["# IBWD session report", "",
             f"Client: {report['client']}",
             f"Snapshot: {report['capture_event']} (recorded data may be incomplete)",
             f"Last recorded activity: {report['last_timestamp'] or 'unknown'}", "",
             f"Direct IBWD calls: {report['direct_ibwd_calls']}",
             f"Recorded tokens: {tokens}",
             f"IBWD mention in recognized instruction records: {guidance}", "",
             "## Recorded tool calls", ""]
    lines.extend(f"- `{name}`: {count}" for name, count in report["tool_calls"].items())
    if not report["tool_calls"]:
        lines.append("No direct tool calls recorded.")
    lines += ["", "## Interpretation", "",
              "Zero direct IBWD calls does not establish why it was unused or whether it was available.",
              "Shell/orchestration calls and child sessions may be missing from these counts.",
              "Instruction evidence is limited to recognized instruction records, not ordinary mentions.",
              "Task success, server availability, task category and savings are not inferred.",
              "Token totals describe this session, not tokens saved by IBWD."]
    if report["warnings"]:
        lines += ["", "## Recording warnings", ""]
        lines.extend(f"- {warning}" for warning in report["warnings"])
    return "\n".join(lines) + "\n"


def capture_session(event: dict, client: str, repo: Path) -> Path | None:
    """Consume a client's actual transcript path, never guess the newest log."""
    if not isinstance(event, dict):
        raise ValueError("Expected a hook event object.")
    if event.get("hook_event_name") not in {"Stop", "SessionEnd"}:
        raise ValueError("Expected a Stop or SessionEnd hook event.")
    if event.get("agent_id") or event.get("agent_type"):
        return None  # Parent and child usage must not be silently combined.
    repo = repo.resolve()
    cwd = event.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("Hook event has no absolute working directory.")
    if not Path(cwd).resolve().is_relative_to(repo):
        raise ValueError("Hook working directory is outside the configured repository.")
    transcript = event.get("transcript_path")
    if not isinstance(transcript, str) or not Path(transcript).is_absolute():
        raise ValueError("Client did not supply an absolute transcript path.")
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Client did not supply a session identity.")
    report = analyze_log(Path(transcript), client)
    # Match transcript identity to the event before persisting an attribution.
    import hashlib

    expected = hashlib.sha256((client + ":" + session_id).encode()).hexdigest()[:20]
    if report["session_key"] != expected:
        raise ValueError("Hook session identity does not match the transcript.")
    report["capture_event"] = event["hook_event_name"]
    report["provisional"] = True  # Even SessionEnd does not promise final accounting.
    folder = repo / ".ibwd" / "usage" / "sessions"
    name = f"{client}-{report['session_key']}"
    destination = folder / f"{name}.json"
    _atomic_write(destination, json.dumps(report, indent=2) + "\n")
    _atomic_write(folder / f"{name}.md", render_report(report))
    # Human-facing shortcut; JSON session reports remain unique for aggregation.
    _atomic_write(folder.parent / f"latest-{client}.md", render_report(report))
    return destination
