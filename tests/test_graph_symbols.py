from __future__ import annotations

from pathlib import Path

from ibwd.scan import run_scan
from ibwd.graph.database import connect
from ibwd.graph.queries import find_symbol, list_symbols


def test_run_scan_extracts_symbols_across_python_and_js(symbol_repo: Path):
    run_scan(symbol_repo)
    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        symbol_rows = conn.execute(
            "SELECT node_type, name, file_path FROM nodes WHERE node_type IN ('Class','Function','Method')"
        ).fetchall()
        names = {(row["node_type"], row["name"], row["file_path"]) for row in symbol_rows}

        assert ("Class", "User", "src/models.py") in names
        assert ("Method", "__init__", "src/models.py") in names
        assert ("Method", "greet", "src/models.py") in names
        assert ("Function", "create_user", "src/models.py") in names
        assert ("Function", "greet", "src/other.py") in names
        assert ("Function", "main", "web/app.js") in names
        assert ("Class", "Widget", "web/app.js") in names
        assert ("Method", "render", "web/app.js") in names
        assert ("Function", "util", "web/util.ts") in names

        # every DEFINES edge should be a static, fully-confident fact
        edge_rows = conn.execute("SELECT DISTINCT confidence, source_type FROM edges WHERE relation = 'DEFINES'").fetchall()
        for row in edge_rows:
            assert row["confidence"] == 1.0
            assert row["source_type"] == "static_analysis"
    finally:
        conn.close()


def test_find_symbol_returns_same_named_method_and_function_in_two_files(symbol_repo: Path):
    run_scan(symbol_repo)
    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        rows = find_symbol(conn, "greet")
        matches = {(row["kind"], row["file_path"]) for row in rows}
        assert matches == {("Method", "src/models.py"), ("Function", "src/other.py")}
    finally:
        conn.close()


def test_find_symbol_falls_back_to_case_insensitive_substring(symbol_repo: Path):
    run_scan(symbol_repo)
    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        assert find_symbol(conn, "nonexistent-exact-name") == []
        rows = find_symbol(conn, "WIDGET")
        assert {row["name"] for row in rows} == {"Widget"}
    finally:
        conn.close()


def test_list_symbols_returns_ordered_by_line(symbol_repo: Path):
    run_scan(symbol_repo)
    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        rows = list_symbols(conn, "src/models.py")
        assert [row["name"] for row in rows] == ["User", "__init__", "greet", "create_user"]
    finally:
        conn.close()


def test_symbols_are_reparsed_only_on_content_change(symbol_repo: Path):
    run_scan(symbol_repo)
    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path = 'src/other.py' AND node_type = 'Function'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert before == 1  # just the `greet` function

    # add a symbol to a different file; src/other.py content is unchanged
    (symbol_repo / "src" / "models.py").write_text(
        (symbol_repo / "src" / "models.py").read_text() + "\n\ndef extra():\n    pass\n"
    )
    run_scan(symbol_repo)

    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        rows = find_symbol(conn, "extra")
        assert len(rows) == 1
        after = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path = 'src/other.py' AND node_type = 'Function'"
        ).fetchone()[0]
        assert after == 1
    finally:
        conn.close()


def test_symbols_removed_when_file_deleted(symbol_repo: Path):
    run_scan(symbol_repo)
    (symbol_repo / "src" / "other.py").unlink()
    run_scan(symbol_repo)

    conn = connect(symbol_repo / ".ibwd" / "graph.db")
    try:
        remaining = conn.execute("SELECT COUNT(*) FROM nodes WHERE file_path = 'src/other.py'").fetchone()[0]
        assert remaining == 0
    finally:
        conn.close()
