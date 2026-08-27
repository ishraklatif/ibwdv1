from __future__ import annotations

from pathlib import Path

from ibwd.scanner.filesystem import classify_file, hash_file, scan_files


def test_classify_file():
    assert classify_file("src/app.py") == "source"
    assert classify_file("tests/test_app.py") == "test"
    assert classify_file("src/app.test.ts") == "test"
    assert classify_file("README.md") == "doc"
    assert classify_file("pyproject.toml") == "config"
    assert classify_file("Dockerfile") == "config"
    assert classify_file("data.bin") == "other"


def test_scan_files_respects_gitignore(git_repo: Path):
    scanned = scan_files(git_repo)
    paths = {f.path for f in scanned}

    assert "ignored.txt" not in paths
    assert ".git/config" not in paths
    assert "src/app.py" in paths
    assert "tests/test_app.py" in paths


def test_scan_files_classifies_correctly(git_repo: Path):
    scanned = {f.path: f.kind for f in scan_files(git_repo)}

    assert scanned["src/app.py"] == "source"
    assert scanned["tests/test_app.py"] == "test"
    assert scanned["README.md"] == "doc"
    assert scanned["pyproject.toml"] == "config"


def test_hash_is_stable_and_content_sensitive(git_repo: Path):
    path = git_repo / "src" / "app.py"
    h1 = hash_file(path)
    h2 = hash_file(path)
    assert h1 == h2

    path.write_text("def main():\n    return 2\n")
    h3 = hash_file(path)
    assert h3 != h1
