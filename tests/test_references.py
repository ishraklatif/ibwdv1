from __future__ import annotations

from ibwd.scanner.javascript import extract_javascript_references
from ibwd.scanner.python import extract_python_references
from ibwd.scanner.references import UNKNOWN_RECEIVER, Binding


def test_python_imports_and_bindings():
    refs = extract_python_references(
        b"import os.path as p\n"
        b"import sys\n"
        b"from .a import b as c, d\n"
        b"from . import e\n"
        b"from x.y import *\n"
    )
    by_spec = {(i.spec, i.level): i for i in refs.imports}

    assert by_spec[("os.path", 0)].bindings == [Binding("p")]
    assert by_spec[("sys", 0)].bindings == [Binding("sys")]
    assert by_spec[("a", 1)].bindings == [Binding("c", "b"), Binding("d", "d")]
    assert by_spec[("", 1)].bindings == [Binding("e", "e")]
    # wildcard import: still a file->file import, but binds no names
    assert by_spec[("x.y", 0)].bindings == []


def test_python_calls_capture_receivers():
    refs = extract_python_references(
        b"def f(self):\n"
        b"    foo(1)\n"
        b"    self.g()\n"
        b"    m.h.k()\n"
        b"    get().z()\n"
    )
    calls = {(c.name, c.receiver) for c in refs.calls}
    assert ("foo", None) in calls
    assert ("g", "self") in calls
    assert ("k", "m.h") in calls
    assert ("z", UNKNOWN_RECEIVER) in calls  # receiver is an expression we can't name


def test_python_base_classes_skip_keyword_arguments():
    refs = extract_python_references(b"class A(B, m.C, metaclass=Meta):\n    pass\n")
    assert {(b.class_name, b.name, b.receiver) for b in refs.bases} == {("A", "B", None), ("A", "C", "m")}


def test_javascript_imports_cover_esm_require_and_reexport():
    refs = extract_javascript_references(
        b"import D, { a as b, c } from './m';\n"
        b"import * as ns from '../n';\n"
        b"import './side';\n"
        b"const { x, y: z } = require('./r');\n"
        b"const q = require('./q');\n"
        b"export { w } from './w';\n"
    )
    by_spec = {i.spec: i for i in refs.imports}

    assert by_spec["./m"].bindings == [Binding("D", "default"), Binding("b", "a"), Binding("c", "c")]
    assert by_spec["../n"].bindings == [Binding("ns")]
    assert by_spec["./side"].bindings == []
    assert by_spec["./r"].bindings == [Binding("x", "x"), Binding("z", "y")]
    assert by_spec["./q"].bindings == [Binding("q")]
    assert by_spec["./w"].bindings == []


def test_javascript_calls_include_new_and_skip_require():
    refs = extract_javascript_references(
        b"class A { m() { this.f(); new Foo(); ns.g(); require('zz'); } }\n"
    )
    calls = {(c.name, c.receiver) for c in refs.calls}
    assert calls == {("f", "this"), ("Foo", None), ("g", "ns")}


def test_typescript_extends_but_not_implements():
    refs = extract_javascript_references(
        b"class A extends B implements I<T> {}\nclass C extends ns.D {}\n",
        dialect="typescript",
    )
    assert {(b.class_name, b.name, b.receiver) for b in refs.bases} == {("A", "B", None), ("C", "D", "ns")}
