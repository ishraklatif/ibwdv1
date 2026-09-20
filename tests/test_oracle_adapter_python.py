"""Exact-fixture acceptance test for benchmarks/tools/scip_python_to_graph.py (the Python oracle adapter).

Needs the oracle tooling (scip-python, node, a venv with protobuf + scip_pb2). Set IBWD_ORACLE_TOOLS to the evidence directory
that contains `oracle-tools/` and `oracle-venv/`; otherwise the test is skipped. Whole-repository scoring must not proceed
unless this passes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = os.environ.get("IBWD_ORACLE_TOOLS")
pytestmark = pytest.mark.skipif(not TOOLS, reason="set IBWD_ORACLE_TOOLS to run the oracle adapter fixtures")

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "oracle_python"
ADAPTER = ROOT / "benchmarks" / "tools" / "scip_python_to_graph.py"

CORE, USE, INIT = "pkg/core.py", "pkg/use.py", "pkg/__init__.py"

EXPECTED_EDGES = {
    # (relation, source, target, resolution_basis)
    ("CALLS", CORE, f"{CORE}::helper", "binding"),                                 # module-level `touched = helper()` after non-ASCII text
    ("REFERENCES", f"{CORE}::Opts", f"{CORE}::default_backoff", "binding"),        # class-body value: owned by the class (executed at import)
    ("INHERITS", f"{CORE}::Child", f"{CORE}::Base", "binding"),                    # `Generic[T]` is external: no edge
    ("CALLS", f"{CORE}::P.conf", f"{CORE}::helper", "binding"),                    # accessor pair: both bodies keep their owner
    ("CALLS", f"{CORE}::P.conf", f"{CORE}::outer", "binding"),
    ("CALLS", f"{CORE}::make", f"{CORE}::helper", "binding"),                      # nested `inner` rolls up to `make`
    ("CALLS", f"{CORE}::ov", f"{CORE}::helper", "binding"),                        # overload stubs do not overwrite the implementation
    ("IMPORTS", INIT, CORE, "binding"),                                            # relative import
    ("IMPORTS", USE, INIT, "binding"),                                             # from pkg import helper as h2
    ("IMPORTS", USE, CORE, "binding"),                                             # import pkg.core / from pkg.core import ...
    ("INHERITS", f"{USE}::K", f"{CORE}::Child", "binding"),
    ("CALLS", f"{USE}::K.m", f"{USE}::K.n", "binding"),                            # self.n()
    ("CALLS", f"{USE}::a", f"{CORE}::helper", "binding"),                          # fn()
    ("CALLS", f"{USE}::b", f"{CORE}::outer", "binding"),                           # outer(fn): CALLS outer ...
    ("REFERENCES", f"{USE}::b", f"{CORE}::helper", "binding"),                     # ... and REFERENCES helper
    ("CALLS", f"{USE}::d", f"{CORE}::helper", "binding"),                          # core.helper(): module attribute
    ("CALLS", f"{USE}::e", f"{CORE}::helper", "binding"),                          # h2(): alias through a re-export
    ("REFERENCES", f"{USE}::f", f"{CORE}::helper", "binding"),                     # map(helper, ...)
    ("CALLS", f"{USE}::g", f"{CORE}::Base.new_method", "type_declared"),           # obj.new_method() with obj: Base — a POSSIBLE target
    ("CALLS", f"{USE}::i", f"{CORE}::helper", "binding"),                          # nested `cb` rolls up to `i`
    ("CALLS", f"{CORE}::Coll", f"{CORE}::helper", "binding"),                      # class-body call: owned by the class
    ("REFERENCES", f"{USE}::r1", f"{CORE}::Base.new_method", "type_declared"),      # obj.new_method as a value, obj: Base — POSSIBLE target
    ("REFERENCES", f"{USE}::r2", f"{CORE}::Base.new_method", "binding"),           # Base.new_method as a value: lexical class receiver
    ("REFERENCES", f"{USE}::r2", f"{CORE}::Base", "binding"),                       # `Base` used as a namespace
    ("CALLS", f"{USE}::flat", f"{CORE}::Base.new_method", "type_declared"),         # `self` in a module-level function is just a typed parameter
    ("CALLS", f"{USE}::via_variable", f"{CORE}::Base.new_method", "type_declared"),  # a module-level instance: inferred type, not lexical
    ("CALLS", USE, f"{CORE}::Base", "binding"),                                    # instance = Base() at module level
    ("REFERENCES", f"{CORE}::Slotted.__init__", f"{CORE}::helper", "binding"),      # self.fn() on a __slots__ name creates NO edge (scip-python reports the class symbol)
    ("CALLS", f"{CORE}::shadowed_import", f"{CORE}::Prop", "binding"),
    ("CALLS", f"{CORE}::shadowed_import", f"{CORE}::Prop.read", "type_declared"),  # a comprehension variable named `cls` is not the lexical class
    ("REFERENCES", f"{CORE}::Prop.read", f"{CORE}::Prop.val", "binding"),          # @property: defined as a term, used as `().`
    ("REFERENCES", f"{CORE}::pick", f"{CORE}::helper", "binding"),                 # (a if c else b)(x): the branches are not the callee
    ("REFERENCES", f"{CORE}::pick", f"{CORE}::outer", "binding"),
    ("CALLS", f"{CORE}::Coll.use", f"{CORE}::Coll.esc", "binding"),                # self.esc(): NOT the nested `build.<locals>.esc` that shares its symbol
}


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    tools = Path(TOOLS)
    work = tmp_path_factory.mktemp("fx")            # a clean root: no parent packages leak into scip-python's module names
    shutil.copytree(FIXTURE / "pkg", work / "pkg")
    shutil.copy(FIXTURE / "manifest.json", work / "manifest.json")
    env = {**os.environ, "PATH": f"{tools / 'oracle-tools' / 'node_modules' / '.bin'}:/opt/homebrew/bin:{os.environ['PATH']}"}
    index = work / "fx.scip"
    subprocess.run(["scip-python", "index", ".", "--project-name", "fx", "--project-version", "0", "--output", str(index)],
                   cwd=work, env=env, check=True, capture_output=True)
    out = work / "graph.json"
    subprocess.run([str(tools / "oracle-venv" / "bin" / "python"), str(ADAPTER), str(index), str(work), str(work / "manifest.json"), str(out),
                    "--proto-dir", str(tools / "oracle-tools")], check=True, capture_output=True)
    return json.loads(out.read_text())


def test_edges_match_the_expected_classification_exactly(graph):
    actual = {(e["relation"], e["source"], e["target"], e["basis"] if e["basis"] != "path" else "binding") for e in graph["edges"]}
    assert actual == EXPECTED_EDGES, (sorted(actual - EXPECTED_EDGES), sorted(EXPECTED_EDGES - actual))


def test_type_positions_are_never_runtime_references(graph):
    type_use = {(o["file"], tuple(o["start"]), o["target_id"]) for o in graph["occurrences"] if o["relation"] == "TYPE_USE"}
    assert type_use == {(USE, (14, 9), f"{CORE}::Child"), (USE, (14, 19), f"{CORE}::Child"),   # def c(x: Child) -> Child
                        (USE, (30, 11), f"{CORE}::Base"), (USE, (42, 9), f"{CORE}::Opts"),
                        (USE, (53, 12), f"{CORE}::Base"), (USE, (61, 15), f"{CORE}::Base"),
                        (USE, (72, 13), f"{CORE}::Child"), (USE, (72, 21), f"{CORE}::Base")}   # `type Alias = Child | Base`
    assert not any(e["target"] == f"{CORE}::Opts" for e in graph["edges"])


def test_function_valued_property_call_creates_no_definite_edge(graph):
    # h(o: Opts): o.backoff() — the property's declared type does not prove which function runs
    assert not any(e["source"] == f"{USE}::h" for e in graph["edges"])


def test_nested_functions_keep_their_original_owner_and_targets_are_not_replaced(graph):
    nested = {(o["original_owner_id"].split("::")[1], o["projected_owner_id"].split("::")[1], o["target_id"].split("::")[1], o["relation"])
              for o in graph["occurrences"] if o["file"] in (CORE, USE) and "<locals>" in (o["original_owner_id"] + o["target_id"])}
    assert ("make.<locals>.inner", "make", "helper", "CALLS") in nested         # original owner preserved, projected to `make`
    assert ("i.<locals>.cb", "i", "helper", "CALLS") in nested
    assert {(o["original_owner_id"].split("::")[1], o["target_id"].split("::")[1]) for o in graph["nested_target_occurrences"]} == {
        ("make", "make.<locals>.inner"), ("i", "i.<locals>.cb"), ("Coll.build", "Coll.build.<locals>.esc")}                # nested targets reported, never rewritten to `make`/`i`


def test_non_ascii_text_before_a_reference_does_not_shift_its_position(graph):
    line = 'label = "héllo→世界"; touched = helper()'
    hits = [o for o in graph["occurrences"] if o["file"] == CORE and o["target_id"].endswith("::helper") and o["start"][0] == 56]
    assert [(o["relation"], o["start"][1]) for o in hits] == [("CALLS", line.index("helper()"))]
    # scip-python DECLARES UTF-8 but emits UTF-16 columns; the adapter validates instead of trusting the declaration
    assert graph["declared_text_encoding"] == "UTF8" and graph["text_encoding"] == "UTF16"


def test_a_parameter_that_shadows_a_module_function_is_not_a_call_edge(graph):
    # shadow(helper): helper() calls the parameter, not the module-level function
    assert not any(e["source"] == f"{CORE}::shadow" for e in graph["edges"])
