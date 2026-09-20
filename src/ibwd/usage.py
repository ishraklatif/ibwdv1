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

CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
CLAUDE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _counts(value, fields):
    if not isinstance(value, dict):
        return None
    if any(type(value.get(k)) is not int or value[k] < 0 for k in fields):
        return None
    return {k: value[k] for k in fields}


def analyze_log(path: Path, client: str, condition="unknown", task_kind="unknown", outcome="unknown") -> dict:
    warnings = set()
    models, efforts, versions, session_ids = set(), set(), set(), set()
    tools, seen_calls = Counter(), set()
    latest = None
    messages = {}
    malformed = records = snapshots = 0
    started = ended = None

    def call(name, ident):
        if not isinstance(name, str):
            return
        if not ident:
            warnings.add("Tool calls without IDs cannot be counted reliably.")
            return
        if ident not in seen_calls:
            seen_calls.add(ident)
            tools[name] += 1

    with path.open(encoding="utf-8") as stream:
        for line in stream:
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
            stamp = event.get("timestamp")
            if isinstance(stamp, str):
                started = started or stamp
                ended = stamp
            if client == "codex":
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta":
                    sid = payload.get("id") or payload.get("session_id")
                    if sid:
                        session_ids.add(str(sid))
                    if payload.get("cli_version"):
                        versions.add(str(payload["cli_version"]))
                if event.get("type") == "turn_context":
                    if payload.get("model"):
                        models.add(str(payload["model"]))
                    if payload.get("effort"):
                        efforts.add(str(payload["effort"]))
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
            else:
                if event.get("sessionId"):
                    session_ids.add(str(event["sessionId"]))
                if event.get("version"):
                    versions.add(str(event["version"]))
                if event.get("effort"):
                    efforts.add(str(event["effort"]))
                message = event.get("message")
                if event.get("type") != "assistant" or not isinstance(message, dict):
                    continue
                model = message.get("model")
                if model == "<synthetic>":
                    continue
                if model:
                    models.add(str(model))
                mid = message.get("id")
                if not mid:
                    warnings.add("Assistant message without an ID; usage cannot be deduplicated.")
                    continue
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
    if condition == "disabled" and any("ibwd_" in name for name in tools):
        warnings.add("Declared disabled but direct IBWD calls were observed.")
    # Stable pseudonym supports replacing snapshots of a resumed session without exporting its ID.
    identity = next(iter(session_ids)) if len(session_ids) == 1 else str(path.resolve())
    return {
        "schema_version": 1, "client": client,
        "session_key": hashlib.sha256((client + ':' + identity).encode()).hexdigest()[:20],
        "condition": condition, "task_kind": task_kind, "outcome": outcome,
        "models": sorted(models), "efforts": sorted(efforts), "client_versions": sorted(versions),
        "first_timestamp": started, "last_timestamp": ended, "records": records,
        "usage_snapshots": snapshots, "usage": latest, "accounting": method,
        "tool_calls": dict(sorted(tools.items())),
        "direct_ibwd_calls": sum(n for name, n in tools.items() if "ibwd_" in name),
        "warnings": sorted(warnings), "comparable": not warnings and condition != "unknown" and task_kind != "unknown" and outcome != "unknown",
        "limitations": ["One supplied log only; child-agent logs and omitted requests may be absent.",
                        "Indirect calls through shell or orchestration tools are not detected.",
                        "No direct IBWD calls does not mean IBWD was disabled or had no context overhead.",
                        "Observed session usage is not a causal savings measurement or subscription allowance."],
    }


def summarize_reports(paths: tuple[Path, ...]) -> dict:
    groups = defaultdict(list)
    seen = set()
    excluded = 0
    for path in paths:
        report = json.loads(path.read_text())
        if not isinstance(report, dict) or report.get("schema_version") != 1 or report.get("client") not in ("codex", "claude"):
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
