"""Resolve an import specifier to a repo file path (None = external / not found)."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ibwd.scanner.symbols import EXTENSION_DIALECTS

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts")
# `import './x.js'` in TS source usually means ./x.ts — try the source twin.
_JS_EXT_TWINS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}
# Non-root directories treated as Python import roots besides the importer's own ancestors.
PYTHON_SOURCE_ROOTS = ("src", "lib")

# Confidence for a module resolved by exact/relative path vs. an inferred import root.
CONF_EXACT = 1.0
CONF_INFERRED_ROOT = 0.9


TS_CONFIG_NAMES = ("tsconfig.json", "jsconfig.json")
_MAX_EXTENDS_DEPTH = 3


def is_ts_config(file_path: str) -> bool:
    name = PurePosixPath(file_path).name
    return name in TS_CONFIG_NAMES or (name.startswith("tsconfig.") and name.endswith(".json"))


def _strip_jsonc(text: str) -> str:
    """Drop // and /* */ comments and trailing commas (tsconfig is JSONC), leaving strings intact."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


@dataclass
class AliasConfig:
    """Import aliases from the nearest tsconfig/jsconfig: `paths` patterns plus `baseUrl`."""

    base_dir: str | None = None  # repo-relative dir that non-relative specifiers/paths resolve against
    paths: dict[str, list[str]] = field(default_factory=dict)


def language_of(file_path: str) -> str | None:
    dialect = EXTENSION_DIALECTS.get(posixpath.splitext(file_path)[1].lower())
    if dialect is None:
        return None
    return "python" if dialect == "python" else "javascript"


class ModuleResolver:
    def __init__(self, paths: Iterable[str], repo_root: Path | None = None):
        self.paths = set(paths)
        self.repo_root = repo_root  # lets JS/TS resolution read tsconfig `paths` aliases
        self._alias_cache: dict[str, AliasConfig | None] = {}
        self._roots_cache: dict[str, list[str]] = {}

    def resolve(self, spec: str, level: int, importer: str) -> tuple[str | None, float]:
        """Return (repo file path or None, confidence) for an import in `importer`."""
        if language_of(importer) == "python":
            return self._resolve_python(spec, level, importer)
        return self._resolve_javascript(spec, importer)

    # -- Python ---------------------------------------------------------
    # Plain string paths ("" = repo root) — this runs once per import per scan, so pathlib
    # object churn was the dominant cost of an incremental rescan.

    def _python_at(self, base: str, parts: list[str]) -> str | None:
        """`base/a/b.py` or `base/a/b/__init__.py` (or `base/__init__.py` for no parts)."""
        target = "/".join(parts)
        prefix = f"{base}/" if base else ""
        if parts:
            candidate = f"{prefix}{target}.py"
            if candidate in self.paths:
                return candidate
            candidate = f"{prefix}{target}/__init__.py"
        else:
            candidate = f"{prefix}__init__.py"
        return candidate if candidate in self.paths else None

    def _python_roots(self, importer_dir: str) -> list[str]:
        """Absolute-import roots for an importer: repo root, each ancestor dir, then conventional source roots."""
        roots = self._roots_cache.get(importer_dir)
        if roots is None:
            roots = [""]
            if importer_dir:
                pieces = importer_dir.split("/")
                roots.extend("/".join(pieces[: n + 1]) for n in range(len(pieces)))
            roots.extend(PYTHON_SOURCE_ROOTS)
            self._roots_cache[importer_dir] = roots
        return roots

    def _resolve_python(self, spec: str, level: int, importer: str) -> tuple[str | None, float]:
        parts = spec.split(".") if spec else []
        importer_dir = posixpath.dirname(importer)

        if level > 0:
            base = importer_dir
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            return self._python_at(base, parts), CONF_EXACT

        # Absolute: repo root first, then each ancestor directory of the importer (script-style
        # sibling imports, monorepo sub-packages), then conventional source roots (src/, lib/ —
        # e.g. tests importing the package).
        for root in self._python_roots(importer_dir):
            found = self._python_at(root, parts)
            if found:
                return found, CONF_EXACT if root == "" else CONF_INFERRED_ROOT
        return None, 0.0

    # -- JavaScript / TypeScript ---------------------------------------

    def _javascript_file(self, joined: str) -> str | None:
        """A normalized repo path -> the real file (adding extensions / index files as needed)."""
        candidates = [joined]
        suffix = PurePosixPath(joined).suffix
        stem = joined[: -len(suffix)] if suffix else joined
        for twin in _JS_EXT_TWINS.get(suffix, ()):
            candidates.append(stem + twin)
        candidates.extend(joined + ext for ext in JS_EXTENSIONS)
        candidates.extend(f"{joined}/index{ext}" for ext in JS_EXTENSIONS)
        return next((c for c in candidates if c in self.paths), None)

    def _resolve_javascript(self, spec: str, importer: str) -> tuple[str | None, float]:
        if not spec.startswith("."):
            return self._resolve_alias(spec, importer)

        joined = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        if joined.startswith(".."):
            return None, 0.0
        found = self._javascript_file(joined)
        return (found, CONF_EXACT) if found else (None, 0.0)

    # -- tsconfig / jsconfig aliases -----------------------------------

    def _read_config(self, config_path: str, depth: int = 0) -> AliasConfig | None:
        if self.repo_root is None or depth > _MAX_EXTENDS_DEPTH:
            return None
        try:
            data = json.loads(_strip_jsonc((self.repo_root / config_path).read_text(errors="ignore")))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        config_dir = posixpath.dirname(config_path)

        # A local `extends` supplies defaults; package-name extends (e.g. "expo/tsconfig.base") are skipped.
        parent = None
        extends = data.get("extends")
        if isinstance(extends, str) and extends.startswith("."):
            parent_path = posixpath.normpath(posixpath.join(config_dir, extends))
            parent = self._read_config(parent_path if parent_path.endswith(".json") else parent_path + ".json", depth + 1)

        options = data.get("compilerOptions") if isinstance(data.get("compilerOptions"), dict) else {}
        config = AliasConfig(parent.base_dir if parent else None, dict(parent.paths) if parent else {})
        if isinstance(options.get("baseUrl"), str):
            config.base_dir = posixpath.normpath(posixpath.join(config_dir, options["baseUrl"]))
        if isinstance(options.get("paths"), dict):
            # `paths` targets are relative to baseUrl when set, otherwise to the config's own directory
            base = config.base_dir if config.base_dir is not None else config_dir
            config.paths = {
                pattern: [posixpath.normpath(posixpath.join(base, t)) for t in targets if isinstance(t, str)]
                for pattern, targets in options["paths"].items()
                if isinstance(targets, list)
            }
        return config

    def _alias_config(self, importer: str) -> AliasConfig | None:
        """The nearest tsconfig.json/jsconfig.json at or above the importer (that's the one TS would use)."""
        directory = posixpath.dirname(importer)
        while True:
            for name in TS_CONFIG_NAMES:
                config_path = posixpath.normpath(posixpath.join(directory, name))
                if config_path not in self._alias_cache:
                    exists = self.repo_root is not None and (self.repo_root / config_path).is_file()
                    self._alias_cache[config_path] = self._read_config(config_path) if exists else None
                if self._alias_cache[config_path] is not None:
                    return self._alias_cache[config_path]
            if directory in ("", "."):
                return None
            directory = posixpath.dirname(directory)

    def _resolve_alias(self, spec: str, importer: str) -> tuple[str | None, float]:
        config = self._alias_config(importer)
        if config is None:
            return None, 0.0  # bare package / no alias configured: external as far as we can tell

        for pattern, targets in config.paths.items():
            star = None
            if "*" in pattern:
                prefix, suffix = pattern.split("*", 1)
                if spec.startswith(prefix) and spec.endswith(suffix) and len(spec) >= len(prefix) + len(suffix):
                    star = spec[len(prefix) : len(spec) - len(suffix)]
            elif spec == pattern:
                star = ""
            if star is None:
                continue
            for target in targets:
                found = self._javascript_file(target.replace("*", star, 1) if "*" in target else target)
                if found:
                    return found, CONF_EXACT

        if config.base_dir is not None:  # baseUrl: `import x from 'src/foo'`
            found = self._javascript_file(posixpath.normpath(posixpath.join(config.base_dir, spec)))
            if found:
                return found, CONF_EXACT
        return None, 0.0
