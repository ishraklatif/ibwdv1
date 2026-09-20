"""Parse a `claude -p --output-format stream-json --verbose` transcript into a measurement. No I/O, no model calls.

Statuses: ok | malformed | no_result | usage_missing | budget_exhausted | error_result | conflicting_results.
Usage is taken ONLY from the final `result` event (all four fields); it is never summed from per-request usage, never defaulted to zero,
and a missing field makes the whole measurement unavailable (tokens = None).
"""
from __future__ import annotations

import json

USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def parse_transcript(stdout: str) -> dict:
    events, malformed, seen = [], 0, set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(d, dict):
            malformed += 1
            continue
        uid = d.get("uuid")
        if uid is not None:                                   # a replayed/duplicated event is counted once
            if uid in seen:
                continue
            seen.add(uid)
        events.append(d)

    tools, mcp, tool_calls, files = None, None, [], []
    results = [e for e in events if e.get("type") == "result"]
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init":
            tools, mcp = e.get("tools"), e.get("mcp_servers")
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append(block.get("name"))
                    inp = block.get("input") or {}
                    for k in ("file_path", "path", "pattern"):
                        if isinstance(inp.get(k), str):
                            files.append({"tool": block.get("name"), k: inp[k]})
    out = {"status": None, "tokens": None, "usage": None, "cost_usd": None, "result_text": None, "tool_inventory": tools, "mcp_servers": mcp,
           "tool_calls": tool_calls, "file_accesses": files, "malformed_lines": malformed, "events": len(events), "duration_ms": None, "num_turns": None,
           "result_subtype": None}
    if not results:
        out["status"] = "malformed" if malformed and not events else "no_result"
        return out
    distinct = {json.dumps({k: r.get(k) for k in ("subtype", "is_error", "result", "usage", "total_cost_usd")}, sort_keys=True) for r in results}
    if len(distinct) > 1:
        out["status"] = "conflicting_results"
        return out
    r = results[-1]
    usage = r.get("usage")
    out.update(result_text=r.get("result"), cost_usd=r.get("total_cost_usd"), duration_ms=r.get("duration_ms"), num_turns=r.get("num_turns"),
               result_subtype=r.get("subtype"))
    if isinstance(usage, dict) and all(isinstance(usage.get(f), int) and not isinstance(usage.get(f), bool) for f in USAGE_FIELDS):
        out["usage"] = {f: usage[f] for f in USAGE_FIELDS}
        out["tokens"] = sum(usage[f] for f in USAGE_FIELDS)          # T = input + cache_creation + cache_read + output, final result.usage only
    subtype = str(r.get("subtype") or "")
    if "budget" in subtype:
        out["status"] = "budget_exhausted"
    elif r.get("is_error") or (subtype and subtype != "success"):
        out["status"] = "error_result"
    elif out["tokens"] is None:
        out["status"] = "usage_missing"
    else:
        out["status"] = "ok"
    return out
