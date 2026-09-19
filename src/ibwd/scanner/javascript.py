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
    clean_receiver,
)
from ibwd.scanner.symbols import SymbolInfo

_LANGUAGES = {
    "javascript": Language(tsjavascript.language()),
    "typescript": Language(tstypescript.language_typescript()),
    "tsx": Language(tstypescript.language_tsx()),
}

_FUNCTION_DECLARATION_TYPES = {"function_declaration", "generator_function_declaration"}
_FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression", "generator_function"}


def _text(node: Node) -> str:
    return node.text.decode("utf-8")


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
                value = child.child_by_field_name("value")
                if value is not None and value.type in _FUNCTION_VALUE_TYPES:
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        kind = "Method" if class_stack else "Function"
                        add(child, _text(name_node), kind, class_stack)
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


def _js_reexport(node: Node, refs: FileReferences) -> None:
    """`export { a } from './m'` / `export * from './m'` — a file->file import with no local bindings."""
    source = node.child_by_field_name("source")
    if source is not None:
        refs.imports.append(ImportRef(_string_value(source), 0, [], node.start_point.row + 1))


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


def _js_require(node: Node, refs: FileReferences) -> None:
    """`const x = require('./m')` / `const { a, b: c } = require('./m')`."""
    spec = _require_spec(node.child_by_field_name("value"))
    target = node.child_by_field_name("name")
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
    refs.imports.append(ImportRef(spec, 0, bindings, node.start_point.row + 1))


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

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        line = node.start_point.row + 1
        if node.type == "import_statement":
            _js_import_statement(node, refs)
        elif node.type == "export_statement":
            _js_reexport(node, refs)
        elif node.type == "variable_declarator":
            _js_require(node, refs)
        elif node.type == "call_expression":
            _js_callee(node.child_by_field_name("function"), line, refs)
        elif node.type == "new_expression":
            _js_callee(node.child_by_field_name("constructor"), line, refs)
        elif node.type == "class_declaration":
            _js_bases(node, refs)
        stack.extend(reversed(node.children))

    return refs
