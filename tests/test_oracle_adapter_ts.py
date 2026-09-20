"""Exact-fixture acceptance test for benchmarks/tools/ts_oracle.js (the TypeScript oracle adapter).

Needs node and the oracle tooling: set IBWD_ORACLE_TOOLS to the evidence directory that contains `oracle-tools/` (with `typescript`).
Otherwise the test is skipped. Whole-repository TypeScript scoring must not proceed unless this passes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = os.environ.get("IBWD_ORACLE_TOOLS")
pytestmark = pytest.mark.skipif(not TOOLS, reason="set IBWD_ORACLE_TOOLS to run the TypeScript oracle adapter fixtures")

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "oracle_ts"
ADAPTER = ROOT / "benchmarks" / "tools" / "ts_oracle.js"
CORE, USE, CARD, INDEX = "src/core.ts", "src/use.tsx", "src/card.tsx", "src/index.ts"

EXPECTED_EDGES = {
    # (relation, source, target, basis)
    ("CALLS", CORE, f"{CORE}::helper", "binding"),                                 # module-level `touched = helper()` after astral/non-ASCII text
    ("INHERITS", f"{CORE}::Child", f"{CORE}::Base", "binding"),
    ("CALLS", f"{CORE}::P.conf", f"{CORE}::helper", "binding"),                    # accessor pair: getter and setter keep the owner P.conf
    ("CALLS", f"{CORE}::P.conf", f"{CORE}::outer", "binding"),
    ("CALLS", f"{CORE}::make", f"{CORE}::helper", "binding"),                      # nested `inner` rolls up to `make`
    ("CALLS", f"{CORE}::ov", f"{CORE}::helper", "binding"),                        # overload signatures do not replace the implementation
    ("IMPORTS", INDEX, CORE, "path"),                                              # export { helper, outer } from "./core"
    ("IMPORTS", USE, CARD, "path"),
    ("IMPORTS", USE, CORE, "path"),                                                # "@/core": tsconfig paths alias
    ("IMPORTS", USE, INDEX, "path"),
    ("CALLS", f"{USE}::a", f"{CORE}::helper", "binding"),                          # fn()
    ("CALLS", f"{USE}::b", f"{CORE}::outer", "binding"),                           # outer(fn): CALLS outer ...
    ("REFERENCES", f"{USE}::b", f"{CORE}::helper", "binding"),                     # ... and REFERENCES helper; `fn` inside outer is a parameter: no edge
    ("CALLS", f"{USE}::d", f"{CORE}::helper", "binding"),                          # core.helper(): namespace import
    ("CALLS", f"{USE}::e", f"{CORE}::helper", "binding"),                          # h2(): named import through a barrel re-export
    ("REFERENCES", f"{USE}::f", f"{CORE}::helper", "binding"),                     # [1].map(helper)
    ("CALLS", f"{USE}::g", f"{CORE}::Base.newMethod", "type_declared"),            # obj.newMethod() with obj: Base — a POSSIBLE target
    ("INHERITS", f"{USE}::K", f"{CORE}::Child", "binding"),
    ("CALLS", f"{USE}::K.m", f"{USE}::K.n", "binding"),                            # this.n()
    ("CALLS", f"{USE}::i", f"{CORE}::helper", "binding"),                          # nested `cb` rolls up to `i`
    ("CALLS", f"{USE}::j", f"{CORE}::defaultFn", "binding"),                       # default import
    ("CALLS", f"{USE}::k", f"{CORE}::Base.newMethod", "type_declared"),            # optional call maybe?.newMethod()
    ("CALLS", f"{USE}::l", f"{CORE}::Child", "binding"),                           # constructor call
    ("CALLS", f"{USE}::m1", f"{CARD}::Card", "binding"),                           # <Card ... />
    ("CALLS", f"{USE}::m1", f"{USE}::compute", "binding"),                         # value={compute()}
    ("REFERENCES", f"{USE}::m1", f"{USE}::handler", "binding"),                    # onClick={handler}: a reference, not a call
    ("REFERENCES", f"{USE}::typeVsValue", f"{CORE}::helper", "binding"),           # `typeof helper === ...` in expression position
    ("CALLS", f"{USE}::anon", f"{CORE}::helper", "binding"),                       # anonymous callback: owner is `anon`
    ("CALLS", f"{USE}::ov1", f"{CORE}::ov", "binding"),
    ("REFERENCES", f"{USE}::useProp", f"{CORE}::P.conf", "type_declared"),         # accessor read through a typed receiver
}


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tools = Path(TOOLS)
    work = tmp_path_factory.mktemp("tsfx")
    shutil.copytree(FIXTURE, work / "fx")
    out = work / "graph.json"
    env = {**os.environ, "PATH": f"/opt/homebrew/bin:{os.environ['PATH']}"}
    subprocess.run(["node", str(ADAPTER), "--typescript", str(tools / "oracle-tools" / "node_modules" / "typescript"), "--repo", str(work / "fx"),
                    "--manifest", str(work / "fx" / "manifest.json"), "--project", "tsconfig.json", "--out", str(out)], check=True, capture_output=True, env=env)
    return json.loads(out.read_text())


def test_edges_match_the_expected_classification_exactly(graph):
    actual = {(e["relation"], e["source"], e["target"], e["basis"]) for e in graph["edges"]}
    assert actual == EXPECTED_EDGES, (sorted(actual - EXPECTED_EDGES), sorted(EXPECTED_EDGES - actual))
    assert graph["unassigned_files"] == []


def test_type_positions_are_never_runtime_references(graph):
    type_use = {(o["file"], tuple(o["start"]), o["target_id"]) for o in graph["occurrences"] if o["relation"] == "TYPE_USE"}
    assert (USE, (73, 39), f"{CORE}::helper") in type_use                          # `fn: typeof helper`
    assert not any(e["source"] == f"{USE}::c" for e in graph["edges"])            # c(x: Child): Child — types only


def test_function_typed_property_call_creates_no_definite_edge(graph):
    # h(o: Opts): o.backoff() — the property's declared type does not prove which function runs
    assert not any(e["source"] == f"{USE}::h" for e in graph["edges"])
    assert not any(e["target"] == f"{CORE}::defaultBackoff" for e in graph["edges"])


def test_nested_functions_keep_their_original_owner_and_targets_are_not_replaced(graph):
    nested = {(o["original_owner_id"].split("::")[1], o["projected_owner_id"].split("::")[1], o["target_id"].split("::")[1], o["relation"])
              for o in graph["occurrences"] if "<locals>" in o["original_owner_id"]}
    assert ("make.<locals>.inner", "make", "helper", "CALLS") in nested
    assert ("i.<locals>.cb", "i", "helper", "CALLS") in nested
    assert {(o["original_owner_id"].split("::")[1], o["target_id"].split("::")[1]) for o in graph["nested_target_occurrences"]} == {
        ("make", "make.<locals>.inner"), ("i", "i.<locals>.cb")}


def test_astral_and_non_ascii_text_before_a_reference_does_not_shift_its_position(graph):
    line = 'export const label = "héllo→世界😀"; export const touched = helper();'
    hit = [o for o in graph["occurrences"] if o["file"] == CORE and o["target_id"].endswith("::helper") and o["start"][0] == 49]
    utf16_col = len(line[: line.index("helper()")].encode("utf-16-le")) // 2
    assert [(o["relation"], o["start"][1]) for o in hit] == [("CALLS", utf16_col)]
    assert graph["text_encoding"] == "UTF16"                                       # TypeScript's own unit; astral characters count twice
