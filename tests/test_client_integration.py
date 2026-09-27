"""Exercise the actual stdio boundary used by both clients, without an LLM."""
import asyncio
import json
import sqlite3
import tomllib

import anyio
import pytest
from click.testing import CliRunner
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ibwd.cli import main
from ibwd.health import inspect_index
from ibwd.scan import run_scan
from ibwd.telemetry import read_events


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_generated_config_drives_real_mcp_from_unrelated_directory(tmp_path, client):
    repo = tmp_path / 'target repo "quoted"'
    repo.mkdir()
    (repo / "app.py").write_text("def start():\n    return finish()\n\ndef finish():\n    return 1\n")
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    result = CliRunner().invoke(main, ["client-config", "--client", client, "--repo", str(repo)])
    assert result.exit_code == 0, result.output
    config = (tomllib.loads(result.output)["mcp_servers"] if client == "codex"
              else json.loads(result.output)["mcpServers"])["ibwd"]
    assert not (repo / ".ibwd").exists()  # printing config never scans or installs

    def payload(result):
        assert not error(result), result
        data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        if data is None:
            data = json.loads(result.content[0].text)
        return data["result"] if isinstance(data, dict) and "result" in data else data

    def error(result):
        return getattr(result, "is_error", getattr(result, "isError", False))

    async def exercise():
        with anyio.fail_after(30):
            params = StdioServerParameters(**config, cwd=elsewhere)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert "Use IBWD first" in initialized.instructions
                    assert "ibwd_scan" in initialized.instructions
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert names == {"ibwd_scan", "ibwd_find_files", "ibwd_find_symbol", "ibwd_list_symbols",
                                     "ibwd_callers", "ibwd_dependents", "ibwd_trace_path", "ibwd_context", "ibwd_read", "ibwd_impact", "ibwd_compiler_evidence", "ibwd_artifact_save", "ibwd_artifact_read"}
                    missing = await session.call_tool("ibwd_find_symbol", {"name": "finish"})
                    assert payload(missing)[0]['symbol_id'] == 'app.py::finish'
                    assert (repo / ".ibwd/graph.db").exists()
                    assert read_events(repo)[-1]["status"] == "success"
                    assert payload(await session.call_tool("ibwd_scan", {}))["total_files"] == 1
                    found = await session.call_tool("ibwd_find_files", {})
                    assert payload(found)[0]["path"] == "app.py"
                    assert found.meta["ibwd"]["observation_id"] == read_events(repo)[-1]["observation_id"]
                    assert payload(await session.call_tool("ibwd_find_symbol", {"name": "finish"}))[0]["line"] == 4
                    assert len(payload(await session.call_tool("ibwd_list_symbols", {"file": "app.py"}))) == 2
                    assert payload(await session.call_tool("ibwd_callers", {"symbol": "finish"}))[0]["name"] == "start"
                    assert payload(await session.call_tool("ibwd_dependents", {"symbol": "start"}))[0]["name"] == "finish"
                    assert payload(await session.call_tool("ibwd_trace_path", {"source": "start", "target": "finish"}))["hops"] == 1
                    impact_result = await session.call_tool('ibwd_impact', {'targets': ['app.py::finish']})
                    assert payload(impact_result)['items'][0]['symbol_id'] == 'app.py::start'
                    assert len(json.dumps(impact_result.model_dump(mode='json', by_alias=True, exclude_none=True),
                                          ensure_ascii=False, separators=(',', ':')).encode()) <= 16384
                    compiler_result = await session.call_tool('ibwd_compiler_evidence', {'file': 'app.py', 'line': 1, 'column': 1})
                    assert payload(compiler_result)['status'] == 'unknown'
                    packet_result = await session.call_tool('ibwd_context', {'task': 'finish', 'targets': ['app.py::finish'],
                                                                            'semantic': True})
                    packet = payload(packet_result)
                    assert packet['semantic']['status'] == 'fallback'  # optional runtime/index absent
                    assert packet['items'][0]['symbol_id'] == 'app.py::finish'
                    saved = payload(await session.call_tool('ibwd_artifact_save', {
                        'kind': 'summary', 'payload': {'evidence': [
                            {'file': 'app.py', 'hash': packet['files']['app.py'], 'range': [4, 4]}],
                            'dependencies': []}}))
                    artifact_result = await session.call_tool('ibwd_artifact_read', {'artifact_id': saved['artifact_id']})
                    assert payload(artifact_result)['status'] == 'ready'
                    assert len(json.dumps(artifact_result.model_dump(mode='json', by_alias=True, exclude_none=True),
                                          ensure_ascii=False, separators=(',', ':')).encode()) <= 16384
                    assert len(json.dumps(packet_result.model_dump(mode='json', by_alias=True, exclude_none=True),
                                          ensure_ascii=False, separators=(',', ':')).encode()) <= 8000
                    source_result = await session.call_tool('ibwd_read', {
                        'symbol_id_or_path': 'app.py::finish', 'expected_hash': packet['files']['app.py']})
                    assert payload(source_result)['items'][0]['text'] == 'def finish():\n    return 1\n'
                    assert len(json.dumps(source_result.model_dump(mode='json', by_alias=True, exclude_none=True),
                                          ensure_ascii=False, separators=(',', ':')).encode()) <= 4000

    asyncio.run(exercise())
    assert not (elsewhere / ".ibwd").exists()
    assert inspect_index(repo)["status"] == "ready"
    events = read_events(repo)
    assert len(events) == 26  # artifact reads count as retrieval; saves do not.
    assert all(e["session_key"] is None for e in events)
    assert all("arguments" not in e for e in events)
    assert all(e["response_bytes"] > 0 for e in events if e["status"] == "success")


def test_doctor_is_read_only_and_detects_edits_deletions_and_additions(tmp_path):
    runner = CliRunner()
    args = ["doctor", "--repo", str(tmp_path)]
    result = runner.invoke(main, args)
    assert result.exit_code == 1 and json.loads(result.output)["status"] == "not_indexed"
    assert not (tmp_path / ".ibwd").exists()
    (tmp_path / "app.py").write_text("def f():\n    return 1\n")
    (tmp_path / "old.py").write_text("old = 1\n")
    assert runner.invoke(main, ["scan", "--repo", str(tmp_path)]).exit_code == 0
    db = tmp_path / ".ibwd/graph.db"
    before = db.read_bytes()
    assert runner.invoke(main, args).exit_code == 0
    (tmp_path / "app.py").write_text("def f():\n    return 2\n")
    (tmp_path / "old.py").unlink()
    (tmp_path / "new.ts").write_text("export function fresh() {}\n")
    result = runner.invoke(main, args)
    assert result.exit_code == 1
    assert json.loads(result.output)["changes"] == {"added": ["new.ts"], "removed": ["old.py"], "changed": ["app.py"]}
    assert db.read_bytes() == before


def test_doctor_detects_invalid_and_inconsistent_index(tmp_path):
    (tmp_path / "a.py").write_text("def f(): pass\n")
    run_scan(tmp_path)
    manifest = tmp_path / ".ibwd/manifest.json"
    manifest.write_text("{}")
    assert "disagree" in inspect_index(tmp_path)["problems"][0]
    run_scan(tmp_path)
    with sqlite3.connect(tmp_path / ".ibwd/graph.db") as conn:
        conn.execute("PRAGMA user_version = 1")
    assert inspect_index(tmp_path)["status"] == "stale"
    manifest.write_text("[]")
    assert inspect_index(tmp_path)["status"] == "invalid"
    manifest.write_text("{}")
    (tmp_path / ".ibwd/graph.db").write_bytes(b"not sqlite")
    assert inspect_index(tmp_path)["status"] == "invalid"
