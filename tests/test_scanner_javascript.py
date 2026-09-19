from __future__ import annotations

from ibwd.scanner.javascript import extract_javascript_symbols

JS_SOURCE = b"""\
function main() {
  return 1;
}

class Widget {
  render() {
    return null;
  }

  onClick = () => {
    return 2;
  };
}

const helper = () => 42;
"""

TS_SOURCE = b"""\
export function util(): number {
  return 1;
}
"""


def test_extract_javascript_symbols_finds_function_class_method_and_field_arrow():
    symbols = extract_javascript_symbols(JS_SOURCE, "web/app.js", dialect="javascript")
    by_name = {s.name: s for s in symbols}

    assert by_name["main"].kind == "Function"
    assert by_name["main"].qualified_name == "web/app.js::main"

    assert by_name["Widget"].kind == "Class"
    assert by_name["Widget"].qualified_name == "web/app.js::Widget"

    assert by_name["render"].kind == "Method"
    assert by_name["render"].qualified_name == "web/app.js::Widget.render"

    assert by_name["onClick"].kind == "Method"
    assert by_name["onClick"].qualified_name == "web/app.js::Widget.onClick"

    assert by_name["helper"].kind == "Function"
    assert by_name["helper"].qualified_name == "web/app.js::helper"


def test_extract_typescript_symbols_finds_exported_function():
    symbols = extract_javascript_symbols(TS_SOURCE, "web/util.ts", dialect="typescript")
    assert len(symbols) == 1
    assert symbols[0].name == "util"
    assert symbols[0].kind == "Function"
    assert symbols[0].qualified_name == "web/util.ts::util"


def test_nested_functions_are_not_indexed():
    """Closures are a deliberate Sprint 2 scope cut (see python.py/javascript.py
    docstrings and SPRINT_2.md's Q2 benchmark caveat) -- pin the behavior so a
    future change to it is a conscious decision, not a silent regression."""
    source = b"""\
function outer() {
  function inner() {
    return 1;
  }
  return inner;
}
"""
    symbols = extract_javascript_symbols(source, "web/nested.js", dialect="javascript")
    names = {s.name for s in symbols}
    assert names == {"outer"}
