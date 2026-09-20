#!/usr/bin/env python3
"""Qualify benchmark tasks from an ORACLE graph (never from IBWD's answers) and write benchmarks/ground_truth/<repo>.yaml.

Usage: qualify_tasks.py REPO_DIR MANIFEST ORACLE.json OUT.yaml [--language python|typescript]

Strata (gate definition section 3): Q1 callers, Q2 dependencies, Q3 chain, small-function. For each stratum the tool lists every
qualifying candidate, then selects ONE by a fixed seeded hash order that does not look at IBWD (`SEED` below), so the choice
cannot be steered by which candidate IBWD answers well. A stratum with too few candidates is reported as a FAILED admission
criterion; nothing is manufactured to fill it.

Qualification (all conditions are computed from the oracle's `binding` CALLS edges between indexed, non-nested symbols in
production manifest files):
  Q1  target: function/method, callers in >= 3 distinct production files, 3..15 caller symbols, no type_declared incoming CALLS,
      and the number of textual call sites (`name(`, JSX `<name`) in production files equals the oracle's CALLS occurrences (the oracle is
      demonstrably complete for this name).
  Q2  source: function/method calling >= 3 distinct internal functions defined OUTSIDE its own module, 3..12 internal callees in
      total, no type_declared outgoing CALLS, >= 12 lines long.
  Q3  a directed chain A->B->C->D of exactly three CALLS edges (two intermediates), the unique shortest path between A and D,
      crossing at least one file boundary, every hop a direct owner (no nested-callback projection) and no nested target.
  small  function/method of <= 20 lines with 2..4 distinct internal callees (fan-out), no type_declared outgoing CALLS, >= 1 caller.
Every expected edge is verified against the source line it comes from (the callee's name must occur on that line).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

SEED = "sprint3-gt-v1"
TEST_LIKE = re.compile(r"(^|/)(tests?|__tests__|testing|e2e|mocks?|__mocks__|stories)(/|$)|(^|/)(conftest|test_[^/]*)\.py$|\.(test|spec|stories)\.[jt]sx?$")


def key(repo: str, stratum: str, ident: str) -> str:
    return hashlib.sha256(f"{SEED}:{repo}:{stratum}:{ident}".encode()).hexdigest()


def yaml_dump(obj, indent=0) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        out = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:\n{yaml_dump(v, indent + 1)}")
            else:
                out.append(f"{pad}{k}: {scalar(v)}")
        return "\n".join(out)
    if isinstance(obj, list):
        out = []
        for v in obj:
            if isinstance(v, dict):
                body = yaml_dump(v, indent + 1).split("\n")
                out.append(f"{pad}- {body[0].lstrip()}" + ("\n" + "\n".join(body[1:]) if len(body) > 1 else ""))
            else:
                out.append(f"{pad}- {scalar(v)}")
        return "\n".join(out)
    return f"{pad}{scalar(obj)}"


def scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return "[]" if isinstance(v, list) else "{}"
    return json.dumps(str(v))       # JSON string quoting is valid YAML


def main() -> int:
    repo_dir, manifest_path, oracle_path, out_path = sys.argv[1:5]
    language = sys.argv[sys.argv.index("--language") + 1] if "--language" in sys.argv else "python"
    repo, manifest = Path(repo_dir), json.loads(Path(manifest_path).read_text())
    oracle = json.loads(Path(oracle_path).read_text())
    exts = (".py",) if language == "python" else (".ts", ".tsx", ".js", ".jsx")
    prod = {f for f in manifest["included_files"] if f.endswith(exts) and not TEST_LIKE.search(f)}
    excluded_tests = sum(1 for f in manifest["included_files"] if f.endswith(exts) and TEST_LIKE.search(f))

    syms = {s["id"]: s for s in oracle["symbols"] if s["kind"] != "File" and not s.get("nested") and s["file"] in prod}
    callable_kinds = {"Function", "Method"}
    edges = [e for e in oracle["edges"] if e["relation"] == "CALLS" and e["source"] in syms and e["target"] in syms]
    binding = [e for e in edges if e["basis"] != "type_declared"]
    td_out, td_in = defaultdict(int), defaultdict(int)
    for e in edges:
        if e["basis"] == "type_declared":
            td_out[e["source"]] += 1
            td_in[e["target"]] += 1
    callers, callees = defaultdict(set), defaultdict(set)
    for e in binding:
        if e["source"] != e["target"]:
            callers[e["target"]].add(e["source"])
            callees[e["source"]].add(e["target"])
    occ = defaultdict(list)                                    # (projected owner, target) -> occurrences
    for o in oracle["occurrences"]:
        if o["relation"] == "CALLS" and o["resolution_basis"] != "type_declared":
            occ[(o["projected_owner_id"], o["target_id"])].append(o)

    src_cache: dict[str, list[str]] = {}

    def lines(file: str) -> list[str]:
        if file not in src_cache:
            src_cache[file] = (repo / file).read_text(encoding="utf-8", errors="replace").splitlines()
        return src_cache[file]

    def simple(sid: str) -> str:
        return sid.split("::", 1)[1].split(".")[-1]

    def qual(sid: str) -> str:
        return sid.split("::", 1)[1]

    prod_text = None

    def textual_call_sites(name: str) -> int:
        nonlocal prod_text
        if prod_text is None:
            prod_text = {f: lines(f) for f in prod}
        if language == "python":
            pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"\s*\(")
        else:                                               # name( , name<T>( , new name( and the JSX tags <name ...> / <name/>
            pat = re.compile(r"(?<![A-Za-z0-9_$.])" + re.escape(name) + r"\s*(?:<[^<>()]*>)?\s*\(|<" + re.escape(name) + r"(?=[\s/>])")
        n = 0
        for f, ls in prod_text.items():
            for ln in ls:
                s = ln.strip()
                if s.startswith(("def ", "async def ", "class ", "#", "function ", "//", "*", "/*", "import ", "export type", "type ")) or re.match(r"^(export\s+)?(default\s+)?(async\s+)?function\s", s):
                    continue
                n += len(pat.findall(ln))
        return n

    def evidence(src: str, tgt: str) -> dict | None:
        for o in occ.get((src, tgt), []):
            text = lines(o["file"])[o["start"][0] - 1]
            if simple(tgt) in text:
                return {"source": src, "target": tgt, "file": o["file"], "line": o["start"][0], "text": text.strip()}
        return None

    def loc(sid: str) -> dict:
        s = syms[sid]
        return {"id": sid, "file": s["file"], "line": s["line"], "lines": s["end_line"] - s["line"] + 1}

    # ---------------- Q1
    q1 = []
    for t in syms:
        if syms[t]["kind"] not in callable_kinds or simple(t).startswith("__") or td_in[t]:
            continue
        cs = callers.get(t, set())
        files = {syms[c]["file"] for c in cs}
        if not (3 <= len(cs) <= 15 and len(files) >= 3):
            continue
        n_occ = sum(len(occ[(c, t)]) for c in cs)
        if textual_call_sites(simple(t)) != n_occ:
            continue           # the name is called somewhere the oracle did not resolve: its caller set may be incomplete
        q1.append(t)
    # ---------------- Q2 / small
    q2, small = [], []
    for s in syms:
        if syms[s]["kind"] not in callable_kinds or td_out[s]:
            continue
        cs = {c for c in callees.get(s, set()) if syms[c]["kind"] in callable_kinds | {"Class"}}
        span = syms[s]["end_line"] - syms[s]["line"] + 1
        outside = {c for c in cs if syms[c]["file"] != syms[s]["file"]}
        if len(outside) >= 3 and len(cs) <= 12 and span >= 12:
            q2.append(s)
        if span <= 20 and 2 <= len(cs) <= 4 and callers.get(s):
            small.append(s)
    # ---------------- Q3: unique shortest path of exactly 3 hops, no nested-callback projection
    direct = {(e["source"], e["target"]) for e in binding
              if all(o["original_owner_id"] == o["projected_owner_id"] for o in occ.get((e["source"], e["target"]), [])) and syms[e["target"]]["kind"] in callable_kinds | {"Class"}
              and syms[e["source"]]["kind"] in callable_kinds}
    adj = defaultdict(set)
    for a, b in direct:
        adj[a].add(b)
    q3 = []
    for a in adj:
        dist, paths = {a: 0}, {a: 1}
        dq = deque([a])
        while dq:
            u = dq.popleft()
            if dist[u] == 3:
                continue
            for v in adj.get(u, ()):
                if v not in dist:
                    dist[v], paths[v] = dist[u] + 1, 0
                    dq.append(v)
                if dist[v] == dist[u] + 1:
                    paths[v] += paths[u]
        for d, k in dist.items():
            if k == 3 and paths[d] == 1 and simple(a) != simple(d):
                chain = [a]
                cur = a
                while cur != d:                      # reconstruct the unique shortest path
                    cur = next(v for v in adj[cur] if dist.get(v) == dist[cur] + 1 and (v == d or _reaches(adj, v, d, 3 - dist[cur] - 1)))
                    chain.append(cur)
                files = [syms[c]["file"] for c in chain]
                if len(set(files)) >= 2 and len(set(chain)) == 4 and not any(td_out[c] for c in chain[:-1]):
                    q3.append(tuple(chain))

    report = {"repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "oracle": oracle["oracle"], "seed": SEED,
              "production_files": len(prod), "test_like_files_excluded": excluded_tests,
              "candidates": {"Q1": len(q1), "Q2": len(q2), "Q3": len(q3), "small": len(small)}}
    tasks, failures = [], []

    def pick(stratum, cands, minimum, ident=lambda c: c if isinstance(c, str) else "->".join(c)):
        ordered = sorted(cands, key=lambda c: key(manifest["repo"], stratum, ident(c)))
        if len(cands) < minimum:
            failures.append(f"{stratum}: {len(cands)} qualifying candidates, {minimum} required")
        return ordered

    q1o, q2o, q3o, so = pick("Q1", q1, 3), pick("Q2", q2, 3), pick("Q3", q3, 1), pick("small", small, 1)
    report["candidate_order_first_five"] = {"Q1": q1o[:5], "Q2": q2o[:5], "Q3": [" -> ".join(c) for c in q3o[:3]], "small": so[:5]}

    def task_q1(t):
        cs = sorted(callers[t])
        ev = [evidence(c, t) for c in cs]
        if not all(ev):
            failures.append(f"Q1 {t}: expected edge without verifiable source evidence")
        return {"id": f"{manifest['repo']}-q1", "stratum": "Q1", "relation": "CALLS", "target": loc(t),
                "question": f"Which functions and methods in the production code call `{qual(t)}` (defined in {syms[t]['file']})? List each caller with its file.",
                "expected": [{"caller": c, "file": syms[c]["file"]} for c in cs], "evidence": [e for e in ev if e], "grading": "exact set of caller symbols"}

    def task_q2(s, stratum="Q2"):
        cs = sorted(c for c in callees[s] if syms[c]["kind"] in callable_kinds | {"Class"})
        ev = [evidence(s, c) for c in cs]
        if not all(ev):
            failures.append(f"{stratum} {s}: expected edge without verifiable source evidence")
        return {"id": f"{manifest['repo']}-{stratum.lower()}", "stratum": stratum, "relation": "CALLS", "source": loc(s),
                "question": f"Which functions, methods and classes defined in this repository does `{qual(s)}` (in {syms[s]['file']}) call directly? List each with its file.",
                "expected": [{"callee": c, "file": syms[c]["file"]} for c in cs], "evidence": [e for e in ev if e], "grading": "exact set of internal callees"}

    def task_q3(chain):
        ev = [evidence(a, b) for a, b in zip(chain, chain[1:])]
        if not all(ev):
            failures.append(f"Q3 {chain}: hop without verifiable source evidence")
        return {"id": f"{manifest['repo']}-q3", "stratum": "Q3", "relation": "CALLS", "source": loc(chain[0]), "target": loc(chain[-1]),
                "question": f"Trace the shortest call path from `{qual(chain[0])}` ({syms[chain[0]]['file']}) to `{qual(chain[-1])}` ({syms[chain[-1]]['file']}). Give every function on the path.",
                "expected": {"path": list(chain), "hops": 3, "any_valid_path_of_this_length": False, "note": "the shortest path is unique in the oracle graph"},
                "evidence": [e for e in ev if e], "grading": "the exact three-hop path"}

    if q1o:
        tasks.append(task_q1(q1o[0]))
    if q2o:
        tasks.append(task_q2(q2o[0]))
    if q3o:
        tasks.append(task_q3(q3o[0]))
    if so:
        tasks.append(task_q2(so[0], stratum="small"))
    doc = {"repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "manifest_sha256": manifest["manifest_sha256"],
           "oracle": oracle["oracle"], "oracle_adapter": oracle["adapter"], "selection": {"seed": SEED, "rule": "candidates ordered by sha256(seed:repo:stratum:id); the first is taken; IBWD output is never consulted"},
           "qualification_report": report, "failed_admission_criteria": failures, "tasks": tasks,
           "candidates": {"Q1": [loc(t) for t in q1o[:8]], "Q2": [loc(s) for s in q2o[:8]], "small": [loc(s) for s in so[:8]]}}
    Path(out_path).write_text(yaml_dump(doc) + "\n")
    print(f"{manifest['repo']:18} prod files {len(prod):3} | candidates Q1 {len(q1):3} Q2 {len(q2):3} Q3 {len(q3):3} small {len(small):3} | tasks {len(tasks)} | failures {failures}")
    return 0


def _reaches(adj, u, d, steps) -> bool:
    if steps == 0:
        return u == d
    return any(_reaches(adj, v, d, steps - 1) for v in adj.get(u, ()))


if __name__ == "__main__":
    raise SystemExit(main())
