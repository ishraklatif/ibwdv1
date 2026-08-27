"""Filesystem scanner: walk a repo respecting .gitignore, classify files, hash contents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pathspec
import xxhash

ALWAYS_IGNORE = {".git", ".ibwd", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".mypy_cache"}

TEST_NAME_PATTERNS = ("test_", "_test.", ".test.", ".spec.")
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec"}

DOC_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}
DOC_NAMES = {"readme", "changelog", "license", "contributing", "authors", "notice"}
DOC_DIR_NAMES = {"docs", "doc"}

CONFIG_EXTENSIONS = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".env"}
CONFIG_NAMES = {
    "dockerfile", "makefile", ".gitignore", ".dockerignore", ".editorconfig",
    "pyproject.toml", "package.json", "package-lock.json", "poetry.lock",
    "requirements.txt", "setup.py", "setup.cfg", "uv.lock",
}

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".hpp", ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".sh",
}


@dataclass
class ScannedFile:
    path: str  # posix-style, relative to repo root
    kind: str  # source | test | doc | config | other
    content_hash: str


def _load_gitignore_spec(repo_root: Path) -> pathspec.PathSpec:
    gitignore = repo_root / ".gitignore"
    lines: list[str] = []
    if gitignore.exists():
        lines = gitignore.read_text(errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


def classify_file(rel_path: str) -> str:
    p = Path(rel_path)
    name_lower = p.name.lower()
    ext = p.suffix.lower()
    parts_lower = {part.lower() for part in p.parts[:-1]}

    if parts_lower & TEST_DIR_NAMES or any(pat in name_lower for pat in TEST_NAME_PATTERNS):
        return "test"
    if ext in CONFIG_EXTENSIONS or name_lower in CONFIG_NAMES:
        return "config"
    if ext in DOC_EXTENSIONS or name_lower.split(".")[0] in DOC_NAMES or parts_lower & DOC_DIR_NAMES:
        return "doc"
    if ext in SOURCE_EXTENSIONS:
        return "source"
    return "other"


def hash_file(path: Path) -> str:
    hasher = xxhash.xxh3_64()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def scan_files(repo_root: Path) -> list[ScannedFile]:
    """Walk repo_root respecting .gitignore + ALWAYS_IGNORE, return classified+hashed files."""
    spec = _load_gitignore_spec(repo_root)
    results: list[ScannedFile] = []

    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        rel_path = path.relative_to(repo_root)
        if any(part in ALWAYS_IGNORE for part in rel_path.parts):
            continue
        rel_posix = rel_path.as_posix()
        if spec.match_file(rel_posix):
            continue
        try:
            content_hash = hash_file(path)
        except OSError:
            continue
        results.append(ScannedFile(path=rel_posix, kind=classify_file(rel_posix), content_hash=content_hash))

    return results
