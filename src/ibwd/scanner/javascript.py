"""JS/JSX/TS/TSX Class/Function/Method extraction via tree-sitter."""

from __future__ import annotations

import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

from ibwd.scanner.references import (
    Binding,
    BaseRef,
    CallRef,
    FileReferences,
    ImportRef,
    ReExport,
    clean_receiver,
)
from ibwd.scanner.symbols import SymbolInfo
from ibwd.scanner.valuerefs import javascript_value_refs

_LANGUAGES = {
    "javascript": Language(tsjavascript.language()),
    "typescript": Language(tstypescript.language_typescript()),
    "tsx": Language(tstypescript.language_tsx()),
}

_FUNCTION_DECLARATION_TYPES = {"function_declaration", "generator_function_declaration"}
_FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression", "generator_function"}


def _text(node: Node) -> str:
    return node.text.decode("utf-8")


_WRAPPERS = ("parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression", "type_assertion")


def _unwrap(node: Node | None) -> Node | None:
    """`(() => {})`, `(() => {}) as T`, `(fn) satisfies T`, `fn!`: the function underneath."""
    while node is not None and node.type in _WRAPPERS and node.named_children:
        node = node.named_children[-1] if node.type == "type_assertion" else node.named_children[0]
    return node


def extract_javascript_symbols(source: bytes, file_path: str, dialect: str = "javascript") -> list[SymbolInfo]:
    parser = Parser(_LANGUAGES[dialect])
    tree = parser.parse(source)
    symbols: list[SymbolInfo] = []

    def add(node: Node, name: str, kind: str, class_stack: list[str]) -> None:
        qualified = ".".join([*class_stack, name])
        symbols.append(
            SymbolInfo(
                name=name,
                kind=kind,
                qualified_name=f"{file_path}::{qualified}",
                file_path=file_path,
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
            )
        )

    def visit(node: Node, class_stack: list[str]) -> None:
        for child in node.children:
            if child.type in _FUNCTION_DECLARATION_TYPES:
                name_node = child.child_by_field_name("name")
                if name_node:
                    add(child, _text(name_node), "Function", class_stack)
                continue

            if child.type == "class_declaration":
                name_node = child.child_by_field_name("name")
                name = _text(name_node) if name_node else None
                if name:
                    add(child, name, "Class", class_stack)
                    body = child.child_by_field_name("body")
                    if body:
                        visit(body, [*class_stack, name])
                continue

            if child.type == "method_definition" and class_stack:
                name_node = child.child_by_field_name("name")
                if name_node:
                    add(child, _text(name_node), "Method", class_stack)
                continue

            if child.type == "field_definition" and class_stack:
                value = child.child_by_field_name("value")
                if value is not None and value.type in _FUNCTION_VALUE_TYPES:
                    name_node = child.child_by_field_name("property")
                    if name_node:
                        add(child, _text(name_node), "Method", class_stack)
                continue

            if child.type == "variable_declarator":
                value = _unwrap(child.child_by_field_name("value"))
                if value is not None and value.type in _FUNCTION_VALUE_TYPES:
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        kind = "Method" if class_stack else "Function"
                        add(child, _text(name_node), kind, class_stack)
                else:
                    # `export const createAsyncThunk = (() => { function createAsyncThunk() {} ... })()`: definitions inside a
                    # non-function initialiser (an IIFE, a wrapper call) are still definitions
                    visit(child, class_stack)
                continue

            visit(child, class_stack)

    visit(tree.root_node, [])
    return symbols


def _string_value(node: Node) -> str:
    return _text(node).strip("'\"`")


def _js_import_statement(node: Node, refs: FileReferences) -> None:
    source = node.child_by_field_name("source")
    if source is None:
        return
    bindings: list[Binding] = []
    for child in node.children:
        if child.type != "import_clause":
            continue
        for part in child.children:
            if part.type == "identifier":
                bindings.append(Binding(_text(part), "default"))
            elif part.type == "namespace_import":
                for ident in part.children:
                    if ident.type == "identifier":
                        bindings.append(Binding(_text(ident)))
            elif part.type == "named_imports":
                for spec in part.children:
                    if spec.type != "import_specifier":
                        continue
                    name = spec.child_by_field_name("name")
                    alias = spec.child_by_field_name("alias")
                    if name is not None:
                        bindings.append(Binding(_text(alias) if alias else _text(name), _text(name)))
    refs.imports.append(ImportRef(_string_value(source), 0, bindings, node.start_point.row + 1))


def _default_export_name(value: Node | None) -> str | None:
    """The nameable symbol behind `export default <value>`: `Foo`, or `Foo` in a wrapper like `memo(Foo)`."""
    if value is None:
        return None
    if value.type == "identifier":
        return _text(value)
    if value.type == "call_expression":  # export default connect(mapState)(Foo) / memo(Foo) / React.memo(Foo)
        args = value.child_by_field_name("arguments")
        if args is not None:
            idents = [a for a in args.named_children if a.type == "identifier"]
            if idents:
                return _text(idents[-1])
        return _default_export_name(value.child_by_field_name("function"))
    return None


def _js_default_export(node: Node, refs: FileReferences) -> None:
    """`export default function Foo`, `export default Foo`, `export { Foo as default }`."""
    if any(child.type == "default" for child in node.children):
        declaration = node.child_by_field_name("declaration")
        if declaration is not None:
            name = declaration.child_by_field_name("name")
            if name is not None:
                refs.default_export = _text(name)
            return
        refs.default_export = _default_export_name(node.child_by_field_name("value"))
        return
    for clause in node.children:
        if clause.type != "export_clause" or node.child_by_field_name("source") is not None:
            continue
        for spec in clause.children:
            alias = spec.child_by_field_name("alias") if spec.type == "export_specifier" else None
            name = spec.child_by_field_name("name") if alias is not None else None
            if alias is not None and name is not None and _text(alias) == "default":
                refs.default_export = _text(name)


def _js_commonjs_export(node: Node, refs: FileReferences) -> None:
    """`module.exports = Foo` names the file's default export."""
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is not None and right is not None and left.type == "member_expression" and _text(left) == "module.exports":
        name = _default_export_name(right)
        if name:
            refs.default_export = name


_JS_FUNCTION_NODES = (
    "function_declaration", "function_expression", "generator_function_declaration",
    "generator_function", "arrow_function", "method_definition",
)


def _js_function_scope(node: Node) -> tuple[int, int] | None:
    parent = node.parent
    while parent is not None:
        if parent.type in _JS_FUNCTION_NODES:
            return parent.start_point.row + 1, parent.end_point.row + 1
        parent = parent.parent
    return None


def _js_reexport(node: Node, refs: FileReferences) -> None:
    """`export * from './m'` / `export { a, b as c } from './m'`: a file->file import, and a re-export map
    so an import of `a` through this barrel can be followed to where `a` is really defined."""
    source = node.child_by_field_name("source")
    if source is None:
        return
    spec = _string_value(source)
    refs.imports.append(ImportRef(spec, 0, [], node.start_point.row + 1))
    names: list[tuple[str, str]] | None = None
    for child in node.children:
        if child.type == "export_clause":
            names = names or []
            for part in child.children:
                if part.type != "export_specifier":
                    continue
                name = part.child_by_field_name("name")
                alias = part.child_by_field_name("alias")
                if name is not None:
                    names.append((_text(name), _text(alias) if alias else _text(name)))
        elif child.type == "namespace_export":  # export * as ns from './m' — a namespace object, not names
            return
    refs.reexports.append(ReExport(spec, names))


def _js_local_export_specifiers(node: Node) -> list[tuple[str, str]]:
    """`export { a, b as c }` WITHOUT a source: (local name, exported name) pairs."""
    if node.child_by_field_name("source") is not None:
        return []
    out: list[tuple[str, str]] = []
    for child in node.children:
        if child.type == "export_clause":
            for part in child.children:
                if part.type == "export_specifier":
                    name, alias = part.child_by_field_name("name"), part.child_by_field_name("alias")
                    if name is not None:
                        out.append((_text(name), _text(alias) if alias else _text(name)))
    return out


def _require_spec(node: Node | None) -> str | None:
    """The './x' in `require('./x')`, else None."""
    if node is None or node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if function is None or function.type != "identifier" or _text(function) != "require":
        return None
    if args is None or args.named_child_count == 0 or args.named_children[0].type != "string":
        return None
    return _string_value(args.named_children[0])


def _dynamic_import_spec(node: Node | None) -> str | None:
    """The './x' in `import('./x')`, looking through `await`."""
    if node is not None and node.type == "await_expression" and node.named_child_count:
        node = node.named_children[0]
    if node is None or node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if function is None or function.type != "import":
        return None
    if args is None or args.named_child_count == 0 or args.named_children[0].type != "string":
        return None
    return _string_value(args.named_children[0])


def _lazy_import_spec(node: Node | None) -> str | None:
    """`lazy(() => import('./X'))` / `React.lazy(() => import('./X'))` -> './X'."""
    if node is None or node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    if function is None or _text(function).split(".")[-1] != "lazy":
        return None
    args = node.child_by_field_name("arguments")
    if args is None or args.named_child_count == 0:
        return None
    loader = args.named_children[0]
    if loader.type not in ("arrow_function", "function_expression"):
        return None
    body = loader.child_by_field_name("body")
    if body is not None and body.type == "statement_block":
        for stmt in body.named_children:
            if stmt.type == "return_statement" and stmt.named_child_count:
                return _dynamic_import_spec(stmt.named_children[0])
        return None
    return _dynamic_import_spec(body)


def _js_require(node: Node, refs: FileReferences) -> None:
    """`const x = require('./m')`, `const { a, b: c } = require('./m')`, `await import('./m')`, `lazy(() => import('./X'))`."""
    value = node.child_by_field_name("value")
    target = node.child_by_field_name("name")
    lazy_spec = _lazy_import_spec(value)
    if lazy_spec is not None and target is not None and target.type == "identifier":
        refs.imports.append(ImportRef(lazy_spec, 0, [Binding(_text(target), "default")], node.start_point.row + 1, _js_function_scope(node)))
        return
    spec = _require_spec(value) or _dynamic_import_spec(value)
    if spec is None or target is None:
        return
    bindings: list[Binding] = []
    if target.type == "identifier":
        bindings.append(Binding(_text(target)))
    elif target.type == "object_pattern":
        for prop in target.children:
            if prop.type == "shorthand_property_identifier_pattern":
                bindings.append(Binding(_text(prop), _text(prop)))
            elif prop.type == "pair_pattern":
                key = prop.child_by_field_name("key")
                value = prop.child_by_field_name("value")
                if key is not None and value is not None and value.type == "identifier":
                    bindings.append(Binding(_text(value), _text(key)))
    refs.imports.append(ImportRef(spec, 0, bindings, node.start_point.row + 1, _js_function_scope(node)))


def _js_callee(function: Node | None, line: int, refs: FileReferences) -> None:
    if function is None:
        return
    if function.type == "identifier":
        name = _text(function)
        if name != "require":
            refs.calls.append(CallRef(name, None, line))
    elif function.type == "member_expression":
        obj = function.child_by_field_name("object")
        prop = function.child_by_field_name("property")
        if obj is not None and prop is not None:
            refs.calls.append(CallRef(_text(prop), clean_receiver(_text(obj)), line))


def _js_jsx(node: Node, refs: FileReferences) -> None:
    """`<Card />` / `<Card.Header>` / `<ui.Btn />` — rendering a component is a use of it.

    Recorded as a call so callers/dependents/trace_path see React usage. Lowercase
    bare tags (`<div>`) are intrinsic DOM elements, not repo symbols, and are skipped.
    """
    name = node.child_by_field_name("name")
    if name is None:
        return
    line = node.start_point.row + 1
    if name.type == "identifier":
        text = _text(name)
        if text[:1].isupper():
            refs.calls.append(CallRef(text, None, line))
    elif name.type == "member_expression":
        obj = name.child_by_field_name("object")
        prop = name.child_by_field_name("property")
        if obj is not None and prop is not None:
            refs.calls.append(CallRef(_text(prop), clean_receiver(_text(obj)), line))


def _js_bases(node: Node, refs: FileReferences) -> None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = _text(name_node)
    class_line = node.start_point.row + 1
    for heritage in node.children:
        if heritage.type != "class_heritage":
            continue
        # JS: `extends <expr>` is a direct child; TS wraps it in extends_clause
        # (and puts `implements` in a sibling clause we deliberately skip).
        exprs = []
        for part in heritage.children:
            if part.type == "extends_clause":
                exprs.extend(part.children_by_field_name("value"))
            elif part.type in ("identifier", "member_expression"):
                exprs.append(part)
        for expr in exprs:
            if expr.type == "identifier":
                refs.bases.append(BaseRef(class_name, class_line, _text(expr), None))
            elif expr.type == "member_expression":
                obj = expr.child_by_field_name("object")
                prop = expr.child_by_field_name("property")
                if obj is not None and prop is not None:
                    refs.bases.append(BaseRef(class_name, class_line, _text(prop), clean_receiver(_text(obj))))


def extract_javascript_references(source: bytes, dialect: str = "javascript") -> FileReferences:
    """Extract imports, call sites and base classes (unresolved) from JS/TS source."""
    parser = Parser(_LANGUAGES[dialect])  # keep a reference: a temporary Parser crashes Node.text
    tree = parser.parse(source)
    refs = FileReferences()
    pending_local_exports: list[tuple[str, str]] = []

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        line = node.start_point.row + 1
        if node.type == "import_statement":
            _js_import_statement(node, refs)
        elif node.type == "export_statement":
            _js_reexport(node, refs)
            _js_default_export(node, refs)
            pending_local_exports.extend(_js_local_export_specifiers(node))
        elif node.type == "assignment_expression":
            _js_commonjs_export(node, refs)
        elif node.type == "variable_declarator":
            _js_require(node, refs)
        elif node.type == "call_expression":
            _js_callee(node.child_by_field_name("function"), line, refs)
            # `import('./x')` / `require('./x')` anywhere (`lazy: () => import('./x').then(...)`), not only as a declarator's value
            spec = _dynamic_import_spec(node) or _require_spec(node)
            if spec is not None and not any(i.spec == spec and i.line == line for i in refs.imports):
                refs.imports.append(ImportRef(spec, 0, [], line, _js_function_scope(node)))
        elif node.type == "new_expression":
            _js_callee(node.child_by_field_name("constructor"), line, refs)
        elif node.type in ("jsx_opening_element", "jsx_self_closing_element"):
            _js_jsx(node, refs)
        elif node.type == "class_declaration":
            _js_bases(node, refs)
        stack.extend(reversed(node.children))

    # `import { a } from './m'; export { a }` re-exports `a` exactly like `export { a } from './m'` (a barrel written in two steps)
    by_local = {b.local: (imp.spec, b.member) for imp in refs.imports for b in imp.bindings if b.member not in (None, "default")}
    for local, exported in pending_local_exports:
        if local in by_local:
            spec, member = by_local[local]
            refs.reexports.append(ReExport(spec, [(member, exported)]))

    refs.value_refs = javascript_value_refs(tree.root_node)
    return refs
