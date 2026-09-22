import json
from pathlib import Path
import shlex
import subprocess

from click.testing import CliRunner
import pytest

from ibwd.cli import main
from ibwd.usage import analyze_log
from ibwd.usage_hooks import capture_session, hook_config, install_hooks


def transcript(repo, client="codex"):
    if client == "codex":
        events = [
            {"type": "session_meta", "payload": {"id": "private-session", "cli_version": "test"}},
            {"type": "turn_context", "payload": {"model": "test", "effort": "medium"}},
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "call-1",
                "name": "mcp__ibwd__ibwd_find_symbol", "arguments": "SECRET SOURCE"}},
        ]
    else:
        events = [{"type": "assistant", "sessionId": "private-session", "message": {
            "id": "message-1", "model": "test", "content": [
                {"type": "tool_use", "id": "call-1", "name": "mcp__ibwd__ibwd_find_symbol", "input": "SECRET SOURCE"}
            ]}}]
    path = repo / "transcript.jsonl"
    path.write_text("\n".join(map(json.dumps, events)) + "\n")
    event = {"hook_event_name": "Stop", "session_id": "private-session",
             "cwd": str(repo), "transcript_path": str(path)}
    return path, event


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_generated_command_saves_report_without_stdout_or_content(tmp_path, client):
    repo = tmp_path / "project with spaces and 'quotes'"
    repo.mkdir()
    _, event = transcript(repo, client)
    command = hook_config(client, repo)["hooks"]["Stop"][0]["hooks"][0]["command"]
    result = subprocess.run(shlex.split(command), input=json.dumps(event), text=True,
                            capture_output=True, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    folder = repo / ".ibwd/usage/sessions"
    reports = list(folder.glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["direct_ibwd_calls"] == 1
    assert report["condition"] == report["outcome"] == "unknown"
    assert report["provisional"] and report["usage"] is None
    assert "SECRET SOURCE" not in reports[0].read_text()
    assert "private-session" not in reports[0].read_text()
    assert "Direct IBWD calls: 1" in reports[0].with_suffix(".md").read_text()
    assert (folder.parent / f"latest-{client}.md").read_text() == reports[0].with_suffix(".md").read_text()


def test_repeated_and_resumed_capture_replaces_same_session(tmp_path):
    path, event = transcript(tmp_path)
    first = capture_session(event, "codex", tmp_path)
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "response_item", "payload": {
            "type": "function_call", "call_id": "call-2", "name": "mcp__ibwd__ibwd_scan"}}) + "\n")
    event["hook_event_name"] = "SessionEnd"
    assert capture_session(event, "codex", tmp_path) == first
    report = json.loads(first.read_text())
    assert report["direct_ibwd_calls"] == 2 and report["capture_event"] == "SessionEnd"
    assert len(list(first.parent.glob("*.json"))) == 1


@pytest.mark.parametrize("field,value", [("session_id", "wrong-session"), ("transcript_path", None),
    ("transcript_path", "relative.jsonl"), ("hook_event_name", "PreToolUse"), ("cwd", "/")])
def test_invalid_event_does_not_write_reports(tmp_path, field, value):
    _, event = transcript(tmp_path)
    event[field] = value
    with pytest.raises(ValueError):
        capture_session(event, "codex", tmp_path)
    assert not (tmp_path / ".ibwd").exists()


def test_child_event_is_not_attributed_to_parent(tmp_path):
    _, event = transcript(tmp_path)
    event["agent_id"] = "child"
    assert capture_session(event, "codex", tmp_path) is None
    assert not (tmp_path / ".ibwd").exists()


@pytest.mark.parametrize("client,relative", [("codex", ".codex/hooks.json"),
                                            ("claude", ".claude/settings.local.json")])
def test_setup_preserves_settings_backs_up_and_is_idempotent(tmp_path, client, relative):
    config = tmp_path / relative
    config.parent.mkdir()
    original = {"permissions": {"allow": ["Read"]}, "hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "existing-command"}]}]}}
    config.write_text(json.dumps(original))
    assert install_hooks(client, tmp_path) == config
    installed = config.read_text()
    install_hooks(client, tmp_path)
    assert config.read_text() == installed
    contents = json.loads(installed)
    assert contents["permissions"] == original["permissions"]
    assert len(contents["hooks"]["Stop"]) == 2
    assert json.loads(config.with_name(config.name + ".ibwd-backup").read_text()) == original


@pytest.mark.parametrize("text", ["not json", "[]", '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
def test_setup_refuses_invalid_existing_config(tmp_path, text):
    config = tmp_path / ".codex/hooks.json"
    config.parent.mkdir()
    config.write_text(text)
    result = CliRunner().invoke(main, ["usage-setup", "--client", "codex", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert config.read_text() == text


def test_instruction_evidence_excludes_conversation_mentions(tmp_path):
    path, _ = transcript(tmp_path)
    mention = {"type": "response_item", "payload": {"type": "message", "role": "user",
               "content": [{"type": "input_text", "text": "Why wasn't IBWD used?"}]}}
    with path.open("a") as stream:
        stream.write(json.dumps(mention) + "\n")
    assert not analyze_log(path, "codex")["instruction_evidence"]["ibwd_mentioned"]
    mention["payload"]["content"][0]["text"] = "# AGENTS.md instructions for /project\nUse IBWD first."
    with path.open("a") as stream:
        stream.write(json.dumps(mention) + "\n")
    assert analyze_log(path, "codex")["instruction_evidence"]["ibwd_mentioned"]


def test_hook_error_is_advisory_and_never_requests_continuation(tmp_path):
    result = CliRunner().invoke(main, ["usage-hook", "--client", "codex", "--repo", str(tmp_path)], input="{}")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "report not saved" in result.stderr
