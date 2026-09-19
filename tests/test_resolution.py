from __future__ import annotations

from pathlib import Path

from ibwd.graph.database import connect
from ibwd.scan import run_scan


def _write(root: Path, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _edges(root: Path, relation: str) -> dict[tuple[str, str], float]:
    """{(source, target): confidence}; symbols are labelled by qualified_name, files by path."""
    conn = connect(root / ".ibwd" / "graph.db")
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(s.qualified_name, s.file_path) AS src,
                   COALESCE(t.qualified_name, t.file_path) AS tgt,
                   e.confidence, e.source_type
            FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id
            WHERE e.relation = ?
            """,
            (relation,),
        ).fetchall()
    finally:
        conn.close()
    assert all(row["source_type"] == "static_analysis" for row in rows)
    return {(row["src"], row["tgt"]): row["confidence"] for row in rows}


PY_REPO = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "def helper():\n"
        "    return 1\n"
        "\n"
        "def unique_thing():\n"
        "    return 2\n"
        "\n"
        "class Base:\n"
        "    def run(self):\n"
        "        return self.step()\n"
        "    def step(self):\n"
        "        return helper()\n"
        "\n"
        "class Child(Base):\n"
        "    def step(self):\n"
        "        return 0\n"
        "\n"
        "def local_caller():\n"
        "    return helper()\n"
    ),
    "pkg/app.py": (
        "import os\n"
        "from pkg.core import helper as h, Base\n"
        "from pkg import core\n"
        "\n"
        "class Sub(Base):\n"
        "    pass\n"
        "\n"
        "def main():\n"
        "    h()\n"
        "    core.unique_thing()\n"
        "    os.path.join('a')\n"
        "    Base.step(None)\n"
    ),
}


def test_tier1_import_map_is_0_95(tmp_path: Path):
    _write(tmp_path, PY_REPO)
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("pkg/app.py::main", "pkg/core.py::helper")] == 0.95  # `from m import helper as h`
    assert calls[("pkg/app.py::main", "pkg/core.py::unique_thing")] == 0.95  # `core.unique_thing()` via submodule import
    assert calls[("pkg/app.py::main", "pkg/core.py::Base.step")] == 0.95  # `Base.step()` on an imported class


def test_external_package_calls_are_not_resolved(tmp_path: Path):
    _write(tmp_path, {**PY_REPO, "pkg/join.py": "def join(x):\n    return x\n"})
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    # os.path.join(...) must not be linked to the repo's unrelated `join`
    assert not any(src == "pkg/app.py::main" and tgt == "pkg/join.py::join" for src, tgt in calls)


def test_tier2_same_module_is_0_90(tmp_path: Path):
    _write(tmp_path, PY_REPO)
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("pkg/core.py::local_caller", "pkg/core.py::helper")] == 0.90
    # self.step() inside Base.run binds to Base.step, not Child.step
    assert calls[("pkg/core.py::Base.run", "pkg/core.py::Base.step")] == 0.90
    assert ("pkg/core.py::Base.run", "pkg/core.py::Child.step") not in calls


def test_tier3_unique_name_is_0_75(tmp_path: Path):
    _write(
        tmp_path,
        {
            "a.py": "def caller(x):\n    mystery_fn()\n    return x.only_here()\n",
            "b.py": "def mystery_fn():\n    return 1\n\nclass Thing:\n    def only_here(self):\n        return 2\n",
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("a.py::caller", "b.py::mystery_fn")] == 0.75
    assert calls[("a.py::caller", "b.py::Thing.only_here")] == 0.75


def test_tier4_suffix_uses_receiver_name_when_name_is_ambiguous(tmp_path: Path):
    _write(
        tmp_path,
        {
            "repos.py": (
                "class UserRepo:\n    def save(self):\n        return 1\n\n"
                "class OrderRepo:\n    def save(self):\n        return 2\n"
            ),
            "svc.py": "def go(user_repo):\n    return user_repo.save()\n",
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("svc.py::go", "repos.py::UserRepo.save")] == 0.55
    assert ("svc.py::go", "repos.py::OrderRepo.save") not in calls


def test_attribute_calls_do_not_link_to_unrelated_module_level_functions(tmp_path: Path):
    _write(
        tmp_path,
        {
            "tools.py": "def tool():\n    return 1\n",
            "server.py": "mcp = make()\n\n@mcp.tool()\ndef handler():\n    return 2\n",
        },
    )
    run_scan(tmp_path)

    # `mcp` is a plain variable, so `.tool()` is a method call — never tools.py::tool
    assert not any(tgt == "tools.py::tool" for _, tgt in _edges(tmp_path, "CALLS"))


def test_module_alias_receiver_may_still_reach_a_function_elsewhere(tmp_path: Path):
    # `utils` is an internal module alias whose file doesn't define `reexported`
    # (e.g. it re-exports it), so the unique-name fallback may still link it.
    _write(
        tmp_path,
        {
            "utils.py": "from impl import reexported\n",
            "impl.py": "def reexported():\n    return 1\n",
            "main.py": "import utils\n\ndef go():\n    return utils.reexported()\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("main.py::go", "impl.py::reexported")] == 0.75


def test_tier5_fuzzy_ignores_case_and_underscores(tmp_path: Path):
    _write(
        tmp_path,
        {
            "a.py": "def fetch_data():\n    return 1\n",
            "b.py": "def caller():\n    return fetchData()\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("b.py::caller", "a.py::fetch_data")] == 0.35


def test_ambiguous_names_are_left_unresolved(tmp_path: Path):
    _write(
        tmp_path,
        {
            "a.py": "def run():\n    return 1\n",
            "b.py": "def run():\n    return 2\n",
            "c.py": "def caller(thing):\n    return thing.run()\n",
        },
    )
    run_scan(tmp_path)

    assert not any(src == "c.py::caller" for src, _ in _edges(tmp_path, "CALLS"))


def test_module_level_calls_are_attributed_to_the_file(tmp_path: Path):
    _write(
        tmp_path,
        {
            "lib.py": "def setup():\n    return 1\n",
            "script.py": "from lib import setup\n\nsetup()\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("script.py", "lib.py::setup")] == 0.95


def test_inherits_edges_same_module_and_imported(tmp_path: Path):
    _write(tmp_path, PY_REPO)
    run_scan(tmp_path)
    inherits = _edges(tmp_path, "INHERITS")

    assert inherits[("pkg/core.py::Child", "pkg/core.py::Base")] == 0.90
    assert inherits[("pkg/app.py::Sub", "pkg/core.py::Base")] == 0.95


def test_imports_edges_are_file_to_file(tmp_path: Path):
    _write(
        tmp_path,
        {
            **PY_REPO,
            "src/lib/util.py": "def u():\n    return 1\n",
            "src/lib/user.py": "from .util import u\nimport os\n",
        },
    )
    run_scan(tmp_path)
    imports = _edges(tmp_path, "IMPORTS")

    assert imports[("pkg/app.py", "pkg/core.py")] == 1.0
    assert imports[("pkg/app.py", "pkg/__init__.py")] == 1.0
    assert imports[("src/lib/user.py", "src/lib/util.py")] == 1.0  # relative import
    assert not any(src == "src/lib/user.py" and tgt != "src/lib/util.py" for src, tgt in imports)  # `os` is external


def test_python_src_layout_import_is_resolved_from_a_sibling_tree(tmp_path: Path):
    _write(
        tmp_path,
        {
            "src/mypkg/__init__.py": "",
            "src/mypkg/mod.py": "def f():\n    return 1\n",
            "scripts/run.py": "from mypkg.mod import f\n\ndef go():\n    f()\n",
        },
    )
    run_scan(tmp_path)

    # `mypkg` isn't at the repo root, but src/ is a conventional import root
    assert _edges(tmp_path, "IMPORTS")[("scripts/run.py", "src/mypkg/mod.py")] == 0.9
    assert _edges(tmp_path, "CALLS")[("scripts/run.py::go", "src/mypkg/mod.py::f")] == 0.95


def test_test_files_are_not_part_of_the_call_graph(tmp_path: Path):
    # Test files carry no symbol nodes (Sprint 2) and are linked via TESTED_BY in
    # Sprint 4, so they don't show up as callers/importers here.
    _write(
        tmp_path,
        {
            "lib.py": "def f():\n    return 1\n",
            "tests/test_lib.py": "from lib import f\n\ndef test_f():\n    f()\n",
        },
    )
    run_scan(tmp_path)

    assert not any(src.startswith("tests/") for src, _ in _edges(tmp_path, "CALLS"))
    assert not any(src.startswith("tests/") for src, _ in _edges(tmp_path, "IMPORTS"))


JS_REPO = {
    "web/util.js": "export function fmt() { return 1; }\nexport const twice = () => 2;\n",
    "web/base.js": "export class Base { hello() { return 1; } }\n",
    "web/app.js": (
        "import { fmt } from './util';\n"
        "import * as u from './util';\n"
        "import Base from './base';\n"
        "class Widget extends Base {\n"
        "  render() { this.paint(); return fmt(); }\n"
        "  paint() { return u.twice(); }\n"
        "}\n"
        "function main() { return new Widget(); }\n"
    ),
    "web/main.ts": "import { fmt } from './util.js';\nexport function run() { return fmt(); }\n",
}


def test_javascript_calls_imports_and_inherits(tmp_path: Path):
    _write(tmp_path, JS_REPO)
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")
    imports = _edges(tmp_path, "IMPORTS")
    inherits = _edges(tmp_path, "INHERITS")

    assert calls[("web/app.js::Widget.render", "web/util.js::fmt")] == 0.95  # named import
    assert calls[("web/app.js::Widget.paint", "web/util.js::twice")] == 0.95  # namespace import
    assert calls[("web/app.js::Widget.render", "web/app.js::Widget.paint")] == 0.90  # this.paint()
    assert calls[("web/app.js::main", "web/app.js::Widget")] == 0.90  # new Widget()
    assert inherits[("web/app.js::Widget", "web/base.js::Base")] == 0.95  # default import
    assert imports[("web/app.js", "web/util.js")] == 1.0
    assert imports[("web/main.ts", "web/util.js")] == 1.0  # './util.js' resolves to the real file
    assert calls[("web/main.ts::run", "web/util.js::fmt")] == 0.95


def test_edges_re_resolve_when_an_unchanged_file_gains_a_target(tmp_path: Path):
    _write(tmp_path, {"a.py": "def caller():\n    return late()\n"})
    run_scan(tmp_path)
    assert _edges(tmp_path, "CALLS") == {}

    _write(tmp_path, {"b.py": "def late():\n    return 1\n"})
    run_scan(tmp_path)
    assert _edges(tmp_path, "CALLS") == {("a.py::caller", "b.py::late"): 0.75}  # a.py itself never changed

    (tmp_path / "b.py").unlink()
    run_scan(tmp_path)
    assert _edges(tmp_path, "CALLS") == {}


def test_unchanged_scan_keeps_edges_and_stale_graph_is_rebuilt(tmp_path: Path):
    _write(tmp_path, PY_REPO)
    first = run_scan(tmp_path)
    assert first["edges"]["CALLS"] > 0

    second = run_scan(tmp_path)
    assert second["edges"] == first["edges"]

    # A graph built before Sprint 3 (no edge-build version, no edges) is
    # rebuilt on the next scan even though no file changed.
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    conn.execute("DELETE FROM edges WHERE relation IN ('IMPORTS', 'CALLS', 'INHERITS')")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    third = run_scan(tmp_path)
    assert third["unchanged"] == third["total_files"]
    assert third["edges"] == first["edges"]
