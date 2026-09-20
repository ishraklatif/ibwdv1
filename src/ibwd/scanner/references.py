"""Shared reference types (imports / calls / base classes) + language dispatch.

Sprint 3 extracts *unresolved* references per file with tree-sitter; turning
them into graph edges (IMPORTS / CALLS / INHERITS / REFERENCES) is graph/resolution.py's job.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ibwd.scanner.symbols import EXTENSION_DIALECTS

# Receiver text we can't reason about (`get().foo()`, `a[0].foo()`, ...).
UNKNOWN_RECEIVER = "?"

_SIMPLE_RECEIVER = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")


def clean_receiver(text: str) -> str:
    """Keep plain dotted receivers (`self`, `mod.sub`); collapse anything else."""
    return text if _SIMPLE_RECEIVER.match(text) else UNKNOWN_RECEIVER


@dataclass
class Binding:
    """A local name introduced by an import.

    member=None means `local` names the imported module itself (`import a.b as c`,
    `import * as ns`); otherwise `local` names `member` inside the module
    (`from m import member as local`, JS default imports use member="default").
    """

    local: str
    member: str | None = None


@dataclass
class ImportRef:
    spec: str  # Python: dotted module without leading dots; JS: raw specifier ('./x')
    level: int = 0  # Python relative-import depth (0 = absolute)
    bindings: list[Binding] = field(default_factory=list)
    line: int = 0
    # (start_line, end_line) of the function this import sits in, or None for a file-level import.
    # A function-local import only binds names inside that function.
    scope: tuple[int, int] | None = None


@dataclass
class ReExport:
    """`export * from './x'` (names=None) or `export { a, b as c } from './x'` (names = [(imported, exported)])."""

    spec: str
    names: list[tuple[str, str]] | None = None


@dataclass
class CallRef:
    name: str
    receiver: str | None  # None for a bare `foo()`; "self"/"mod.sub"/UNKNOWN_RECEIVER otherwise
    line: int


@dataclass
class BaseRef:
    class_name: str
    class_line: int
    name: str
    receiver: str | None


@dataclass
class FileReferences:
    imports: list[ImportRef] = field(default_factory=list)
    calls: list[CallRef] = field(default_factory=list)
    bases: list[BaseRef] = field(default_factory=list)
    # A function used without being called (`useReducer(fn)`, `component={Screen}`): see valuerefs.py.
    value_refs: list[CallRef] = field(default_factory=list)
    reexports: list[ReExport] = field(default_factory=list)
    # Every definition's (qualname, start_line, end_line), *including* repeated qualnames (property getter/setter pairs,
    # typing.overload stubs). The graph keeps one node per qualname, so this is what attributes a call to its owner.
    def_ranges: list[tuple[str, int, int]] = field(default_factory=list)
    # Python: (name, first_line, last_line) of names a function/comprehension binds itself (parameters, assigned variables,
    # nested defs): they shadow same-named module-level symbols and imports on those lines. See valuerefs.python_local_names.
    local_names: list[tuple[str, int, int]] = field(default_factory=list)
    # JS/TS: name of the symbol this file exports by default (`export default Foo`), if nameable.
    default_export: str | None = None


def refs_to_json(refs: FileReferences) -> str:
    return json.dumps(asdict(refs), separators=(",", ":"))


def refs_from_json(text: str) -> FileReferences:
    data = json.loads(text)
    return FileReferences(
        imports=[
            ImportRef(
                i["spec"], i["level"], [Binding(b["local"], b["member"]) for b in i["bindings"]], i["line"],
                tuple(i["scope"]) if i.get("scope") else None,
            )
            for i in data["imports"]
        ],
        calls=[CallRef(c["name"], c["receiver"], c["line"]) for c in data["calls"]],
        bases=[BaseRef(b["class_name"], b["class_line"], b["name"], b["receiver"]) for b in data["bases"]],
        value_refs=[CallRef(c["name"], c["receiver"], c["line"]) for c in data.get("value_refs", [])],
        reexports=[
            ReExport(r["spec"], [tuple(n) for n in r["names"]] if r["names"] is not None else None)
            for r in data.get("reexports", [])
        ],
        def_ranges=[(q, a, b) for q, a, b in data.get("def_ranges", [])],
        local_names=[(n, a, b) for n, a, b in data.get("local_names", [])],
        default_export=data.get("default_export"),
    )


def extract_references(abs_path: Path, file_path: str) -> FileReferences | None:
    """Extract imports/calls/base classes from a file, if its language is supported."""
    dialect = EXTENSION_DIALECTS.get(Path(file_path).suffix.lower())
    if dialect is None:
        return None

    try:
        source = abs_path.read_bytes()
    except OSError:
        return None

    from ibwd.scanner.symbols import extract_symbols_from_source

    if dialect == "python":
        from ibwd.scanner.python import extract_python_references

        refs = extract_python_references(source)
    else:
        from ibwd.scanner.javascript import extract_javascript_references

        refs = extract_javascript_references(source, dialect=dialect)
    refs.def_ranges = [
        (sym.qualified_name.split("::", 1)[-1], sym.start_line, sym.end_line)
        for sym in extract_symbols_from_source(source, file_path, dialect)
    ]
    return refs
