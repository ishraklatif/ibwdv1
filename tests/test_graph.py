from __future__ import annotations

from pathlib import Path

from ibwd.graph.database import connect
from ibwd.graph.queries import find_files, sync_files
from ibwd.scanner.filesystem import scan_files


def test_sync_files_creates_nodes_and_edges(git_repo: Path):
    conn = connect(git_repo / ".ibwd" / "graph.db")
    scanned = scan_files(git_repo)

    summary = sync_files(conn, scanned, previous_manifest={})

    assert summary.added == len(scanned)
    assert summary.changed == 0
    assert summary.removed == 0

    file_rows = conn.execute("SELECT COUNT(*) FROM nodes WHERE node_type = 'File'").fetchone()[0]
    assert file_rows == len(scanned)

    dir_rows = conn.execute("SELECT COUNT(*) FROM nodes WHERE node_type = 'Directory'").fetchone()[0]
    assert dir_rows > 0  # root '.', 'src', 'tests' at least

    contains_edges = conn.execute("SELECT COUNT(*) FROM edges WHERE relation = 'CONTAINS'").fetchone()[0]
    assert contains_edges > 0

    # every CONTAINS edge should be a static, fully-confident fact
    rows = conn.execute("SELECT DISTINCT confidence, source_type FROM edges").fetchall()
    for row in rows:
        assert row["confidence"] == 1.0
        assert row["source_type"] == "static_analysis"

    conn.close()


def test_sync_files_is_idempotent_and_detects_changes(git_repo: Path):
    conn = connect(git_repo / ".ibwd" / "graph.db")

    scanned = scan_files(git_repo)
    sync_files(conn, scanned, previous_manifest={})
    manifest = {f.path: f.content_hash for f in scanned}

    # unchanged rescan
    rescanned = scan_files(git_repo)
    summary = sync_files(conn, rescanned, previous_manifest=manifest)
    assert summary.added == 0
    assert summary.changed == 0
    assert summary.unchanged == len(rescanned)

    # modify one file
    (git_repo / "src" / "app.py").write_text("def main():\n    return 99\n")
    rescanned2 = scan_files(git_repo)
    summary2 = sync_files(conn, rescanned2, previous_manifest=manifest)
    assert summary2.changed == 1
    assert summary2.unchanged == len(rescanned2) - 1

    conn.close()


def test_sync_files_removes_deleted_files(git_repo: Path):
    conn = connect(git_repo / ".ibwd" / "graph.db")
    scanned = scan_files(git_repo)
    sync_files(conn, scanned, previous_manifest={})
    manifest = {f.path: f.content_hash for f in scanned}

    (git_repo / "src" / "utils.py").unlink()
    rescanned = scan_files(git_repo)
    summary = sync_files(conn, rescanned, previous_manifest=manifest)

    assert summary.removed == 1
    remaining = {row["file_path"] for row in find_files(conn)}
    assert "src/utils.py" not in remaining

    conn.close()


def test_find_files_filters_by_kind_and_pattern(git_repo: Path):
    conn = connect(git_repo / ".ibwd" / "graph.db")
    sync_files(conn, scan_files(git_repo), previous_manifest={})

    source_files = {row["file_path"] for row in find_files(conn, kind="source")}
    assert source_files == {"src/app.py", "src/utils.py"}

    app_files = {row["file_path"] for row in find_files(conn, name_pattern="app")}
    assert app_files == {"src/app.py", "tests/test_app.py"}

    conn.close()
