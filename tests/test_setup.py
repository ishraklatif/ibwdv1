import json
import tomllib

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.setup import BLOCK, START, inspect_setup, plan_setup, server_config, setup_project, skill_path, skill_source


def test_setup_both_preserves_settings_and_instructions_and_is_idempotent(tmp_path):
    (tmp_path / "app.py").write_text("def finish(): return 1\n")
    (tmp_path / "AGENTS.md").write_text("# Team rules\nRun tests.\n")
    (tmp_path / "CLAUDE.md").write_text("# Claude rules\nKeep changes small.\n")
    (tmp_path / ".codex").mkdir()
    original_toml = '# preserve this comment\nmodel = "test"\n[mcp_servers.other]\ncommand = "other"\n'
    (tmp_path / ".codex/config.toml").write_text(original_toml)
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.local.json").write_text(json.dumps({"permissions": {"deny": ["Bash(rm *)"]}}))
    result = setup_project(tmp_path)
    assert result["readiness"]["status"] == "ready"
    assert (tmp_path / "AGENTS.md").read_text().startswith("# Team rules\nRun tests.\n")
    assert (tmp_path / "CLAUDE.md").read_text().startswith("# Claude rules\nKeep changes small.\n")
    assert (tmp_path / ".codex/config.toml").read_text().startswith(original_toml)
    assert tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"]["ibwd"] == server_config(tmp_path)
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["other"] == {"command": "other"}
    assert json.loads((tmp_path / ".claude/settings.local.json").read_text())["permissions"] == {"deny": ["Bash(rm *)"]}
    assert (tmp_path / ".ibwd/setup-backups/AGENTS.md").read_text() == "# Team rules\nRun tests.\n"
    assert plan_setup(tmp_path) == {}
    assert setup_project(tmp_path)["changed_files"] == []
    assert (tmp_path / "AGENTS.md").read_text().count(START) == 1
    assert (tmp_path / skill_path("codex")).read_text() == (tmp_path / skill_path("claude")).read_text() == skill_source()
    assert "description: Locate unfamiliar" in skill_source()
    assert "override conflicting instructions" in skill_source()


def test_override_receives_routing_without_replacing_agents(tmp_path):
    (tmp_path / "AGENTS.md").write_text("original")
    (tmp_path / "AGENTS.override.md").write_text("override")
    result = setup_project(tmp_path, "codex")
    assert BLOCK in (tmp_path / "AGENTS.override.md").read_text()
    assert (tmp_path / "AGENTS.md").read_text() == "original"
    assert result["readiness"]["clients"]["codex"]["routing_file"] == "AGENTS.override.md"
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".mcp.json").exists()


@pytest.mark.parametrize("relative,content", [
    (".mcp.json", "invalid json"),
    (".mcp.json", '{"mcpServers":{"ibwd":{"command":"someone-else"}}}'),
    (".claude/settings.local.json", '{"hooks":{"Stop":{}}}'),
    ("CLAUDE.md", START + "\nmissing end"),
    (".codex/config.toml", "invalid toml"),
    (".agents/skills/ibwd-navigation/SKILL.md", "user-owned skill"),
])
def test_preflight_failure_never_partially_installs(tmp_path, relative, content):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        setup_project(tmp_path)
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_skill_preserves_conflicting_user_direction(tmp_path):
    original = "Do not use IBWD on this task.\n"
    (tmp_path / "AGENTS.md").write_text(original)
    setup_project(tmp_path)
    assert (tmp_path / "AGENTS.md").read_text().startswith(original)
    assert "Respect the user's instructions" in skill_source()


def test_readiness_distinguishes_missing_stale_disabled_and_runtime_unknown(tmp_path):
    missing = inspect_setup(tmp_path)
    assert missing["status"] == "needs_attention"
    assert missing["index"]["status"] == "not_indexed"
    assert missing["clients"]["codex"]["problems"]
    setup_project(tmp_path)
    ready = inspect_setup(tmp_path)
    assert ready["status"] == "ready"
    assert ready["clients"]["codex"]["runtime_connection"] == "unverified"
    assert ready["clients"]["claude"]["agent_adoption"] == "unverified"
    assert not ready["clients"]["claude"]["automatic_report_seen"]
    (tmp_path / "new.py").write_text("def new(): pass\n")
    assert inspect_setup(tmp_path)["index"]["status"] == "stale"
    path = tmp_path / ".claude/settings.local.json"
    config = json.loads(path.read_text())
    config["disableAllHooks"] = True
    config["disabledMcpjsonServers"] = ["ibwd"]
    path.write_text(json.dumps(config))
    problems = inspect_setup(tmp_path)["clients"]["claude"]["problems"]
    assert any("disable all hooks" in p for p in problems)
    assert any("disable the ibwd" in p for p in problems)


def test_dry_run_and_doctor_are_read_only(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--repo", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "AGENTS.md" in json.loads(result.output)["would_change"]
    assert not list(tmp_path.iterdir())
    result = runner.invoke(main, ["doctor", "--setup", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert json.loads(result.output)["status"] == "needs_attention"
    assert not list(tmp_path.iterdir())


def test_setup_refuses_symlinked_configuration_and_index(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".ibwd").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        plan_setup(repo)
    assert not list(outside.iterdir())


def test_existing_correct_codex_config_keeps_extra_options_and_comments(tmp_path):
    (tmp_path / ".codex").mkdir()
    expected = server_config(tmp_path)
    content = '[mcp_servers.ibwd]\n# timeout belongs to the user\nstartup_timeout_sec = 40\n' + "\n".join(
        f"{k} = {json.dumps(v)}" for k, v in expected.items()) + "\n"
    (tmp_path / ".codex/config.toml").write_text(content)
    setup_project(tmp_path, "codex")
    assert (tmp_path / ".codex/config.toml").read_text() == content
