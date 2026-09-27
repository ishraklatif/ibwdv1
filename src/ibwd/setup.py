"""Project-local onboarding and read-only readiness checks for both clients."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tomllib
import platform
from importlib.metadata import version
from importlib.resources import files

from ibwd.health import inspect_index
from ibwd.scan import run_scan
from ibwd.telemetry import TOOLS as OBSERVED_TOOLS
from ibwd.usage_hooks import _atomic_write, hook_config, merge_hook_config

START = "<!-- ibwd:routing:start -->"
END = "<!-- ibwd:routing:end -->"
ROUTING = """Use IBWD first for supported repository navigation, including navigation needed
for implementation, debugging and UI work:
- File discovery: ibwd_find_files; definitions: ibwd_find_symbol.
- File structure: ibwd_list_symbols; callers/importers: ibwd_callers.
- Dependencies: ibwd_dependents; connections: ibwd_trace_path.
- Task evidence: ibwd_context; exact source: ibwd_read with expected_hash.
  Context includes lexical test/doc/config scope; test matches are not verified coverage.
- Before applicable coding, debugging, or UI work, use at least one relevant IBWD lookup before searching or editing.
  For unfamiliar tasks start with ibwd_context; for known targets use the direct lookup. Use graph tools only when
  relationships matter. Do not call every tool by default. If unavailable or outside indexed scope, say so and search normally.
Retrieval checks freshness and refreshes automatically, including after edits or branch switches.
Use response_version=2 for bounded results, source hashes and generation-bound pagination.
Follow next_cursor with the same query when more evidence is needed; truncated results are incomplete.
ibwd_scan remains available for an explicit refresh; concurrent operations are serialized.
Use exact symbol_id values from discovery when names are ambiguous.
Read source before editing. Use text search for exact text,
runtime behavior and exhaustive searches. Symbols and edges cover production
Python and JS/JSX/TS/TSX; lexical context also covers tests/docs/config, while dynamic dispatch and inferred receivers remain incomplete.
An empty graph result never proves no uses or that deletion is safe.
If IBWD tools are unavailable or fail, say so briefly and use ordinary search;
do not silently claim IBWD was used. Do not make unrelated calls just to raise usage.
No paid model jobs or benchmarks. Normal work and local deterministic checks only."""
BLOCK = f"{START}\n## IBWD repository navigation\n\n{ROUTING}\n{END}"
TOOLS = OBSERVED_TOOLS | {"ibwd_artifact_save"}


def skill_path(client: str) -> str:
    return (".agents" if client == "codex" else ".claude") + "/skills/ibwd-navigation/SKILL.md"


def skill_source() -> str:
    return files("ibwd").joinpath("resources/ibwd-navigation/SKILL.md").read_text(encoding="utf-8")


def selected_clients(client: str) -> tuple[str, ...]:
    if client not in {"codex", "claude", "both"}:
        raise ValueError("Unknown client.")
    return ("codex", "claude") if client == "both" else (client,)


def server_config(repo: Path) -> dict:
    return {"command": str(Path(sys.executable).absolute()),
            "args": ["-m", "ibwd.mcp.server", "--repo", str(repo.resolve())]}


def _read(repo: Path, relative: str) -> str:
    path = repo / relative
    for part in (path, *path.parents):
        if part == repo:
            break
        if part.is_symlink():
            raise ValueError(f"Refusing project setup through a symlink: {relative}")
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _json(text: str) -> dict:
    value = json.loads(text) if text.strip() else {}
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON configuration object.")
    return value


def routing_file(repo: Path, client: str) -> str:
    if client == "claude":
        return "CLAUDE.md"
    # AGENTS.md is commonly a symlink shared with Claude instructions. Never
    # write through it: select the Codex override automatically on fresh clones.
    return "AGENTS.override.md" if (repo / "AGENTS.override.md").exists() or (repo / "AGENTS.md").is_symlink() else "AGENTS.md"


def merge_routing(text: str) -> str:
    if START not in text and END not in text:
        return text + ("\n\n" if text else "") + BLOCK + "\n"
    if text.count(START) != 1 or text.count(END) != 1 or text.index(END) < text.index(START):
        raise ValueError("Malformed IBWD routing markers; repair them before setup.")
    return text[:text.index(START)] + BLOCK + text[text.index(END) + len(END):]


def _server_matches(value: object, expected: dict) -> bool:
    return (isinstance(value, dict) and "url" not in value and value.get("type", "stdio") == "stdio"
            and all(value.get(k) == v for k, v in expected.items()))


def plan_setup(repo: Path, client: str = "both") -> dict[str, str]:
    """Validate all inputs before writing. Do not overwrite a conflicting server."""
    repo = repo.resolve()
    _read(repo, ".ibwd/manifest.json")  # Validate index location even on an idempotent setup.
    expected = server_config(repo)
    changes = {}
    for name in selected_clients(client):
        relative = ".codex/config.toml" if name == "codex" else ".mcp.json"
        original = _read(repo, relative)
        config = tomllib.loads(original) if name == "codex" else _json(original)
        key = "mcp_servers" if name == "codex" else "mcpServers"
        servers = config.get(key, {})
        if not isinstance(servers, dict):
            raise ValueError(f"Expected an MCP server table in {relative}.")
        existing = servers.get("ibwd")
        if "ibwd" in servers and not _server_matches(existing, expected):
            raise ValueError(f"Conflicting ibwd server in {relative}; reconcile its command/args with "
                             f"ibwd client-config --client {name} --repo PATH before setup. No files written.")
        if existing is None:
            if name == "codex":
                # Append only a new table, retaining comments and unrelated TOML byte-for-byte.
                updated = original + ("\n" if original else "") + "[mcp_servers.ibwd]\n" + "\n".join(
                    f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in expected.items()) + "\n"
                tomllib.loads(updated)  # Reject incompatible inline/dotted-table layouts before writes.
            else:
                config.setdefault(key, {})["ibwd"] = expected
                updated = json.dumps(config, indent=2) + "\n"
            changes[relative] = updated
        route = routing_file(repo, name)
        changes[route] = merge_routing(_read(repo, route))
        skill = skill_path(name)
        existing_skill = _read(repo, skill)
        if existing_skill and "<!-- ibwd:managed-skill:v1 -->" not in existing_skill:
            raise ValueError(f"Conflicting user-owned skill at {skill}; preserve or rename it before setup. No files written.")
        changes[skill] = skill_source()
        hooks_path = ".codex/hooks.json" if name == "codex" else ".claude/settings.local.json"
        hooks = merge_hook_config(_json(_read(repo, hooks_path)), name, repo)
        changes[hooks_path] = json.dumps(hooks, indent=2) + "\n"
    original_ignore = _read(repo, ".gitignore")
    ignore = original_ignore
    # Explicit root entries, appended after any earlier negation rules.
    for entry in ("/.ibwd/", "/.codex/config.toml", "/.codex/hooks.json", "/.mcp.json",
                  "/.claude/settings.local.json", "*.ibwd-backup"):
        if entry not in ignore.splitlines():
            ignore += ("\n" if ignore and not ignore.endswith("\n") else "") + entry + "\n"
    changes[".gitignore"] = ignore
    changes = {rel: text for rel, text in changes.items() if _read(repo, rel) != text}
    for rel in changes:
        _read(repo, ".ibwd/setup-backups/" + rel.replace("/", "__"))
    return changes


def setup_project(repo: Path, client: str = "both") -> dict:
    repo = repo.resolve()
    changes = plan_setup(repo, client)
    for relative, text in changes.items():
        path = repo / relative
        if path.exists():
            backup = repo / ".ibwd/setup-backups" / relative.replace("/", "__")
            backup.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(path.read_text(encoding="utf-8"))
        _atomic_write(path, text)
    run_scan(repo)  # Scan after instruction/config changes so freshness includes them.
    return {"changed_files": list(changes), "readiness": inspect_setup(repo, client)}


def inspect_setup(repo: Path, client: str = "both") -> dict:
    """Disk configuration is evidence of setup, never evidence of client loading."""
    repo = repo.resolve()
    expected = server_config(repo)
    clients = {}
    for name in selected_clients(client):
        problems = []
        route = routing_file(repo, name)
        reporting = ".codex/hooks.json" if name == "codex" else ".claude/settings.local.json"
        config_path = ".codex/config.toml" if name == "codex" else ".mcp.json"
        try:
            raw = _read(repo, config_path)
            config = tomllib.loads(raw) if name == "codex" else _json(raw)
            servers = config.get("mcp_servers" if name == "codex" else "mcpServers", {})
            server = servers.get("ibwd") if isinstance(servers, dict) else None
            if not _server_matches(server, expected):
                problems.append("Missing or mismatched MCP command/repository; run ibwd setup --repo PATH.")
            if isinstance(server, dict):
                if server.get("enabled") is False:
                    problems.append("IBWD is explicitly disabled in project configuration.")
                if server.get("disabled_tools") or ("enabled_tools" in server and
                        (not isinstance(server["enabled_tools"], list) or not TOOLS <= set(server["enabled_tools"]))):
                    problems.append("Project tool filters restrict IBWD tools; review enabled_tools/disabled_tools.")
            if not Path(expected["command"]).is_file() or not os.access(expected["command"], os.X_OK):
                problems.append("Configured Python interpreter is missing or not executable.")
            if BLOCK not in _read(repo, route):
                problems.append(f"Missing or outdated IBWD routing block in {route}; run ibwd setup --repo PATH.")
            if _read(repo, skill_path(name)) != skill_source():
                problems.append(f"Missing or outdated routing skill in {skill_path(name)}; run ibwd setup --repo PATH.")
            hook_settings = _json(_read(repo, reporting))
            hooks = hook_settings.get("hooks", {})
            for event, groups in hook_config(name, repo)["hooks"].items():
                if not isinstance(hooks, dict) or groups[0] not in hooks.get(event, []):
                    problems.append(f"Missing {event} reporting hook; run ibwd setup --repo PATH.")
            if name == "codex" and config.get("features", {}).get("hooks") is False:
                problems.append("Project config disables hooks; enable them to collect reports.")
            if name == "claude" and hook_settings.get("disableAllHooks") is True:
                problems.append("Claude project settings disable all hooks.")
            if name == "claude" and "ibwd" in hook_settings.get("disabledMcpjsonServers", []):
                problems.append("Claude project settings disable the ibwd MCP server.")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            problems.append(f"Cannot inspect project configuration: {exc}")
        clients[name] = {"local_status": "ready" if not problems else "needs_attention",
                         "routing_file": route, "problems": problems,
                         "skill_file": skill_path(name),
                         "automatic_report_seen": (repo / ".ibwd/usage" / f"latest-{name}.md").is_file(),
                         "runtime_connection": "unverified", "agent_adoption": "unverified"}
    index = inspect_index(repo)
    ready = index["status"] == "ready" and all(c["local_status"] == "ready" for c in clients.values())
    return {"repository": str(repo), "status": "ready" if ready else "needs_attention",
            "runtime": {"python": platform.python_version(), "platform": sys.platform,
                        "ibwd": version('ibwd'), "mcp": version('mcp'),
                        "support": "macOS/Linux/WSL; native Windows hooks and locking are unsupported",
                        "client_versions": "unverified; recorded from ordinary-work transcripts when supplied"},
            "clients": clients, "index": index,
            "next_steps": ["Restart/reconnect both clients and check IBWD in /mcp.",
                           "In Codex, review/trust hooks in /hooks; complete client project/MCP approvals.",
                           "Use the next ordinary coding task to inspect automatic usage reports; no paid probe session."],
            "scope": "Project-local files and index only. Global policies, instruction overrides in subdirectories, "
                     "client trust, loaded tools, and model selection of IBWD are not verified."}
