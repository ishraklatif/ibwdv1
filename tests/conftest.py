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


@pytest.fixture
def symbol_repo(tmp_path: Path) -> Path:
    """A git repo with Python + JS/TS source, for symbol-extraction tests.

    `greet` is defined twice — once as a method (src/models.py::User.greet)
    and once as a top-level function (src/other.py::greet) — so tests can
    confirm ibwd_find_symbol returns both, not just one.
    """
    Repo.init(tmp_path)

    _write(
        tmp_path,
        "src/models.py",
        "class User:\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "\n"
        "    def greet(self):\n"
        "        return f'hi {self.name}'\n"
        "\n"
        "\n"
        "def create_user(name):\n"
        "    return User(name)\n",
    )
    _write(
        tmp_path,
        "src/other.py",
        "def greet():\n"
        "    return 'hello'\n",
    )
    _write(
        tmp_path,
        "web/app.js",
        "function main() {\n"
        "  return 1;\n"
        "}\n"
        "\n"
        "class Widget {\n"
        "  render() {\n"
        "    return null;\n"
        "  }\n"
        "}\n"
        "\n"
        "const helper = () => 42;\n",
    )
    _write(
        tmp_path,
        "web/util.ts",
        "export function util(): number {\n"
        "  return 1;\n"
        "}\n",
    )

    return tmp_path
