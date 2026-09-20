"""Value references: a function used without being called (`useReducer(fn)`, `component={Screen}`, `.map(render)`).

Recorded as CallRef(name, receiver, line), like calls, but resolved conservatively into REFERENCES
edges (imports and same-module names only — never the loose unique-name fallback). To avoid linking
a *variable* to a same-named function, names bound locally (parameters, assignments, loop and catch
variables, nested defs) in any enclosing function are skipped.
"""

from __future__ import annotations

from tree_sitter import Node

from ibwd.scanner.references import UNKNOWN_RECEIVER, CallRef, clean_receiver


def _text(node: Node) -> str:
    return node.text.decode("utf-8")


def _field_of(node: Node) -> str | None:
    parent = node.parent
    if parent is None:
        return None
    for index, child in enumerate(parent.children):
        if child == node:
            return parent.field_name_for_child(index)
    return None


def _is_simple_dotted(node: Node, member_type: str, object_field: str, leaf_types: tuple[str, ...]) -> bool:
    """`a`, `a.b`, `self.x`, `this.x.y` — an attribute chain rooted at a plain name."""
    while node.type == member_type:
        node = node.child_by_field_name(object_field)
        if node is None:
            return False
    return node.type in leaf_types


# --------------------------------------------------------------------------- Python

_PY_FUNCTION_SCOPES = ("function_definition", "lambda")
_PY_PARAM_PARENTS = {
    "parameters", "lambda_parameters", "default_parameter", "typed_parameter",
    "typed_default_parameter", "list_splat_pattern", "dictionary_splat_pattern",
}
_PY_SKIP_SUBTREES = {"import_statement", "import_from_statement", "global_statement", "nonlocal_statement", "type"}
_PY_PATTERN_TYPES = ("pattern_list", "tuple_pattern", "list_pattern", "list_splat_pattern", "as_pattern_target")


def _py_pattern_names(node: Node | None, out: set[str]) -> None:
    if node is None:
        return
    if node.type == "identifier":
        out.add(_text(node))
    elif node.type in _PY_PATTERN_TYPES or node.type == "expression_list":
        for child in node.children:
            _py_pattern_names(child, out)


def _py_locals(fn: Node) -> set[str]:
    names: set[str] = set()
    params = fn.child_by_field_name("parameters")
    if params is not None:
        for child in params.children:
            if child.type == "identifier":
                names.add(_text(child))
            elif child.type in ("default_parameter", "typed_default_parameter"):
                _py_pattern_names(child.child_by_field_name("name"), names)
            elif child.type == "typed_parameter":
                for sub in child.children:
                    if sub.type == "identifier":
                        names.add(_text(sub))
                        break
                    if sub.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                        _py_pattern_names(sub, names)
                        break
            elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                _py_pattern_names(child, names)
    stack = list(fn.children)
    while stack:
        node = stack.pop()
        t = node.type
        if t in ("assignment", "augmented_assignment", "for_statement", "for_in_clause"):
            _py_pattern_names(node.child_by_field_name("left"), names)
        elif t == "named_expression":
            _py_pattern_names(node.child_by_field_name("name"), names)
        elif t == "as_pattern":
            for child in node.children:
                if child.type == "as_pattern_target":
                    _py_pattern_names(child, names)
        elif t in ("function_definition", "class_definition"):
            name = node.child_by_field_name("name")
            if name is not None:
                names.add(_text(name))
        # (function-local imports are NOT locals here: they bind to a repo symbol, resolved through the import bindings)
        stack.extend(node.children)
    return names


def _param_names(fn: Node, names: set[str]) -> None:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return
    for child in params.children:
        if child.type == "identifier":
            names.add(_text(child))
        elif child.type in ("default_parameter", "typed_default_parameter"):
            _py_pattern_names(child.child_by_field_name("name"), names)
        elif child.type == "typed_parameter":
            for sub in child.children:
                if sub.type == "identifier":
                    names.add(_text(sub))
                    break
                if sub.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                    _py_pattern_names(sub, names)
                    break
        elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
            _py_pattern_names(child, names)


_COMPREHENSIONS = ("list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression")


def python_local_names(root: Node) -> list[tuple[str, int, int]]:
    """(name, first_line, last_line): names a function (or comprehension/lambda) binds itself.

    Python scoping: a name bound anywhere in a function (parameter, assignment, for/with/except target, walrus, nested
    def/class) is local to the *whole* body, so it shadows a same-named module-level function or import there — a call
    through it goes to whatever the variable holds, never provably to the module-level symbol. Imports are handled by the
    import scope, `global`/`nonlocal` names are not local, and default values/annotations in the signature are evaluated
    in the enclosing scope (the scope starts at the body).
    """
    out: list[tuple[str, int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type in _COMPREHENSIONS:
            names: set[str] = set()
            for child in node.children:
                if child.type == "for_in_clause":
                    _py_pattern_names(child.child_by_field_name("left"), names)
            out.extend((n, node.start_point.row + 1, node.end_point.row + 1) for n in names)
            continue
        if node.type not in ("function_definition", "lambda"):
            continue
        names = set()
        _param_names(node, names) if node.type == "function_definition" else _lambda_params(node, names)
        body = node.child_by_field_name("body")
        excluded: set[str] = set()
        walk = list(body.children) if body is not None and node.type == "function_definition" else []
        while walk:
            n = walk.pop()
            t = n.type
            if t in ("function_definition", "class_definition"):
                name = n.child_by_field_name("name")
                if name is not None:
                    names.add(_text(name))
                continue                      # a nested scope's own bindings are not this function's
            if t == "lambda" or t in _COMPREHENSIONS:
                continue
            if t in ("assignment", "augmented_assignment", "for_statement"):
                _py_pattern_names(n.child_by_field_name("left"), names)
            elif t == "named_expression":
                _py_pattern_names(n.child_by_field_name("name"), names)
            elif t == "as_pattern":
                for child in n.children:
                    if child.type == "as_pattern_target":
                        _py_pattern_names(child, names)
            elif t in ("global_statement", "nonlocal_statement"):
                excluded.update(_text(c) for c in n.children if c.type == "identifier")
            walk.extend(n.children)
        names -= excluded
        start = body.start_point.row + 1 if body is not None else node.start_point.row + 1
        out.extend((n, start, node.end_point.row + 1) for n in names)
    return out


def _lambda_params(node: Node, names: set[str]) -> None:
    params = node.child_by_field_name("parameters")
    if params is not None:
        for child in params.children:
            if child.type == "identifier":
                names.add(_text(child))
            elif child.type in ("default_parameter", "typed_default_parameter"):
                _py_pattern_names(child.child_by_field_name("name"), names)
            elif child.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                _py_pattern_names(child, names)


def _in_class_base_subscript(node: Node) -> bool:
    """Inside a generic base such as `class C(ObjectDescription[Arg])`: the head is the inheritance edge and the arguments are
    type expressions, not runtime uses of a function/class."""
    p = node.parent
    while p is not None:
        if p.type == "subscript":
            grand = p.parent
            if grand is not None and grand.type == "argument_list" and grand.parent is not None and grand.parent.type == "class_definition":
                return True
        elif p.type in ("call", "block", "module", "function_definition", "lambda", "argument_list", "keyword_argument"):
            return False
        p = p.parent
    return False


def _py_is_value_use(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    if _in_class_base_subscript(node):
        return False
    t, f = parent.type, _field_of(node)
    if t in ("function_definition", "class_definition") and f == "name":
        return False
    if t == "call" and f == "function":
        return False
    if t == "list_splat" and parent.parent is not None and (
        (parent.parent.type == "call" and _field_of(parent) == "function")
        or (parent.parent.type == "attribute" and _field_of(parent) == "object")
    ):
        return False  # `[*g(x)]` / `[*self.f(x)]` misparsed as (*g)(x) / (*self).f(x): part of a callee, not a value
    if t == "keyword_argument" and f == "name":
        return False
    if t in _PY_PARAM_PARENTS and f != "value":
        return False
    if t in ("assignment", "augmented_assignment") and f in ("left", "type"):
        return False
    if t == "delete_statement":  # `del name` unbinds it; it is not a use
        return False
    if t in ("for_statement", "for_in_clause") and f == "left":
        return False
    if t in _PY_PATTERN_TYPES or (t == "as_pattern" and f == "alias"):
        return False
    if t == "named_expression" and f == "name":
        return False
    if t == "attribute":
        # the `.attr` part is never a value on its own; the head of a chain (`Cls` in `Cls.CONST`, `logtool` in `logtool.command`)
        # is a use of that name — a class or function used as a namespace
        return f == "object" and _text(node) not in ("self", "cls", "super")
    if t == "argument_list" and parent.parent is not None and parent.parent.type == "class_definition":
        return False  # base classes
    return True


def python_value_refs(root: Node) -> list[CallRef]:
    out: list[CallRef] = []
    locals_cache: dict[int, set[str]] = {}
    stack: list[tuple[Node, tuple[set[str], ...]]] = [(root, ())]
    while stack:
        node, scopes = stack.pop()
        t = node.type
        if t in _PY_SKIP_SUBTREES:
            continue
        if t in _PY_FUNCTION_SCOPES:
            key = node.id
            if key not in locals_cache:
                locals_cache[key] = _py_locals(node)
            inner = (*scopes, locals_cache[key])
            for child in reversed(node.children):
                # `def f(cb=cb)`: the default value is evaluated in the enclosing scope, so it is not shadowed by the parameter
                signature = child.type in ("parameters", "lambda_parameters", "return_type")
                stack.append((child, scopes if signature else inner))
            continue
        if t == "identifier" and _py_is_value_use(node):
            name = _text(node)
            if not any(name in scope for scope in scopes):
                out.append(CallRef(name, None, node.start_point.row + 1))
            continue
        if t == "attribute" and node.parent is not None and node.parent.type == "attribute" \
                and _field_of(node) == "object" and not _in_class_base_subscript(node):
            # the inner part of a longer chain: `self.client.delete(key)` reads the attribute/property `client` of `self`
            obj, attr = node.child_by_field_name("object"), node.child_by_field_name("attribute")
            if obj is not None and attr is not None and obj.type == "identifier":
                out.append(CallRef(_text(attr), _text(obj), node.start_point.row + 1))
        if t == "attribute" and _py_is_value_use_attribute(node):
            obj0 = node.child_by_field_name("object")
            attr0 = node.child_by_field_name("attribute")
            if obj0 is not None and attr0 is not None and obj0.type == "call":
                fn0 = obj0.child_by_field_name("function")
                if fn0 is not None and fn0.type == "identifier" and _text(fn0) == "super":
                    out.append(CallRef(_text(attr0), "super", node.start_point.row + 1))    # `func = super().method`
                    continue
            if _is_simple_dotted(node, "attribute", "object", ("identifier",)):
                receiver = clean_receiver(_text(node.child_by_field_name("object")))
                attr = node.child_by_field_name("attribute")
                if receiver != UNKNOWN_RECEIVER and attr is not None:
                    out.append(CallRef(_text(attr), receiver, node.start_point.row + 1))
                    head = receiver.split(".")[0]
                    if "." in receiver:                      # `self.a.b` also reads `self.a`
                        out.append(CallRef(receiver.split(".")[1], head, node.start_point.row + 1))
                    if head not in ("self", "cls", "super") and not any(head in scope for scope in scopes):
                        out.append(CallRef(head, None, node.start_point.row + 1))   # the class/function used as a namespace
                continue  # don't visit the chain's inner names
        for child in reversed(node.children):
            stack.append((child, scopes))
    return out


def _py_is_value_use_attribute(node: Node) -> bool:
    """Is this attribute chain (`mod.fn`, `self.handler`) used as a value, from its top-most node?"""
    parent = node.parent
    if parent is None or parent.type == "attribute":  # inner part of a longer chain
        return False
    if _in_class_base_subscript(node):
        return False
    t, f = parent.type, _field_of(node)
    if t == "call" and f == "function":
        return False
    if t in ("assignment", "augmented_assignment") and f in ("left", "type"):
        return False
    if t in ("for_statement", "for_in_clause") and f == "left":
        return False
    if t in _PY_PATTERN_TYPES or (t == "as_pattern" and f == "alias"):
        return False
    if t == "keyword_argument" and f == "name":
        return False
    if t == "argument_list" and parent.parent is not None and parent.parent.type == "class_definition":
        return False
    return True


# --------------------------------------------------------------------------- JavaScript / TypeScript

_JS_FUNCTION_SCOPES = (
    "function_declaration", "function_expression", "generator_function_declaration",
    "generator_function", "arrow_function", "method_definition",
)
# `typeof fn` inside a type (type_query) names the function only as a type, never at runtime.
_JS_SKIP_SUBTREES = {"import_statement", "export_specifier", "export_clause", "type_query"}
_JS_PATTERN_LEAVES = ("identifier", "shorthand_property_identifier_pattern")


def _js_pattern_names(node: Node | None, out: set[str]) -> None:
    if node is None:
        return
    t = node.type
    if t in _JS_PATTERN_LEAVES:
        out.add(_text(node))
    elif t == "pair_pattern":
        _js_pattern_names(node.child_by_field_name("value"), out)
    elif t == "assignment_pattern":
        _js_pattern_names(node.child_by_field_name("left"), out)
    elif t in ("required_parameter", "optional_parameter"):
        _js_pattern_names(node.child_by_field_name("pattern"), out)
    elif t in ("object_pattern", "array_pattern", "rest_pattern", "formal_parameters"):
        for child in node.children:
            _js_pattern_names(child, out)


def _js_locals(fn: Node) -> set[str]:
    names: set[str] = set()
    _js_pattern_names(fn.child_by_field_name("parameters"), names)
    _js_pattern_names(fn.child_by_field_name("parameter"), names)  # arrow fn with a single bare param
    stack = list(fn.children)
    while stack:
        node = stack.pop()
        t = node.type
        if t == "variable_declarator":
            _js_pattern_names(node.child_by_field_name("name"), names)
        elif t == "catch_clause":
            _js_pattern_names(node.child_by_field_name("parameter"), names)
        elif t == "for_in_statement":
            _js_pattern_names(node.child_by_field_name("left"), names)
        elif t in ("function_declaration", "generator_function_declaration", "class_declaration"):
            name = node.child_by_field_name("name")
            if name is not None:
                names.add(_text(name))
        stack.extend(node.children)
    return names


def _js_is_value_use(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    t, f = parent.type, _field_of(node)
    if t in ("function_declaration", "function_expression", "generator_function_declaration", "class_declaration") and f == "name":
        return False
    if t == "call_expression" and f == "function":
        return False
    if t == "new_expression" and f == "constructor":
        return False
    if t in ("jsx_opening_element", "jsx_closing_element", "jsx_self_closing_element") and f == "name":
        return False
    if t == "variable_declarator" and f == "name":
        return False
    if t in ("formal_parameters", "required_parameter", "optional_parameter", "rest_pattern", "object_pattern", "array_pattern"):
        return False
    if t == "assignment_pattern" and f == "left":
        return False
    if t == "pair_pattern" and f == "value":
        return False
    if t in ("assignment_expression", "augmented_assignment_expression") and f == "left":
        return False
    if t in ("update_expression", "namespace_import", "import_clause", "import_specifier", "class_heritage", "extends_clause", "export_statement"):
        return False
    if t == "for_in_statement" and f == "left":
        return False
    if t == "catch_clause" and f == "parameter":
        return False
    if t == "arrow_function" and f == "parameter":
        return False
    if t == "member_expression":  # the object of a chain: the whole chain is emitted from its top
        return False
    return True


def _js_is_value_use_member(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return False
    t, f = parent.type, _field_of(node)
    if t == "member_expression":
        return False
    if t == "call_expression" and f == "function":
        return False
    if t in ("assignment_expression", "augmented_assignment_expression") and f == "left":
        return False
    if t in ("update_expression", "export_statement", "class_heritage", "extends_clause"):
        return False
    if t == "new_expression" and f == "constructor":
        return False
    if t in ("jsx_opening_element", "jsx_closing_element", "jsx_self_closing_element") and f == "name":
        return False
    if t == "for_in_statement" and f == "left":
        return False
    return True


def javascript_value_refs(root: Node) -> list[CallRef]:
    out: list[CallRef] = []
    locals_cache: dict[int, set[str]] = {}
    stack: list[tuple[Node, tuple[set[str], ...]]] = [(root, ())]
    while stack:
        node, scopes = stack.pop()
        t = node.type
        if t in _JS_SKIP_SUBTREES:
            continue
        if t in _JS_FUNCTION_SCOPES:
            key = node.id
            if key not in locals_cache:
                locals_cache[key] = _js_locals(node)
            scopes = (*scopes, locals_cache[key])
        if t in ("identifier", "shorthand_property_identifier"):
            if _js_is_value_use(node):
                name = _text(node)
                if not any(name in scope for scope in scopes):
                    out.append(CallRef(name, None, node.start_point.row + 1))
            continue
        if t == "member_expression" and _js_is_value_use_member(node):
            if _is_simple_dotted(node, "member_expression", "object", ("identifier", "this")):
                receiver = clean_receiver(_text(node.child_by_field_name("object")))
                prop = node.child_by_field_name("property")
                if receiver != UNKNOWN_RECEIVER and prop is not None:
                    out.append(CallRef(_text(prop), receiver, node.start_point.row + 1))
                continue
        for child in reversed(node.children):
            stack.append((child, scopes))
    return out
