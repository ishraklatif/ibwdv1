"""Offline summaries of existing client transcripts; never invokes a client or model.

Log formats are version-dependent. Values describe recorded usage, not billing or
subscription allowance. Raw prompts, tool arguments and outputs are never exported.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from ibwd.telemetry import key, tool_name
from ibwd.usage_observations import objects, receipt, response_facts, command_kind, exit_status

CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
CLAUDE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _counts(value, fields):
    if not isinstance(value, dict):
        return None
    if any(type(value.get(k)) is not int or value[k] < 0 for k in fields):
        return None
    return {k: value[k] for k in fields}


def analyze_log(path: Path, client: str, condition="unknown", task_kind="unknown", outcome="unknown", *, state=None, lines=None) -> dict:
    """Fold supported structured events. Optional state/lines enable append-only parsing.

    State contains counters and pseudonymous IDs, never text, arguments or results.
    Nested execution source strings are deliberately never parsed or executed.
    """
    if client not in ("codex", "claude"):
        raise ValueError("Unknown client.")
    state = state if state is not None else {}
    warnings = set(state.get("warnings", []))
    models, efforts, versions, session_ids = (set(state.get(k, [])) for k in ("models", "efforts", "versions", "session_ids"))
    tools = Counter(state.get("tools", {}))
    calls = state.setdefault("calls", {})
    latest = state.get("latest")
    messages = state.setdefault("messages", {})
    malformed, records, snapshots = (state.get(k, 0) for k in ("malformed", "records", "snapshots"))
    started, ended = state.get("started"), state.get("ended")
    instruction_records = state.get("instruction_records", 0)
    ibwd_guidance = state.get("ibwd_guidance", False)
    child_seen = state.get("child_seen", False)
    fallback_seen = state.get("fallback_seen", False)
    activity = state.setdefault('activity', {'task_kinds': [], 'turn_active': None})
    operations = state.setdefault('operations', {})

    def activity_call(name, ident, arguments=None):
        if ident and key(ident) not in operations:
            kind = command_kind(name, arguments)
            operations[key(ident)] = dict(kind=kind, completed=False, exit_code=None)
            if kind == 'edit':
                activity['edits_after_check'] = True
                activity['task_kinds'] = sorted(set(activity['task_kinds']) | {'implementation'})

    def request_activity(text):
        import re
        if not isinstance(text, str) or text.startswith(('# AGENTS.md', '<environment_context>')):
            return
        kinds = set(activity['task_kinds'])
        for kind, pattern in [('implementation', r'\b(implement|build|add|create)\b'),
                              ('debugging', r'\b(fix|debug|broken|bug|error)\b'),
                              ('structural', r'\b(explain|find|read|documentation|documentations)\b')]:
            if re.search(pattern, text, re.I):
                kinds.add(kind)
        activity['task_kinds'] = sorted(kinds)

    def instruction(text):
        nonlocal instruction_records, ibwd_guidance
        if isinstance(text, dict):
            text = text.get("text")
        if isinstance(text, str) and text.strip():
            instruction_records += 1
            ibwd_guidance |= "ibwd" in text.lower()

    def call(name, ident, source="direct"):
        if not isinstance(name, str):
            return
        if not ident:
            warnings.add("Tool calls without IDs cannot be counted reliably.")
            return
        ident = key(ident)
        if ident not in calls:
            calls[ident] = {"name": name, "sources": [], "status": "unknown", "observation_id": None}
            tools[name] += 1
        elif (tool_name(calls[ident]["name"]) or calls[ident]["name"]) != (tool_name(name) or name):
            warnings.add("Conflicting tool identities share a call ID; attribution is incomplete.")
            return
        if source not in calls[ident]["sources"]:
            calls[ident]["sources"].append(source)

    def complete(ident, result, status=None):
        operation = operations.get(key(ident))
        code = exit_status(result)
        if operation is not None:
            operation['completed'] = True
            operation['exit_code'] = code
            if operation['kind'] == 'check' and code is not None:
                activity['edits_after_check'] = False
        item = calls.get(key(ident))
        if item and status:
            item["status"] = status
        direct = item if item and tool_name(item['name']) else None
        if direct and isinstance(result, (dict, list)):
            direct['status'] = status or 'success'
        for number, value in enumerate(objects(result)):
            observation = receipt(value)
            children = list(objects(value.get('content', [])))
            observation = observation or next((receipt(v) for v in children if receipt(v)), None)
            packet = next((v for v in [value, *children] if v.get('schema_version') == 2
                           and 'retrieval_version' in v and isinstance(v.get('items'), list)), None)
            target = direct
            if target is None:
                name = tool_name(observation.get('tool')) if observation else None
                # Legacy MCP envelopes carry identifiable IBWD context packets, but no server identity.
                if not name and 'content' in value and packet:
                    name = 'ibwd_context'
                if not name:
                    continue
                nested_id = f"{ident}:response:{number}"
                call(name, nested_id, 'response_receipt' if observation else 'response_packet')
                target = calls[key(nested_id)]
            target['status'] = status or ('error' if value.get('isError', value.get('is_error')) else 'success')
            if observation:
                target['observation_id'] = observation['observation_id']
                if observation.get('status') in {'success', 'error'}:
                    target['status'] = observation['status']
            facts = response_facts(value)
            if facts is not None:
                target['response'] = facts

    def session(ident):
        session_ids.add(hashlib.sha256((client + ':' + str(ident)).encode()).hexdigest()[:20])

    def read_lines():
        with path.open(encoding="utf-8") as stream:
            yield from stream

    if lines is None:
        lines = read_lines()
    for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError()
            except ValueError:
                malformed += 1
                continue
            records += 1
            # Child records must not inflate parent tokens or tool counts.
            if event.get("agent_id") or event.get("agentId") or event.get("isSidechain"):
                child_seen = True
                continue
            # Explicit adapter event, not a prose heuristic or native-client assumption.
            if event.get("type") == "ibwd_observation" and event.get("schema_version") == 1:
                if event.get("kind") == "fallback" and event.get("reason") in ("unavailable", "error", "unsupported"):
                    fallback_seen = True
            stamp = event.get("timestamp")
            if isinstance(stamp, str):
                started = started or stamp
                ended = stamp
            if client == "codex":
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta":
                    instruction(payload.get("base_instructions"))
                    sid = payload.get("id") or payload.get("session_id")
                    if sid:
                        session(sid)
                    if payload.get("cli_version"):
                        versions.add(str(payload["cli_version"]))
                if event.get("type") == "turn_context":
                    for field in ("developer_instructions", "user_instructions"):
                        instruction(payload.get(field))
                    if payload.get("model"):
                        models.add(str(payload["model"]))
                        activity['current_model'] = str(payload['model'])
                    if payload.get("effort"):
                        efforts.add(str(payload["effort"]))
                        activity['current_effort'] = str(payload['effort'])
                if event.get('type') == 'event_msg' and payload.get('type') in {'task_started', 'task_complete', 'turn_aborted'}:
                    activity['turn_active'] = payload['type'] == 'task_started'
                if event.get("type") == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue  # rate-limit-only events carry no usage
                    usage = _counts(info.get("total_token_usage"), CODEX_FIELDS)
                    if usage is None:
                        warnings.add("Incomplete Codex cumulative usage snapshot.")
                        continue
                    if (usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
                            or usage["cached_input_tokens"] > usage["input_tokens"]
                            or usage["reasoning_output_tokens"] > usage["output_tokens"]):
                        warnings.add("Codex token components do not reconcile under the supported accounting convention.")
                    snapshots += 1
                    if latest and any(usage[k] < latest[k] for k in CODEX_FIELDS):
                        warnings.add("Codex counters decreased; possible reset or mixed history.")
                    latest = usage
                if event.get("type") == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                    call(payload.get("name"), payload.get("call_id"))
                    activity_call(payload.get('name'), payload.get('call_id'), payload.get('arguments', payload.get('input')))
                    if payload.get("name") in ("spawn_agent", "functions.spawn_agent", "collaboration.spawn_agent"):
                        child_seen = True
                if event.get("type") == "response_item" and payload.get("type") in ("function_call_output", "custom_tool_call_output"):
                    complete(payload.get("call_id"), payload.get("output"))
                if event.get("type") == "event_msg" and payload.get("type") in ("mcp_tool_call_begin", "mcp_tool_call_end"):
                    invocation = payload.get("invocation", {})
                    if isinstance(invocation, dict) and invocation.get("server") == "ibwd" and tool_name(invocation.get("tool")):
                        call("mcp__ibwd__" + tool_name(invocation["tool"]), payload.get("call_id"), "structured_mcp")
                        result = payload.get("result")
                        if payload["type"] == "mcp_tool_call_end" and isinstance(result, dict):
                            if "Ok" in result:
                                complete(payload.get("call_id"), result["Ok"])
                            elif "Err" in result:
                                complete(payload.get("call_id"), {}, "transport_error")
                if event.get("type") == "response_item" and payload.get("type") == "message":
                    for block in payload.get("content", []) if isinstance(payload.get("content"), list) else []:
                        if not isinstance(block, dict):
                            continue
                        text = block.get("text", "")
                        if isinstance(text, str) and (payload.get("role") in ("system", "developer") or
                                (payload.get("role") == "user" and text.startswith("# AGENTS.md instructions for "))):
                            instruction(text)
                        elif payload.get('role') == 'user':
                            request_activity(text)
            else:
                if event.get("sessionId"):
                    session(event["sessionId"])
                if event.get("version"):
                    versions.add(str(event["version"]))
                if event.get("effort"):
                    efforts.add(str(event["effort"]))
                message = event.get("message")
                if event.get("type") == "user" and isinstance(message, dict):
                    if isinstance(message.get('content'), str):
                        request_activity(message['content'])
                        activity['turn_active'] = True
                    for block in message.get("content", []) if isinstance(message.get("content"), list) else []:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            complete(block.get("tool_use_id"), block.get("content", []), "error" if block.get("is_error") else "success")
                if event.get("type") != "assistant" or not isinstance(message, dict):
                    continue
                model = message.get("model")
                if model == "<synthetic>":
                    continue
                if model:
                    models.add(str(model))
                    activity['current_model'] = str(model)
                if message.get('stop_reason') == 'end_turn':
                    activity['turn_active'] = False
                mid = message.get("id")
                if not mid:
                    warnings.add("Assistant message without an ID; usage cannot be deduplicated.")
                    continue
                mid = key(mid)
                usage = _counts(message.get("usage"), CLAUDE_FIELDS)
                # Several transcript rows can contain blocks from the same API message.
                # Keep the largest observed counters per message, never sum the snapshots.
                previous = messages.get(mid)
                if usage is not None:
                    messages[mid] = {k: max(usage[k], (previous or {}).get(k, 0)) for k in CLAUDE_FIELDS}
                    snapshots += 1
                elif mid not in messages:
                    messages[mid] = None
                content = message.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            call(block.get("name"), block.get("id"))
                            activity_call(block.get('name'), block.get('id'), block.get('input'))
                            if block.get("name") in ("Agent", "Task"):
                                child_seen = True

    # Save only cumulative parser facts. Derived warnings below are reevaluated each time.
    state.update(warnings=sorted(warnings), models=sorted(models), efforts=sorted(efforts), versions=sorted(versions),
                 session_ids=sorted(session_ids), tools=dict(tools), latest=latest, malformed=malformed,
                 records=records, snapshots=snapshots, started=started, ended=ended,
                 instruction_records=instruction_records, ibwd_guidance=ibwd_guidance,
                 child_seen=child_seen, fallback_seen=fallback_seen)

    if malformed:
        warnings.add(f"{malformed} malformed JSONL lines; the log may be incomplete or still being written.")
    if len(session_ids) != 1:
        warnings.add("Expected exactly one recorded session identity.")
    if client == "claude":
        if any(v is None for v in messages.values()):
            warnings.add("Some assistant messages have no complete usage record.")
        valid = [v for v in messages.values() if v is not None]
        latest = {k: sum(v[k] for v in valid) for k in CLAUDE_FIELDS} if valid else None
        if latest:
            latest["total_tokens"] = sum(latest.values())
        method = "sum of deduplicated assistant-message counters; transcript estimate, not final billed usage"
    else:
        method = "last cumulative token_count snapshot; cache/reasoning subcounts are not added again"
    if latest is None:
        warnings.add("No supported usage records found; missing usage is not zero.")
    if len(models) != 1:
        warnings.add("Missing or mixed models; do not compare as one model.")
    if len(efforts) > 1:
        warnings.add("Mixed reasoning effort settings.")
    if not efforts:
        warnings.add("Effort not recorded; confirm client settings before comparison.")
    if child_seen:
        warnings.add("Child-agent activity observed; child usage is not aggregated and parent accounting may be incomplete.")
    ibwd = {ident: item for ident, item in calls.items() if tool_name(item["name"])}
    # Shared server observation identity is stronger than different client wrapper IDs.
    unique = {}
    for ident, item in ibwd.items():
        identity_key = item["observation_id"] or ident
        if identity_key not in unique:
            unique[identity_key] = dict(item, sources=list(item["sources"]))
        else:
            previous = unique[identity_key]
            previous["sources"] = sorted(set(previous["sources"] + item["sources"]))
            if previous["status"] == "unknown":
                previous["status"] = item["status"]
    if condition == "disabled" and ibwd:
        warnings.add("Declared disabled but direct IBWD calls were observed.")
    # Stable pseudonym supports replacing snapshots of a resumed session without exporting its ID.
    identity = next(iter(session_ids)) if len(session_ids) == 1 else key(path.resolve())[:20]
    retrieval = [item for item in unique.values() if tool_name(item["name"]) != "ibwd_scan"]
    responses = [item['response'] for item in retrieval if 'response' in item]
    work = list(operations.values())
    activity_report = dict(activity, operations={
        'completed': sum(o['completed'] for o in work),
        'failed': sum(o['exit_code'] is not None and o['exit_code'] != 0 for o in work),
        'checks_passed': sum(o['kind'] == 'check' and o['exit_code'] == 0 for o in work),
        'checks_failed': sum(o['kind'] == 'check' and o['exit_code'] is not None and o['exit_code'] != 0 for o in work),
        'checks_pending': sum(o['kind'] == 'check' and o['exit_code'] is None for o in work),
    })
    observations = {
        "configured": {"value": "unknown", "source": "No project configuration inspected."},
        "connection_observed": {"value": True if any(i["status"] in ("success", "error") for i in unique.values()) else "unknown",
                                "source": "Structured IBWD tool responses; an invocation alone does not prove connection."},
        "scan_calls": {"value": sum(tool_name(i["name"]) == "ibwd_scan" for i in unique.values()), "source": "Observed structured invocations only; lower bound."},
        "retrieval_calls": {"value": len(retrieval), "source": "Observed structured invocations only; lower bound."},
        "retrieval_errors": {"value": sum(i["status"] in ("error", "transport_error") for i in retrieval) if retrieval and all(i["status"] != "unknown" for i in retrieval) else "unknown",
                             "source": "Observed retrieval completions; missing responses are unknown."},
        "fallback_observed": {"value": True if fallback_seen else "unknown", "source": "Version-1 explicit ibwd_observation fallback events only."},
        "attribution_unknown": {"value": True if any(n in tools for n in ("exec", "functions.exec", "Bash", "exec_command")) or child_seen else "unknown",
                                "source": "Opaque orchestration/child records cannot establish complete attribution."},
        "usage_incomplete": {"value": bool(latest is None or malformed or child_seen or any(
            word in warning.lower() for warning in warnings for word in
            ('usage', 'counters', 'reconcile', 'session identity', 'message without'))),
                             "source": "Completeness of captured token counters; independent of mixed models and final billing."},
    }
    return {
        "schema_version": 2, "client": client,
        "session_key": identity,
        "condition": condition, "task_kind": task_kind, "outcome": outcome,
        "models": sorted(models), "efforts": sorted(efforts), "client_versions": sorted(versions),
        "first_timestamp": started, "last_timestamp": ended, "records": records,
        "usage_snapshots": snapshots, "usage": latest, "accounting": method,
        'activity': activity_report,
        'response_evidence': dict(responses=len(responses), items=sum(r['items'] for r in responses),
                                 file_references=sum(r['files'] for r in responses),
                                 truncated=sum(r['truncated'] for r in responses),
                                 semantic_modes=dict(Counter(r['semantic'] for r in responses)),
                                 source='Captured response packets; separate from exact server-ledger joins.'),
        "tool_calls": dict(sorted(tools.items())),
        "direct_ibwd_calls": sum("direct" in i["sources"] for i in unique.values()),
        "observations": observations,
        "observed_ibwd_calls": len(unique),
        "server_observation_ids": sorted({i["observation_id"] for i in unique.values() if i["observation_id"]}),
        "child_usage": {"observed": child_seen, "aggregation": "excluded; no reliable disjoint child accounting contract"},
        "instruction_evidence": {"recognized_records": instruction_records,
                                 "ibwd_mentioned": ibwd_guidance,
                                 "scope": "Recognized Codex instruction records only; absence is inconclusive."},
        "warnings": sorted(warnings), "comparable": not warnings and condition != "unknown" and task_kind != "unknown" and outcome != "unknown",
        "limitations": ["One supplied log only; child-agent logs and omitted requests may be absent.",
                        "Structured MCP lifecycle events are counted; code strings and unstructured nested calls are not.",
                        "No direct IBWD calls does not mean IBWD was disabled or had no context overhead.",
                        "Observed session usage is not a causal savings measurement or subscription allowance."],
    }


def summarize_reports(paths: tuple[Path, ...]) -> dict:
    groups = defaultdict(list)
    seen = set()
    excluded = 0
    for path in paths:
        report = json.loads(path.read_text())
        if not isinstance(report, dict) or report.get("schema_version") not in (1, 2) or report.get("client") not in ("codex", "claude"):
            raise ValueError("Expected an IBWD usage report, not a raw transcript.")
        key = (report["client"], report["session_key"])
        if key in seen:
            raise ValueError("Multiple reports describe the same session. Keep only its latest snapshot.")
        seen.add(key)
        if not report["comparable"] or not report.get("usage"):
            excluded += 1
            continue
        group = (report["client"], tuple(report["models"]), tuple(report["efforts"]),
                 tuple(report["client_versions"]), report["task_kind"], report["condition"])
        groups[group].append(report)
    rows = []
    for key, reports in sorted(groups.items()):
        passed = [r for r in reports if r["outcome"] == "passed"]
        rows.append(dict(zip(("client", "models", "efforts", "client_versions", "task_kind", "condition"), key)) | {
            "sessions": len(reports), "passed": len(passed),
            "median_recorded_tokens_all_outcomes": statistics.median(r["usage"]["total_tokens"] for r in reports),
            "median_recorded_tokens_passed": statistics.median(r["usage"]["total_tokens"] for r in passed) if passed else None,
        })
    return {"groups": rows, "excluded_reports": excluded,
            "interpretation": "Observational groups, not matched tasks. Do not infer a savings percentage or extra messages from these medians."}
