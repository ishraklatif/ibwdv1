"""Python Class/Function/Method extraction via tree-sitter."""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from ibwd.scanner.symbols import SymbolInfo

_LANGUAGE = Language(tspython.language())


def _unwrap_decorated(node: Node) -> Node:
    """A decorated def/class is wrapped in `decorated_definition` — unwrap it."""
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return child
    return node


def _name_of(node: Node) -> str | None:
    name_node = node.child_by_field_name("name")
    return name_node.text.decode("utf-8") if name_node else None


def extract_python_symbols(source: bytes, file_path: str) -> list[SymbolInfo]:
    parser = Parser(_LANGUAGE)
    tree = parser.parse(source)
    symbols: list[SymbolInfo] = []

    def visit(node: Node, class_stack: list[str]) -> None:
        for outer in node.children:
            defn = _unwrap_decorated(outer)
            start_line = outer.start_point.row + 1
            end_line = outer.end_point.row + 1

            if defn.type == "class_definition":
                name = _name_of(defn)
                if name:
                    qualified = ".".join([*class_stack, name])
                    symbols.append(
                        SymbolInfo(
                            name=name,
                            kind="Class",
                            qualified_name=f"{file_path}::{qualified}",
                            file_path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    )
                    body = defn.child_by_field_name("body")
                    if body:
                        visit(body, [*class_stack, name])
                continue

            if defn.type == "function_definition":
                name = _name_of(defn)
                if name:
                    qualified = ".".join([*class_stack, name])
                    symbols.append(
                        SymbolInfo(
                            name=name,
                            kind="Method" if class_stack else "Function",
                            qualified_name=f"{file_path}::{qualified}",
                            file_path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                        )
                    )
                # Nested/closure functions inside a function body are out of
                # scope for Sprint 2 — don't recurse into it.
                continue

            # Recurse through other containers (module top level, if/try
            # blocks, decorated_definition already unwrapped above) so
            # conditionally-defined top-level functions/classes are found.
            visit(outer, class_stack)

    visit(tree.root_node, [])
    return symbols
