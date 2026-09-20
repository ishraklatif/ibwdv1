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


@pytest.mark.anyio
async def test_ibwd_callers_dependents_and_trace_path_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "chain.py").write_text(
        "def a():\n    return b()\n\n"
        "def b():\n    return c()\n\n"
        "def c():\n    return d()\n\n"
        "def d():\n    return 1\n\n"
        "def solo():\n    return 0\n"
    )
    monkeypatch.chdir(tmp_path)
    await mcp.call_tool("ibwd_scan", {})

    callers = _tool_result_json(await mcp.call_tool("ibwd_callers", {"symbol": "d"}))
    assert [(r["name"], r["distance"], r["confidence"]) for r in callers] == [("c", 1, 0.9)]
    assert callers[0]["file"] == "chain.py" and callers[0]["line"] == 7 and callers[0]["relation"] == "CALLS"

    deep = _tool_result_json(await mcp.call_tool("ibwd_callers", {"symbol": "d", "depth": 3}))
    assert [r["name"] for r in deep] == ["c", "b", "a"]

    dependents = _tool_result_json(await mcp.call_tool("ibwd_dependents", {"symbol": "a", "depth": 2}))
    assert [(r["name"], r["distance"]) for r in dependents] == [("b", 1), ("c", 2)]

    unknown = _tool_result_json(await mcp.call_tool("ibwd_callers", {"symbol": "nope"}))
    assert len(unknown) == 1 and unknown[0]["empty_result"] is True and "No symbol or file matching" in unknown[0]["statement"]

    traced = _tool_result_json(await mcp.call_tool("ibwd_trace_path", {"source": "a", "target": "d"}))
    assert [hop["name"] for hop in traced["path"]] == ["a", "b", "c", "d"]
    assert traced["hops"] == 3
    assert traced["path"][0]["edge_type"] is None
    assert [(h["edge_type"], h["confidence"]) for h in traced["path"][1:]] == [("CALLS", 0.9)] * 3

    no_path = _tool_result_json(await mcp.call_tool("ibwd_trace_path", {"source": "a", "target": "solo"}))
    assert no_path["path"] is None and no_path["reason"].startswith("no path found")
    assert [(r["name"], r["file"]) for r in no_path["source_resolved"]] == [("a", "chain.py")]
    assert [(r["name"], r["file"]) for r in no_path["target_resolved"]] == [("solo", "chain.py")]

    missing = _tool_result_json(await mcp.call_tool("ibwd_trace_path", {"source": "a", "target": "nope"}))
    assert missing["path"] is None and "target not found" in missing["reason"]

    bad = _tool_result_json(await mcp.call_tool("ibwd_trace_path", {"source": "a", "target": "d", "edge_types": ["BOGUS"]}))
    assert bad["path"] is None and "unsupported edge_types" in bad["reason"]


@pytest.mark.anyio
async def test_ibwd_callers_labels_ambiguous_targets(symbol_repo: Path, monkeypatch: pytest.MonkeyPatch):
    (symbol_repo / "src" / "use.py").write_text(
        "from other import greet\n\ndef go():\n    return greet()\n"
    )
    monkeypatch.chdir(symbol_repo)
    await mcp.call_tool("ibwd_scan", {})

    # `greet` is defined twice (User.greet and other.greet): results say which one they reach
    callers = _tool_result_json(await mcp.call_tool("ibwd_callers", {"symbol": "greet"}))
    assert {(r["name"], r["of"]) for r in callers} == {("go", "src/other.py:1")}

    narrowed = _tool_result_json(await mcp.call_tool("ibwd_callers", {"symbol": "greet", "file": "src/other.py"}))
    assert [r["name"] for r in narrowed] == ["go"] and "of" not in narrowed[0]


@pytest.mark.anyio
async def test_empty_results_state_their_scope_and_never_claim_deletion_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "m.py").write_text("def caller():\n    return 1\n\ndef unused():\n    return 2\n")
    monkeypatch.chdir(tmp_path)
    await mcp.call_tool("ibwd_scan", {})

    empty = _tool_result_json(await mcp.call_tool("ibwd_callers", {"symbol": "unused"}))
    assert len(empty) == 1 and empty[0]["empty_result"] is True
    text = empty[0]["statement"]
    assert "No resolved incoming CALLS or IMPORTS or INHERITS or REFERENCES edges were found within the indexed production scope" in text
    assert "Other uses may exist" in text and "not evidence that the symbol is unused or safe to delete" in text
    assert empty[0]["supported_claim"] == "no matching edges in this graph" and empty[0]["unsupported_claim"] == "no possible uses in the program"
    assert empty[0]["candidate_hints_included"] is False and empty[0]["matched_targets"][0]["name"] == "unused"
    for forbidden in ("no static use", "safe to delete.", "dead code", "is unused."):
        assert forbidden not in text.lower().replace("not evidence that the symbol is unused or safe to delete", "")

    outgoing = _tool_result_json(await mcp.call_tool("ibwd_dependents", {"symbol": "unused", "include_candidates": True}))
    assert outgoing[0]["empty_result"] is True and "outgoing" in outgoing[0]["statement"] and "resolved and candidate" in outgoing[0]["statement"]

    no_path = _tool_result_json(await mcp.call_tool("ibwd_trace_path", {"source": "caller", "target": "unused"}))
    assert no_path["path"] is None and "not proof that no runtime path exists" in no_path["reason"] and no_path["scope"]
