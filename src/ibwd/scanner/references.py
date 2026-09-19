"""Shared reference types (imports / calls / base classes) + language dispatch.

Sprint 3 extracts *unresolved* references per file with tree-sitter; turning
them into graph edges (IMPORTS / CALLS / INHERITS) is graph/resolution.py's job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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


def extract_references(abs_path: Path, file_path: str) -> FileReferences | None:
    """Extract imports/calls/base classes from a file, if its language is supported."""
    dialect = EXTENSION_DIALECTS.get(Path(file_path).suffix.lower())
    if dialect is None:
        return None

    try:
        source = abs_path.read_bytes()
    except OSError:
        return None

    if dialect == "python":
        from ibwd.scanner.python import extract_python_references

        return extract_python_references(source)

    from ibwd.scanner.javascript import extract_javascript_references

    return extract_javascript_references(source, dialect=dialect)
