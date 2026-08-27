from __future__ import annotations

from pathlib import Path

from ibwd.scan import run_scan


def test_run_scan_end_to_end(git_repo: Path):
    summary = run_scan(git_repo)
    assert summary["added"] == summary["total_files"]
    assert summary["removed"] == 0

    manifest_path = git_repo / ".ibwd" / "manifest.json"
    assert manifest_path.exists()

    # second run with no changes: nothing added/changed
    summary2 = run_scan(git_repo)
    assert summary2["added"] == 0
    assert summary2["changed"] == 0
    assert summary2["unchanged"] == summary2["total_files"]


def test_run_scan_incremental_after_edit(git_repo: Path):
    run_scan(git_repo)

    (git_repo / "src" / "app.py").write_text("def main():\n    return 42\n")
    summary = run_scan(git_repo)

    assert summary["changed"] == 1
    assert summary["added"] == 0
