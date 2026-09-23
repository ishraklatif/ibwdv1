"""No model calls: adoption evidence, incremental reports and shared skills."""
import json
import subprocess
import shlex

from ibwd.telemetry import append_event, read_events
from ibwd.usage import analyze_log
from ibwd.usage_stream import incremental_report
from ibwd.usage_hooks import hook_config
from tests.test_usage_hooks import transcript


def test_nested_events_deduplicate_and_ignore_source_strings(tmp_path):
    path, _ = transcript(tmp_path)
    invocation = {"server": "ibwd", "tool": "ibwd_find_symbol"}
    events = [
        {"type": "event_msg", "payload": {"type": "mcp_tool_call_begin", "call_id": "call-1", "invocation": invocation}},
        {"type": "event_msg", "payload": {"type": "mcp_tool_call_end", "call_id": "call-1", "invocation": invocation,
            "result": {"Ok": {"isError": False, "_meta": {"ibwd": {"schema_version": 1, "observation_id": "a" * 32}}}}}},
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "outer", "name": "functions.exec",
            "arguments": 'await tools.mcp__ibwd__ibwd_scan({})'}},
        {"type": "event_msg", "payload": {"type": "mcp_tool_call_end", "call_id": "retry", "invocation": invocation,
            "result": {"Err": "private error"}}},
    ]
    with path.open("a") as stream:
        stream.write("".join(json.dumps(e) + "\n" for e in events))
    report = analyze_log(path, "codex")
    assert report["observed_ibwd_calls"] == 2
    assert report["direct_ibwd_calls"] == 1
    assert report["observations"]["scan_calls"]["value"] == 0
    assert report["observations"]["retrieval_errors"]["value"] == 1
    assert report["observations"]["connection_observed"]["value"] is True
    assert report["server_observation_ids"] == ["a" * 32]
    assert "private error" not in json.dumps(report)


def test_incremental_partial_repeat_replacement_and_corrupt_checkpoint(tmp_path):
    path, _ = transcript(tmp_path)
    checkpoint = tmp_path / "checkpoint.json"
    first, state = incremental_report(path, "codex", checkpoint)
    checkpoint.write_text(json.dumps(state))
    again, state = incremental_report(path, "codex", checkpoint)
    assert again["parser"]["resumed"] and again["parser"]["bytes_read"] == 0
    extra = json.dumps({"type": "response_item", "payload": {"type": "function_call", "call_id": "second",
                      "name": "mcp__ibwd__ibwd_scan"}})
    with path.open("a") as stream:
        stream.write(extra)
    partial, state = incremental_report(path, "codex", checkpoint)
    assert partial["parser"]["pending_line"] and partial["observed_ibwd_calls"] == 1
    checkpoint.write_text(json.dumps(state))
    with path.open("a") as stream:
        stream.write("\n")
    final, state = incremental_report(path, "codex", checkpoint)
    assert final["observed_ibwd_calls"] == 2
    assert final["records"] == analyze_log(path, "codex")["records"]
    assert "SECRET SOURCE" not in json.dumps(state)
    checkpoint.write_text(json.dumps(state))
    path.write_text('{"type":"session_meta","payload":{"id":"replacement"}}\n')
    replaced, state = incremental_report(path, "codex", checkpoint)
    assert not replaced["parser"]["resumed"] and replaced["observed_ibwd_calls"] == 0
    state["parser"]["calls"] = []
    checkpoint.write_text(json.dumps(state))
    recovered, _ = incremental_report(path, "codex", checkpoint)
    assert recovered["records"] == 1


def test_claude_private_message_ids_and_plain_error(tmp_path):
    path, _ = transcript(tmp_path, "claude")
    with path.open("a") as stream:
        stream.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                     "tool_use_id": "call-1", "is_error": True, "content": "SECRET FAILURE"}]}}) + "\n")
    report, state = incremental_report(path, "claude", tmp_path / "missing")
    assert report["observations"]["retrieval_errors"]["value"] == 1
    assert "message-1" not in json.dumps(state)
    assert "SECRET" not in json.dumps(state)


def test_ledger_retention(tmp_path, monkeypatch):
    monkeypatch.setattr("ibwd.telemetry.MAX_EVENTS", 4)
    for n in range(7):
        append_event(tmp_path, {"observation_id": str(n), "phase": "invoked"})
    assert [e["observation_id"] for e in read_events(tmp_path)] == ["3", "4", "5", "6"]


def test_concurrent_hooks_replace_snapshot_and_compare(tmp_path):
    _, event = transcript(tmp_path)
    command = shlex.split(hook_config("codex", tmp_path)["hooks"]["Stop"][0]["hooks"][0]["command"])
    processes = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(3)]
    for process in processes:
        process.stdin.write(json.dumps(event))
        process.stdin.close()
        process.stdin = None
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        assert not stdout
    reports = list((tmp_path / ".ibwd/usage/sessions").glob("*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["observed_ibwd_calls"] == 1
    assert "not matched tasks or measured savings" in (tmp_path / ".ibwd/usage/comparison.md").read_text()
