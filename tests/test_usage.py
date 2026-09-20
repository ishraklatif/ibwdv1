import json

import pytest
from click.testing import CliRunner

from ibwd.cli import main
from ibwd.usage import analyze_log, summarize_reports


def write_log(tmp_path, events):
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def codex_usage(n):
    return {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
        "input_tokens": n, "cached_input_tokens": n // 2, "output_tokens": 10,
        "reasoning_output_tokens": 5, "total_tokens": n + 10}}}}


def codex_meta():
    return [{"type": "session_meta", "payload": {"id": "private-session-id", "cli_version": "test"}},
            {"type": "turn_context", "payload": {"model": "test-model", "effort": "medium"}}]


def test_codex_cumulative_not_summed_and_no_content_export(tmp_path):
    call = {"type": "response_item", "payload": {"type": "function_call", "call_id": "one",
            "name": "mcp__ibwd__ibwd_callers", "arguments": "SECRET"}}
    log = write_log(tmp_path, codex_meta() + [codex_usage(100), call, call, codex_usage(200), codex_usage(200)])
    r = analyze_log(log, "codex", "enabled", "structural", "passed")
    assert r["usage"]["total_tokens"] == 210
    assert r["direct_ibwd_calls"] == 1 and r["comparable"]
    assert "SECRET" not in json.dumps(r) and "private-session-id" not in json.dumps(r)


def claude_message(mid, output=10):
    return {"type": "assistant", "sessionId": "one", "version": "test", "effort": "medium",
            "message": {"id": mid, "model": "claude-test", "usage": {
                "input_tokens": 100, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30,
                "output_tokens": output}, "content": [{"type": "tool_use", "id": mid + "-tool", "name": "Read", "input": "SECRET"}]}}


def test_claude_deduplicates_message_blocks_and_counts_disjoint_cache(tmp_path):
    log = write_log(tmp_path, [claude_message("a", 5), claude_message("a"), claude_message("a"), claude_message("b")])
    r = analyze_log(log, "claude", "disabled", "implementation", "passed")
    assert r["usage"]["total_tokens"] == 320 and r["tool_calls"] == {"Read": 2}
    assert r["comparable"] and "SECRET" not in json.dumps(r)


def test_missing_usage_never_becomes_zero(tmp_path):
    log = write_log(tmp_path, codex_meta())
    r = analyze_log(log, "codex")
    assert r["usage"] is None and not r["comparable"]
    message = claude_message("a")
    del message["message"]["usage"]
    log = write_log(tmp_path, [message, claude_message("b")])
    r = analyze_log(log, "claude", "enabled", "mixed", "passed")
    assert r["usage"]["total_tokens"] == 160 and not r["comparable"]


def test_reset_malformed_and_missing_effort_block_comparison(tmp_path):
    log = write_log(tmp_path, codex_meta() + [codex_usage(200), codex_usage(100)])
    with log.open("a") as f:
        f.write('{"unfinished":')
    r = analyze_log(log, "codex", "enabled", "mixed", "passed")
    assert not r["comparable"] and len(r["warnings"]) == 2


def test_summary_keeps_clients_separate_and_rejects_resumed_duplicates(tmp_path):
    reports = []
    for client, events in [("codex", codex_meta() + [codex_usage(100)]), ("claude", [claude_message("a")])]:
        report = analyze_log(write_log(tmp_path, events), client, "enabled", "structural", "passed")
        path = tmp_path / f"{client}.json"
        path.write_text(json.dumps(report))
        reports.append(path)
    s = summarize_reports(tuple(reports))
    assert len(s["groups"]) == 2 and s["excluded_reports"] == 0
    with pytest.raises(ValueError, match="same session"):
        summarize_reports((reports[0], reports[0]))


def test_cli_outputs_report_without_running_client(tmp_path):
    log = write_log(tmp_path, codex_meta() + [codex_usage(100)])
    result = CliRunner().invoke(main, ["usage-report", str(log), "--client", "codex"])
    assert result.exit_code == 0 and json.loads(result.output)["condition"] == "unknown"


def test_metadata_and_condition_must_be_explicit(tmp_path):
    call = {"type": "response_item", "payload": {"type": "function_call", "name": "mcp__ibwd__ibwd_scan", "call_id": "1"}}
    r = analyze_log(write_log(tmp_path, codex_meta() + [codex_usage(100), call]), "codex", "disabled", "structural", "passed")
    assert not r["comparable"]


def test_inconsistent_counters_and_partial_reports_are_excluded(tmp_path):
    event = codex_usage(100)
    event["payload"]["info"]["total_token_usage"]["total_tokens"] = 999
    r = analyze_log(write_log(tmp_path, codex_meta() + [event]), "codex", "enabled", "structural", "passed")
    assert not r["comparable"]
    report = tmp_path / "report.json"
    report.write_text(json.dumps(r))
    summary = summarize_reports((report,))
    assert summary["excluded_reports"] == 1 and summary["groups"] == []
    r = analyze_log(write_log(tmp_path, [codex_meta()[0], codex_usage(100)]), "codex", "enabled", "structural", "passed")
    assert not r["comparable"]
