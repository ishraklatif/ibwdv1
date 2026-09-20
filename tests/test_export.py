from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from ibwd.cli import main
from ibwd.export import export_graph
from ibwd.graph.database import connect
from ibwd.scan import run_scan

REPO = {
    "lib.py": "def helper():\n    return 1\n\nclass Base:\n    def step(self):\n        return helper()\n",
    "app.py": "from lib import helper, Base\n\nclass Child(Base):\n    def go(self):\n        return self.step()\n\ndef main():\n    return helper()\n",
}


def _write(root: Path) -> None:
    for name, text in REPO.items():
        (root / name).write_text(text)


def test_export_uses_stable_symbol_ids_and_tiers(tmp_path: Path):
    _write(tmp_path)
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    data = export_graph(conn, "demo", "abc123")

    ids = {s["id"] for s in data["symbols"]}
    assert {"lib.py::helper", "lib.py::Base.step", "app.py::Child.go", "app.py::main", "app.py", "lib.py"} <= ids
    assert data["repo_sha"] == "abc123" and data["callsite_positions"] is False
    assert {"ibwd_commit", "ibwd_dirty", "edge_build_version", "scope"} <= data.keys()  # provenance of the export

    edges = {(e["source"], e["target"], e["relation"]): e for e in data["edges"]}
    assert edges[("app.py::main", "lib.py::helper", "CALLS")]["tier"] == "import_map"
    assert edges[("lib.py::Base.step", "lib.py::helper", "CALLS")]["tier"] == "same_module"
    assert edges[("app.py::Child.go", "lib.py::Base.step", "CALLS")]["tier"] == "inherited"
    assert edges[("app.py::Child", "lib.py::Base", "INHERITS")]["tier"] == "import_map"
    assert edges[("app.py", "lib.py", "IMPORTS")]["tier"] == "path"
    assert all(e["resolution_status"] == "resolved" for e in data["edges"])  # every tier in this fixture is resolved
    assert not any(str(k[0]).isdigit() for k in edges)  # never SQLite row ids


def test_export_cli_writes_json(tmp_path: Path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    run_scan(tmp_path)
    out = tmp_path / "graph.json"

    result = CliRunner().invoke(main, ["export", str(out), "--repo-sha", "deadbeef"])

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["repo_sha"] == "deadbeef"


def test_compare_pairs_reports_precision_recall_and_unmapped_symbols(tmp_path: Path):
    _write(tmp_path)
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    ibwd = export_graph(conn, "demo", "x")
    (tmp_path / "ibwd.json").write_text(json.dumps(ibwd))

    # oracle in the kit's format: one edge IBWD has, one it lacks, one whose symbol can't be mapped
    oracle = {
        "complete": False,
        "symbols": [
            {"id": "app.py:7:main", "file": "app.py", "line": 7, "name": "main"},
            {"id": "lib.py:1:helper", "file": "lib.py", "line": 1, "name": "helper"},
            {"id": "lib.py:5:Base.step", "file": "lib.py", "line": 5, "name": "Base.step"},
            {"id": "app.py:4:Child.go", "file": "app.py", "line": 4, "name": "Child.go"},
            {"id": "app.py:99:ghost", "file": "app.py", "line": 99, "name": "ghost"},
        ],
        "edges": [
            {"source": "app.py:7:main", "target": "lib.py:1:helper", "relation": "CALLS"},          # in IBWD
            {"source": "app.py:4:Child.go", "target": "lib.py:1:helper", "relation": "CALLS"},      # NOT in IBWD (missing)
            {"source": "app.py:99:ghost", "target": "lib.py:1:helper", "relation": "CALLS"},        # unmappable
        ],
    }
    (tmp_path / "oracle.json").write_text(json.dumps(oracle))

    script = Path(__file__).resolve().parent.parent / "benchmarks" / "tools" / "compare_pairs.py"
    out = json.loads(subprocess.run([sys.executable, str(script), str(tmp_path / "oracle.json"), str(tmp_path / "ibwd.json"),
                                     "--relation", "CALLS"], capture_output=True, text=True, check=True).stdout)

    assert out["overall"]["tp"] == 1 and out["overall"]["fn"] == 1
    assert out["overall"]["recall"] == 0.5
    assert out["symbol_mapping"]["oracle_symbols_unmapped"] == 1 and out["symbol_mapping"]["oracle_edges_dropped_unmapped"] == 1
    assert ["app.py::Child.go", "lib.py::helper", "CALLS"] in out["missing"]
    assert out["granularity"].startswith("caller symbol -> target symbol")
