"""JS/JSX/TS/TSX Class/Function/Method extraction via tree-sitter."""

from __future__ import annotations

import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

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
