"""Resolve an import specifier to a repo file path (None = external / not found)."""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from pathlib import PurePosixPath

from ibwd.scanner.symbols import EXTENSION_DIALECTS

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts")
# `import './x.js'` in TS source usually means ./x.ts — try the source twin.
_JS_EXT_TWINS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
# Non-root directories treated as Python import roots besides the importer's own ancestors.
PYTHON_SOURCE_ROOTS = ("src", "lib")

# Confidence for a module resolved by exact/relative path vs. an inferred import root.
CONF_EXACT = 1.0
CONF_INFERRED_ROOT = 0.9


def language_of(file_path: str) -> str | None:
    dialect = EXTENSION_DIALECTS.get(PurePosixPath(file_path).suffix.lower())
    if dialect is None:
        return None
    return "python" if dialect == "python" else "javascript"


class ModuleResolver:
    def __init__(self, paths: Iterable[str]):
        self.paths = set(paths)

    def resolve(self, spec: str, level: int, importer: str) -> tuple[str | None, float]:
        """Return (repo file path or None, confidence) for an import in `importer`."""
        if language_of(importer) == "python":
            return self._resolve_python(spec, level, importer)
        return self._resolve_javascript(spec, importer)

    # -- Python ---------------------------------------------------------

    def _python_at(self, base: PurePosixPath, parts: list[str]) -> str | None:
        """`base/a/b.py` or `base/a/b/__init__.py` (or `base/__init__.py` for no parts)."""
        target = base.joinpath(*parts) if parts else base
        for candidate in ([f"{target}.py"] if parts else []) + [str(target / "__init__.py")]:
            candidate = candidate.removeprefix("./")
            if candidate in self.paths:
                return candidate
        return None

    def _resolve_python(self, spec: str, level: int, importer: str) -> tuple[str | None, float]:
        parts = spec.split(".") if spec else []
        importer_dir = PurePosixPath(importer).parent

        if level > 0:
            base = importer_dir
            for _ in range(level - 1):
                base = base.parent
            return self._python_at(base, parts), CONF_EXACT

        # Absolute: repo root first, then each ancestor directory of the
        # importer (script-style sibling imports, monorepo sub-packages), then
        # conventional source roots (src/, lib/ — e.g. tests importing the package).
        roots: list[PurePosixPath] = [PurePosixPath(".")]
        for ancestor in reversed(importer_dir.parents):
            if str(ancestor) != ".":
                roots.append(ancestor)
        if str(importer_dir) != ".":
            roots.append(importer_dir)
        roots.extend(PurePosixPath(root) for root in PYTHON_SOURCE_ROOTS)

        for root in roots:
            found = self._python_at(root, parts)
            if found:
                return found, CONF_EXACT if str(root) == "." else CONF_INFERRED_ROOT
        return None, 0.0

    # -- JavaScript / TypeScript ---------------------------------------

    def _resolve_javascript(self, spec: str, importer: str) -> tuple[str | None, float]:
        if not spec.startswith("."):
            return None, 0.0  # bare package / alias: external as far as we can tell

        joined = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        if joined.startswith(".."):
            return None, 0.0

        candidates = [joined]
        suffix = PurePosixPath(joined).suffix
        stem = joined[: -len(suffix)] if suffix else joined
        for twin in _JS_EXT_TWINS.get(suffix, ()):
            candidates.append(stem + twin)
        candidates.extend(joined + ext for ext in JS_EXTENSIONS)
        candidates.extend(f"{joined}/index{ext}" for ext in JS_EXTENSIONS)

        for candidate in candidates:
            if candidate in self.paths:
                return candidate, CONF_EXACT
        return None, 0.0
