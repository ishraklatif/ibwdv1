#!/usr/bin/env python3
"""Deterministic grader for the Sprint 3 A/B answers. Never calls a model.

    grade(spec, answer) -> {correct, missing_items, extra_items, invalid_items, parse_error, explanation}

`spec` is a frozen task specification (benchmarks/experiment/tasks/<id>.json); `answer` is the agent's final text or an already parsed
object. One JSON answer contract applies to BOTH conditions:

    {"task_id": "...", "answer": {"callers"|"callees": [{"file": "...", "symbol": "...", "relation": "CALLS"}]}}      (Q1 / Q2 / small)
    {"task_id": "...", "answer": {"path": [{"file": "...", "symbol": "..."}, ...], "relation": "CALLS"}}                (Q3)

Normalisation (fixed in advance, deliberately NOT loose): backslashes become `/`, a leading `./` and duplicate slashes are removed, surrounding
whitespace is stripped. Symbols are compared EXACTLY and case-sensitively as `name`, `Class.method` or `Class`; an item's identity is
`file::symbol`. There is no basename, suffix, substring or case-insensitive matching: a right name in the wrong file is a different item.
Duplicate items are collapsed. Absolute paths, `..` segments, unknown symbols (not indexed in the task's scope), wrong relations and
malformed items are INVALID items. Line numbers are ignored: IBWD has no call-site positions, so the contract never asks for them.

Q1/Q2/small: correct iff the answer set equals the expected set and nothing is invalid.
Q3: correct iff the ordered path equals one of the accepted minimum-cost paths (equal-cost alternatives are all listed in the spec); every
hop must also be a call edge of the oracle graph, otherwise it is reported as an invalid hop.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_spec(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _symbols(spec: dict, root: Path) -> tuple[set[str], set[tuple[str, str]]]:
    d = json.loads((root / spec["valid_symbols_file"]).read_text())
    return set(d["indexed_symbol_ids"]), {(a, b) for a, b in d.get("call_edges", [])}


def norm_path(p) -> str | None:
    if not isinstance(p, str) or not p.strip():
        return None
    p = re.sub(r"/+", "/", p.strip().replace("\\", "/"))
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("/") or ".." in p.split("/") or re.match(r"^[A-Za-z]:", p):
        return None
    return p


def extract_json(text: str):
    """The answer object from a final message: the whole text, a fenced json block, or the last balanced {...} that parses."""
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    for block in reversed(fenced):
        try:
            return json.loads(block)
        except ValueError:
            continue
    for start in [m.start() for m in re.finditer(r"\{", text)][::-1]:
        depth = 0
        for i in range(start, len(text)):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError:
                    break
    raise ValueError("no JSON object found in the answer")


def _result(correct=False, missing=(), extra=(), invalid=(), parse_error=None, explanation=""):
    return {"correct": bool(correct), "missing_items": sorted(missing), "extra_items": sorted(extra), "invalid_items": list(invalid),
            "parse_error": parse_error, "explanation": explanation}


def _items(entries, kind: str, symbols: set[str], invalid: list, want_relation: bool):
    ids, seen = [], set()
    for k, e in enumerate(entries):
        if not isinstance(e, dict):
            invalid.append({"item": e, "reason": "not an object"}); continue
        f, sym = norm_path(e.get("file")), e.get("symbol")
        if f is None:
            invalid.append({"item": e, "reason": "missing or non-repository-relative file"}); continue
        if not isinstance(sym, str) or not sym.strip():
            invalid.append({"item": e, "reason": "missing symbol"}); continue
        if want_relation and e.get("relation") != "CALLS":
            invalid.append({"item": e, "reason": f"relation {e.get('relation')!r} is not CALLS"}); continue
        ident = f"{f}::{sym.strip()}"
        if ident not in symbols:
            invalid.append({"item": e, "reason": "not an indexed symbol of this repository scope"}); continue
        if ident not in seen:
            seen.add(ident)
        ids.append(ident)
    return ids


def grade(spec: dict, answer, root: Path = ROOT) -> dict:
    try:
        obj = extract_json(answer) if isinstance(answer, str) else answer
    except ValueError as exc:
        return _result(parse_error=str(exc), explanation="the final answer contained no parseable JSON object")
    if not isinstance(obj, dict) or "answer" not in obj or not isinstance(obj["answer"], dict):
        return _result(parse_error="missing top-level `answer` object", explanation="the JSON does not follow the answer contract")
    if obj.get("task_id") != spec["task_id"]:
        return _result(parse_error=f"task_id {obj.get('task_id')!r} does not match {spec['task_id']!r}", explanation="answer is for a different task")
    symbols, edges = _symbols(spec, root)
    ans, invalid = obj["answer"], []
    stratum = spec["stratum"]

    if stratum == "Q3":
        path, rel = ans.get("path"), ans.get("relation")
        if not isinstance(path, list) or not path:
            return _result(parse_error="`answer.path` is missing or not a non-empty list", explanation="the path is incomplete")
        if rel != "CALLS":
            invalid.append({"item": "relation", "reason": f"relation {rel!r} is not CALLS"})
        ids = _items(path, "path", symbols, invalid, want_relation=False)
        if len(ids) != len(path):                      # a hop that is malformed or unknown cannot be part of a valid path
            return _result(invalid=invalid, explanation="the path contains invalid hops")
        for a, b in zip(ids, ids[1:]):
            if (a, b) not in edges:
                invalid.append({"item": f"{a} -> {b}", "reason": "not a call edge of the oracle graph"})
        accepted = [[f"{i['file']}::{i['symbol']}" for i in p] for p in spec["expected"]["accepted_paths"]]
        if not invalid and ids in accepted:
            return _result(True, explanation="a minimum-cost path" + (" (an equal-cost alternative)" if ids != accepted[0] else ""))
        union = {x for p in accepted for x in p}
        missing = [x for x in accepted[0] if x not in ids]
        extra = [x for x in ids if x not in union]
        why = "invalid hop(s)" if invalid else "a valid path, but not a minimum-cost path" if not extra and not missing else "wrong nodes on the path"
        return _result(missing=missing, extra=extra, invalid=invalid, explanation=why)

    key = "callers" if stratum == "Q1" else "callees"
    entries = ans.get(key)
    if not isinstance(entries, list):
        return _result(parse_error=f"`answer.{key}` is missing or not a list", explanation="the answer is incomplete")
    ids = set(_items(entries, key, symbols, invalid, want_relation=True))
    expected = {f"{i['file']}::{i['symbol']}" for i in spec["expected"][key]}
    missing, extra = expected - ids, ids - expected
    ok = not missing and not extra and not invalid
    parts = []
    if missing: parts.append(f"{len(missing)} expected item(s) missing")
    if extra: parts.append(f"{len(extra)} item(s) not in the expected set")
    if invalid: parts.append(f"{len(invalid)} invalid item(s)")
    return _result(ok, missing, extra, invalid, None, "exact match" if ok else "; ".join(parts))


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: grade_sprint3_answers.py SPEC.json ANSWER.(json|txt)")
    print(json.dumps(grade(load_spec(sys.argv[1]), Path(sys.argv[2]).read_text()), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
