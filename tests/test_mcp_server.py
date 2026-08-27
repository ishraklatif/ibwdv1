from __future__ import annotations

import json
from pathlib import Path

import pytest

from ibwd.mcp.server import mcp


@pytest.mark.anyio
async def test_ibwd_scan_and_find_files_tools(git_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(git_repo)

    scan_result = await mcp.call_tool("ibwd_scan", {})
    scan_payload = _tool_result_json(scan_result)
    assert scan_payload["added"] > 0

    find_result = await mcp.call_tool("ibwd_find_files", {"kind": "source"})
    files = _tool_result_json(find_result)
    paths = {f["path"] for f in files}
    assert paths == {"src/app.py", "src/utils.py"}


@pytest.mark.anyio
async def test_ibwd_find_symbol_and_list_symbols_tools(symbol_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(symbol_repo)

    await mcp.call_tool("ibwd_scan", {})

    found = _tool_result_json(await mcp.call_tool("ibwd_find_symbol", {"name": "greet"}))
    matches = {(item["kind"], item["file"]) for item in found}
    assert matches == {("Method", "src/models.py"), ("Function", "src/other.py")}

    listed = _tool_result_json(await mcp.call_tool("ibwd_list_symbols", {"file": "src/models.py"}))
    assert [item["name"] for item in listed] == ["User", "__init__", "greet", "create_user"]


def _tool_result_json(result):
    # FastMCP/MCPServer tool results carry a structured_content field alongside
    # the text content blocks; fall back to parsing the first text block.
    structured = getattr(result, "structured_content", None) or (
        result[1] if isinstance(result, tuple) and len(result) > 1 else None
    )
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if isinstance(structured, (dict, list)):
        return structured

    content = result.content if hasattr(result, "content") else result[0]
    return json.loads(content[0].text)
