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


def test_jsx_tags_are_recorded_as_component_uses():
    refs = extract_javascript_references(
        b"function A() { return (<View><Card.Header title='x'/><ui.Btn/><Foo>hi</Foo><div/><Card/></View>); }",
        dialect="tsx",
    )
    calls = {(c.name, c.receiver) for c in refs.calls}
    assert calls == {("View", None), ("Header", "Card"), ("Btn", "ui"), ("Foo", None), ("Card", None)}
    assert ("div", None) not in calls  # lowercase bare tags are DOM elements, not components


def test_default_export_names_are_recorded():
    def default_of(src: bytes) -> str | None:
        return extract_javascript_references(src, dialect="tsx").default_export

    assert default_of(b"export default function Foo() {}") == "Foo"
    assert default_of(b"export default class Foo {}") == "Foo"
    assert default_of(b"const Foo = () => null;\nexport default Foo;") == "Foo"
    assert default_of(b"const Foo = () => null;\nexport { Foo as default };") == "Foo"
    assert default_of(b"const Foo = () => null;\nexport default React.memo(Foo);") == "Foo"
    assert default_of(b"const Foo = () => null;\nexport default connect(mapState)(Foo);") == "Foo"
    assert default_of(b"class Foo {}\nmodule.exports = Foo;") == "Foo"
    assert default_of(b"export default () => null;") is None  # anonymous: nothing to resolve
    assert default_of(b"export { default } from './x';") is None  # a re-export, not this file's own default


def _py_values(src: str) -> set[tuple[str, str | None]]:
    return {(c.name, c.receiver) for c in extract_python_references(src.encode()).value_refs}


def _js_values(src: str, dialect: str = "tsx") -> set[tuple[str, str | None]]:
    return {(c.name, c.receiver) for c in extract_javascript_references(src.encode(), dialect=dialect).value_refs}


def test_python_value_uses_are_recorded():
    values = _py_values(
        "def f(items):\n"
        "    a = handler\n"
        "    b = [render, other]\n"
        "    c = {'k': fn}\n"
        "    run(cb, key=opt)\n"
        "    return self.method_ref, mod.func\n"
    )
    assert {("handler", None), ("render", None), ("other", None), ("fn", None), ("cb", None), ("opt", None)} <= values
    assert {("method_ref", "self"), ("func", "mod")} <= values


def test_python_calls_bindings_and_shadowed_names_are_not_value_uses():
    values = _py_values(
        "import os\n"
        "from x import imported\n"
        "class K(Base):\n"
        "    pass\n"
        "def helper(): pass\n"
        "def f(param, dflt=default_fn):\n"
        "    local = 1\n"
        "    for loop_var in range(3):\n"
        "        pass\n"
        "    helper()\n"          # a call, not a value use
        "    obj.method()\n"      # callee
        "    return param, local, loop_var, os.path\n"
    )
    names = {n for n, _ in values}
    assert "helper" not in names  # only ever called
    assert "param" not in names and "local" not in names and "loop_var" not in names  # locals shadow
    assert "Base" not in names  # base class, not a value use
    assert "imported" not in names  # import statement itself
    assert ("default_fn", None) in values  # a default value is evaluated in the outer scope


def test_python_local_variable_named_like_a_function_is_not_a_value_use():
    values = _py_values("def process(): pass\ndef f():\n    process = 3\n    return process\n")
    assert ("process", None) not in values


def test_javascript_value_uses_are_recorded():
    values = _js_values(
        "function A() {\n"
        "  const [state] = useReducer(reduceWithContext, init);\n"
        "  return (<Stack.Screen component={HomeScreen} onPress={this.handle} />);\n"
        "}\n"
        "const m = items.map(renderItem);\n"
        "const o = { onDone, key: other };\n"
    )
    assert {("reduceWithContext", None), ("HomeScreen", None), ("renderItem", None), ("onDone", None), ("other", None)} <= values
    assert ("handle", "this") in values


def test_javascript_calls_tags_bindings_and_shadowed_names_are_not_value_uses():
    values = _js_values(
        "import { imp } from './x';\n"
        "function helper() {}\n"
        "function A(param, { destructured }) {\n"
        "  const local = 1;\n"
        "  helper();\n"
        "  this.foo();\n"
        "  return (<Card title={local}><param /></Card>) && param && destructured;\n"
        "}\n"
        "export default A;\n"
    )
    names = {n for n, _ in values}
    assert names.isdisjoint({"helper", "Card", "param", "destructured", "local", "imp"})
    assert ("A", None) in values          # `export default A;`: the file uses A as its default export


def test_starred_single_element_list_call_is_a_call_not_a_value():
    # tree-sitter-python parses `[*g(x)]` as call(list_splat(g), (x)); the callee must still be `g`
    refs = extract_python_references(b"def f(x):\n    return [*g(x, 1)]\n")
    assert {(c.name, c.receiver) for c in refs.calls} == {("g", None)}
    assert ("g", None) not in {(c.name, c.receiver) for c in refs.value_refs}

    refs = extract_python_references(b"class K:\n    def f(self):\n        return [*self.helper(1)]\n")
    assert {(c.name, c.receiver) for c in refs.calls} == {("helper", "self")}


def test_function_local_imports_carry_their_function_scope():
    refs = extract_python_references(
        b"from a import top\n"
        b"def f():\n"
        b"    from b import inner\n"
        b"    return inner()\n"
    )
    scopes = {i.spec: i.scope for i in refs.imports}
    assert scopes["a"] is None
    assert scopes["b"] == (2, 4)


def test_dynamic_import_lazy_and_reexport_extraction():
    refs = extract_javascript_references(
        b"export * from './all';\n"
        b"export { a, b as c } from './named';\n"
        b"export { default as Zed } from './z';\n"
        b"const Page = lazy(() => import('./Page'));\n"
        b"async function f() {\n"
        b"  const { init } = await import('./db');\n"
        b"  const ns = await import('./ns');\n"
        b"}\n",
        dialect="tsx",
    )
    by_spec = {i.spec: i for i in refs.imports}

    assert by_spec["./Page"].bindings == [Binding("Page", "default")] and by_spec["./Page"].scope is None
    assert by_spec["./db"].bindings == [Binding("init", "init")] and by_spec["./db"].scope is not None  # local to f
    assert by_spec["./ns"].bindings == [Binding("ns")]
    assert [(r.spec, r.names) for r in refs.reexports] == [
        ("./all", None),
        ("./named", [("a", "a"), ("b", "c")]),
        ("./z", [("default", "Zed")]),
    ]


def test_typeof_in_a_type_position_is_not_a_value_use():
    values = _js_values(
        "export const getOptions = (id: string) => id;\n"
        "type Options = { queryConfig?: QueryConfig<typeof getOptions> };\n"
        "function useIt(x: ReturnType<typeof getOptions>) { return x; }\n"
        "const runtime = register(getOptions);\n"
    )
    assert ("getOptions", None) in values  # the genuine runtime value use (register(getOptions))
    assert sum(1 for v in values if v == ("getOptions", None)) == 1  # ...and only that one


def test_javascript_member_chain_head_and_default_export_are_uses():
    values = _js_values("function A() {}\nA.displayName = 'A';\nexport default A;\nconst x = Cls.CONST;\nLogger.log(1);\n")
    assert ("A", None) in values and ("Cls", None) in values and ("Logger", None) in values


def test_definitions_inside_an_iife_initialiser_are_indexed():
    from ibwd.scanner.symbols import extract_symbols_from_source

    source = (
        b"export const createThing = /* @__PURE__ */ (() => {\n"
        b"  function createThing(x) {\n    return helper(x)\n  }\n"
        b"  return createThing\n})()\n"
        b"const wrapped = ((y) => y) as Fn\n"
    )
    names = {s.name for s in extract_symbols_from_source(source, "a.ts", "typescript")}
    assert names == {"createThing", "wrapped"}


def test_javascript_dynamic_import_anywhere_and_default_parameter_values_and_overload_signatures():
    from ibwd.scanner.javascript import extract_javascript_references

    refs = extract_javascript_references(
        b"export const r = [{ lazy: () => import('./routes/landing').then(convert) }];\n"
        b"function over(a: string): void;\nfunction over(a: number): void;\nfunction over(a: any) {}\n"
        b"function withDefault(onError = noop) { return onError }\n",
        dialect="typescript",
    )
    assert ("./routes/landing", 1) in {(i.spec, i.line) for i in refs.imports}
    values = {(v.name, v.receiver) for v in refs.value_refs}
    assert ("noop", None) in values
    assert ("over", None) not in values                      # overload signature names are declarations


def test_a_barrel_written_as_import_then_export_list_is_followed(tmp_path):
    from ibwd.graph.database import connect
    from ibwd.scan import run_scan

    (tmp_path / "module.ts").write_text("export const coreModule = () => 1\n")
    (tmp_path / "core.ts").write_text("import { coreModule } from './module'\nexport { coreModule }\n")
    (tmp_path / "index.ts").write_text("export { coreModule } from './core'\n")
    (tmp_path / "use.ts").write_text("import { coreModule } from './index'\nexport function go() { return coreModule() }\n")
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    rows = {(r[0], r[1]) for r in conn.execute(
        "SELECT s.qualified_name, t.qualified_name FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id "
        "WHERE e.relation = 'CALLS' AND e.resolution_status = 'resolved'")}
    conn.close()
    assert ("use.ts::go", "module.ts::coreModule") in rows
