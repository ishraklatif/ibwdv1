"""Python Class/Function/Method extraction via tree-sitter."""

from __future__ import annotations

import tree_sitter_python as tspython
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
from ibwd.scanner.valuerefs import python_value_refs

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


def _text(node: Node) -> str:
    return node.text.decode("utf-8")


def _enclosing_function_scope(node: Node) -> tuple[int, int] | None:
    """Line range of the function a statement is nested in; None at module/class level."""
    parent = node.parent
    while parent is not None:
        if parent.type == "function_definition":
            return parent.start_point.row + 1, parent.end_point.row + 1
        parent = parent.parent
    return None


def _python_import(node: Node, refs: FileReferences) -> None:
    line = node.start_point.row + 1
    scope = _enclosing_function_scope(node)
    for child in node.children_by_field_name("name"):
        if child.type == "aliased_import":
            module = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if module is None:
                continue
            local = _text(alias) if alias else _text(module)
            refs.imports.append(ImportRef(_text(module), 0, [Binding(local)], line, scope))
        elif child.type == "dotted_name":
            # `import a.b.c` binds the dotted path itself, so a later
            # `a.b.c.func()` receiver matches this binding verbatim.
            refs.imports.append(ImportRef(_text(child), 0, [Binding(_text(child))], line, scope))


def _python_import_from(node: Node, refs: FileReferences) -> None:
    module_node = node.child_by_field_name("module_name")
    if module_node is None:
        return

    level = 0
    spec = ""
    if module_node.type == "relative_import":
        for child in module_node.children:
            if child.type == "import_prefix":
                level = len(child.text)
            elif child.type == "dotted_name":
                spec = _text(child)
    else:
        spec = _text(module_node)

    bindings: list[Binding] = []
    for child in node.children_by_field_name("name"):
        if child.type == "aliased_import":
            member = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if member is not None:
                bindings.append(Binding(_text(alias) if alias else _text(member), _text(member)))
        elif child.type == "dotted_name":
            bindings.append(Binding(_text(child), _text(child)))
    # `from m import *` has no bindings but is still a file->file import.
    refs.imports.append(ImportRef(spec, level, bindings, node.start_point.row + 1, _enclosing_function_scope(node)))


def _python_call(node: Node, refs: FileReferences) -> None:
    function = node.child_by_field_name("function")
    if function is None:
        return
    line = node.start_point.row + 1
    # tree-sitter-python quirk: `[*g(x)]` (a lone starred list element) parses as call(list_splat(g), (x)),
    # i.e. `(*g)(x)`. Treat the splat's operand as the callee.
    if function.type == "list_splat" and function.named_child_count == 1:
        function = function.named_children[0]
    if function.type == "identifier":
        refs.calls.append(CallRef(_text(function), None, line))
    elif function.type == "attribute":
        obj = function.child_by_field_name("object")
        attr = function.child_by_field_name("attribute")
        if obj is not None and attr is not None:
            refs.calls.append(CallRef(_text(attr), _python_receiver(obj), line))


def _python_receiver(obj: Node) -> str:
    """`super()` / `super(Cls, self)` -> "super"; otherwise the usual cleaned receiver text."""
    if obj.type == "list_splat" and obj.named_child_count == 1:  # `[*self.f(x)]` misparsed as (*self).f(x)
        obj = obj.named_children[0]
    if obj.type == "call":
        function = obj.child_by_field_name("function")
        if function is not None and function.type == "identifier" and _text(function) == "super":
            return "super"
    return clean_receiver(_text(obj))


def _python_bases(node: Node, refs: FileReferences) -> None:
    name_node = node.child_by_field_name("name")
    supers = node.child_by_field_name("superclasses")
    if name_node is None or supers is None:
        return
    class_name = _text(name_node)
    class_line = node.start_point.row + 1
    for base in supers.children:
        if base.type == "subscript":  # a generic base: `ObjectDescription[ASTDeclaration]` -> `ObjectDescription`
            inner = base.child_by_field_name("value")
            if inner is not None and inner.type in ("identifier", "attribute"):
                base = inner
        if base.type == "identifier":
            refs.bases.append(BaseRef(class_name, class_line, _text(base), None))
        elif base.type == "attribute":
            obj = base.child_by_field_name("object")
            attr = base.child_by_field_name("attribute")
            if obj is not None and attr is not None:
                refs.bases.append(BaseRef(class_name, class_line, _text(attr), clean_receiver(_text(obj))))


def extract_python_references(source: bytes) -> FileReferences:
    """Extract imports, call sites and base classes (unresolved) from Python source."""
    parser = Parser(_LANGUAGE)  # keep a reference: a temporary Parser crashes Node.text
    tree = parser.parse(source)
    refs = FileReferences()

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "import_statement":
            _python_import(node, refs)
        elif node.type == "import_from_statement":
            _python_import_from(node, refs)
        elif node.type == "call":
            _python_call(node, refs)
        elif node.type == "class_definition":
            _python_bases(node, refs)
        stack.extend(reversed(node.children))

    refs.value_refs = python_value_refs(tree.root_node)
    return refs
