from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo


def _write(root: Path, rel_path: str, content: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A small git repo fixture with source/test/doc/config files and a .gitignore."""
    Repo.init(tmp_path)

    _write(tmp_path, "src/app.py", "def main():\n    return 1\n")
    _write(tmp_path, "src/utils.py", "def helper():\n    return 2\n")
    _write(tmp_path, "tests/test_app.py", "def test_main():\n    assert True\n")
    _write(tmp_path, "README.md", "# Demo\n")
    _write(tmp_path, "pyproject.toml", "[project]\nname='demo'\n")
    _write(tmp_path, "ignored.txt", "should not be scanned\n")
    _write(tmp_path, ".gitignore", "ignored.txt\n")

    return tmp_path
