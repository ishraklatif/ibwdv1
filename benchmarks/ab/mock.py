"""No-network mock execution: synthetic `claude -p` transcripts for every situation the runner must survive."""
from __future__ import annotations

import hashlib
import json

from .execute import ProcessResult, tools_for
from .config import build_prompt

SCENARIOS = ("success", "wrong_answer", "malformed", "no_usage", "nonzero_exit", "infra_failure", "timeout", "budget", "duplicate_events", "no_result")


def expected_answer(spec: dict) -> dict:
    e = spec["expected"]
    if "callers" in e:
        return {"callers": [dict(i) for i in e["callers"]]}
    if "callees" in e:
        return {"callees": [dict(i) for i in e["callees"]]}
    return {"path": [dict(i) for i in e["accepted_paths"][0]], "relation": "CALLS"}


def _tokens(session: dict, base: int, span: int) -> int:
    return base + int(hashlib.sha256(session["session_id"].encode()).hexdigest()[:6], 16) % span


def transcript(session: dict, spec: dict, scenario: str, tokens: dict | None = None) -> str:
    allowed, _ = tools_for(session["condition"])
    tok = tokens or ({"i": 3, "cc": 9000, "cr": _tokens(session, 40000, 20000), "o": 600} if session["condition"] == "baseline" else {"i": 3, "cc": 6000, "cr": _tokens(session, 6000, 3000), "o": 300})
    usage = {"input_tokens": tok["i"], "cache_creation_input_tokens": tok["cc"], "cache_read_input_tokens": tok["cr"], "output_tokens": tok["o"]}
    ans = {"task_id": spec["task_id"], "answer": expected_answer(spec)}
    if scenario == "wrong_answer":
        key = "path" if "path" in ans["answer"] else next(k for k in ans["answer"] if k != "relation")
        ans["answer"][key] = ans["answer"][key][:-1]
    events = [{"type": "system", "subtype": "init", "uuid": "u0", "tools": allowed, "mcp_servers": [{"name": "ibwd", "status": "connected"}] if session["condition"] == "ibwd" else []},
              {"type": "assistant", "uuid": "u1", "message": {"content": [{"type": "tool_use", "name": "Grep", "input": {"pattern": "x", "path": "src"}}]}}]
    result = {"type": "result", "subtype": "success", "uuid": "u2", "is_error": False, "result": json.dumps(ans), "usage": usage, "total_cost_usd": 0.05, "duration_ms": 4000, "num_turns": 3}
    if scenario == "malformed":
        return "{not json\nalso not json\n"
    if scenario == "no_usage":
        result.pop("usage")
    elif scenario == "budget":
        result.update(subtype="error_max_budget_usd", is_error=True, result="budget exceeded", total_cost_usd=1.02)
    elif scenario == "infra_failure":
        return ""
    elif scenario == "no_result":
        return "\n".join(json.dumps(e) for e in events) + "\n"
    elif scenario == "nonzero_exit":
        return "\n".join(json.dumps(e) for e in events) + "\n"
    events.append(result)
    if scenario == "duplicate_events":
        events = [e for e in events for _ in (0, 1)]
    return "\n".join(json.dumps(e) for e in events) + "\n"


class MockExecutor:
    """`scenario_of(session)` returns a scenario name; an optional `hook(session, attempt)` may raise to simulate an interruption."""

    def __init__(self, tasks_by_id: dict, scenario_of=lambda s: "success", hook=None, calls: list | None = None):
        self.tasks, self.scenario_of, self.hook = tasks_by_id, scenario_of, hook
        self.calls = calls if calls is not None else []

    def __call__(self, session: dict, prompt: str) -> ProcessResult:
        spec = self.tasks[session["task_id"]]
        assert prompt == build_prompt(spec)
        assert json.dumps(spec["expected"]) not in prompt                       # the prompt never contains the expected answer
        self.calls.append(session["session_id"])
        if self.hook:
            self.hook(session, len(self.calls))
        sc = self.scenario_of(session)
        out = transcript(session, spec, sc)
        if sc == "timeout":
            return ProcessResult(out, "", None, 600.0, timed_out=True)
        if sc in ("nonzero_exit", "infra_failure"):
            return ProcessResult(out, "boom", 1, 1.0)
        return ProcessResult(out, "", 0, 4.0)
