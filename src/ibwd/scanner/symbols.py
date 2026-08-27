"""Shared symbol type + language dispatch for tree-sitter symbol extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EXTENSION_DIALECTS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".tsx": "tsx",
}


@dataclass
class SymbolInfo:
    name: str
    kind: str  # Class | Function | Method
    qualified_name: str  # "{file_path}::{Outer.Inner}"
    file_path: str
    start_line: int
    end_line: int


def extract_symbols(abs_path: Path, file_path: str) -> list[SymbolInfo]:
    """Extract Class/Function/Method symbols from a file, if its language is supported.

    Returns an empty list for unsupported extensions (e.g. Go, Rust) — Sprint 2
    only covers Python and JS/TS/TSX per the execution plan.
    """
    dialect = EXTENSION_DIALECTS.get(Path(file_path).suffix.lower())
    if dialect is None:
        return []

    try:
        source = abs_path.read_bytes()
    except OSError:
        return []

    if dialect == "python":
        from ibwd.scanner.python import extract_python_symbols

        return extract_python_symbols(source, file_path)

    from ibwd.scanner.javascript import extract_javascript_symbols

    return extract_javascript_symbols(source, file_path, dialect=dialect)
