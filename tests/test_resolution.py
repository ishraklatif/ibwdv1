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


def test_module_alias_receiver_follows_a_python_reexport(tmp_path: Path):
    # `utils` re-exports `reexported` from impl, so utils.reexported() resolves through the import map (was: a 0.75 name guess)
    _write(
        tmp_path,
        {
            "utils.py": "from impl import reexported\n",
            "impl.py": "def reexported():\n    return 1\n",
            "main.py": "import utils\n\ndef go():\n    return utils.reexported()\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("main.py::go", "impl.py::reexported")] == 0.95


def test_fuzzy_tier_is_disabled_by_default_and_experimental_when_enabled(tmp_path: Path, monkeypatch):
    files = {
        "a.py": "def fetch_data():\n    return 1\n",
        "b.py": "def caller():\n    return fetchData()\n",
    }
    _write(tmp_path / "off", files)
    run_scan(tmp_path / "off")
    assert _edges(tmp_path / "off", "CALLS") == {}  # fuzzy never creates an ordinary edge

    monkeypatch.setenv("IBWD_EXPERIMENTAL_FUZZY", "1")
    _write(tmp_path / "on", files)
    run_scan(tmp_path / "on")
    assert _edges(tmp_path / "on", "CALLS")[("b.py::caller", "a.py::fetch_data")] == 0.35  # opt-in only


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


JSX_REPO = {
    "app/Button.tsx": "export function Button() { return <button />; }\n",
    "app/Card.tsx": "export default function Card() { return <div />; }\n",
    "app/ui/index.ts": "export function Badge() { return null; }\n",
    "app/Screen.tsx": (
        "import { Button } from './Button';\n"
        "import Card from './Card';\n"
        "import * as ui from './ui';\n"
        "function Header() { return <h1 />; }\n"
        "export function Screen() {\n"
        "  return (<div><Header /><Button /><Card /><ui.Badge /></div>);\n"
        "}\n"
    ),
}


def test_jsx_tags_create_calls_edges_from_the_rendering_component(tmp_path: Path):
    _write(tmp_path, JSX_REPO)
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("app/Screen.tsx::Screen", "app/Screen.tsx::Header")] == 0.90  # same file
    assert calls[("app/Screen.tsx::Screen", "app/Button.tsx::Button")] == 0.95  # named import
    assert calls[("app/Screen.tsx::Screen", "app/Card.tsx::Card")] == 0.95  # default import
    assert calls[("app/Screen.tsx::Screen", "app/ui/index.ts::Badge")] == 0.95  # namespace import, <ui.Badge />
    # intrinsic DOM tags create nothing
    assert not any("button" in tgt or "h1" in tgt for _, tgt in calls)


TSCONFIG_REPO = {
    "app/tsconfig.json": (
        "{\n"
        "  // JSONC: comments and trailing commas are legal in tsconfig\n"
        '  "compilerOptions": {\n'
        '    "baseUrl": ".",\n'
        '    "paths": { "@/*": ["./*"], "@lib": ["lib/index.ts"], },\n'
        "  },\n"
        "}\n"
    ),
    "app/components/Ui.tsx": "export function Panel() { return null; }\n",
    "app/lib/index.ts": "export function helper() { return 1; }\n",
    "app/screens/Home.tsx": (
        "import { Panel } from '@/components/Ui';\n"
        "import { helper } from '@lib';\n"
        "import React from 'react';\n"
        "export function Home() { helper(); return <Panel />; }\n"
    ),
}


def test_tsconfig_paths_aliases_resolve_imports(tmp_path: Path):
    _write(tmp_path, TSCONFIG_REPO)
    run_scan(tmp_path)

    imports = _edges(tmp_path, "IMPORTS")
    assert imports[("app/screens/Home.tsx", "app/components/Ui.tsx")] == 1.0  # '@/*' wildcard
    assert imports[("app/screens/Home.tsx", "app/lib/index.ts")] == 1.0  # exact alias '@lib'
    assert not any(tgt.endswith("react") for _, tgt in imports)  # npm package stays external

    calls = _edges(tmp_path, "CALLS")
    assert calls[("app/screens/Home.tsx::Home", "app/components/Ui.tsx::Panel")] == 0.95
    assert calls[("app/screens/Home.tsx::Home", "app/lib/index.ts::helper")] == 0.95


def test_alias_without_any_tsconfig_stays_unresolved(tmp_path: Path):
    files = {k: v for k, v in TSCONFIG_REPO.items() if k != "app/tsconfig.json"}
    _write(tmp_path, files)
    run_scan(tmp_path)

    assert not any(src == "app/screens/Home.tsx" for src, _ in _edges(tmp_path, "IMPORTS"))


def test_local_extends_supplies_paths_and_nearest_config_wins(tmp_path: Path):
    _write(
        tmp_path,
        {
            "tsconfig.base.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
            "web/tsconfig.json": '{"extends": "../tsconfig.base.json"}',
            "src/util.ts": "export function u() { return 1; }\n",
            "web/page.ts": "import { u } from '@/util';\nexport function page() { return u(); }\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "IMPORTS")[("web/page.ts", "src/util.ts")] == 1.0


def test_changing_tsconfig_alone_triggers_a_rebuild(tmp_path: Path):
    files = dict(TSCONFIG_REPO)
    files["app/tsconfig.json"] = '{"compilerOptions": {"baseUrl": "."}}'  # no aliases yet
    _write(tmp_path, files)
    run_scan(tmp_path)
    assert not any(src == "app/screens/Home.tsx" for src, _ in _edges(tmp_path, "IMPORTS"))

    _write(tmp_path, {"app/tsconfig.json": TSCONFIG_REPO["app/tsconfig.json"]})  # only the config changes
    summary = run_scan(tmp_path)

    assert summary["changed"] == 1
    assert _edges(tmp_path, "IMPORTS")[("app/screens/Home.tsx", "app/components/Ui.tsx")] == 1.0


def test_default_import_resolves_through_the_files_default_export_when_names_differ(tmp_path: Path):
    _write(
        tmp_path,
        {
            "app/MyCard.tsx": "function MyCard() { return null; }\nexport default MyCard;\n",
            "app/Wrapped.tsx": "const Inner = () => null;\nexport default React.memo(Inner);\n",
            "app/Screen.tsx": (
                "import Card from './MyCard';\n"
                "import Widget from './Wrapped';\n"
                "export function Screen() { return (<div><Card /><Widget /></div>); }\n"
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    # local names (Card, Widget) differ from the exported names (MyCard, Inner)
    assert calls[("app/Screen.tsx::Screen", "app/MyCard.tsx::MyCard")] == 0.95
    assert calls[("app/Screen.tsx::Screen", "app/Wrapped.tsx::Inner")] == 0.95


INHERIT_REPO = {
    "base.py": (
        "class Base:\n"
        "    def step(self):\n"
        "        return 1\n"
        "    def shared(self):\n"
        "        return 2\n"
    ),
    "other.py": "class Other:\n    def step(self):\n        return 9\n",  # makes `step` ambiguous by name alone
    "child.py": (
        "from base import Base\n"
        "\n"
        "class Child(Base):\n"
        "    def run(self):\n"
        "        return self.step()\n"
        "    def shared(self):\n"
        "        return super().shared()\n"
        "    def own(self):\n"
        "        return self.shared()\n"
        "\n"
        "class GrandChild(Child):\n"
        "    def deep(self):\n"
        "        return self.step()\n"
    ),
}


def test_self_method_follows_inheritance_across_files(tmp_path: Path):
    _write(tmp_path, INHERIT_REPO)
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    # `step` exists on Base and Other, so the name alone is ambiguous; inheritance decides
    assert calls[("child.py::Child.run", "base.py::Base.step")] == 0.85
    assert ("child.py::Child.run", "other.py::Other.step") not in calls
    # two levels up: GrandChild -> Child -> Base
    assert calls[("child.py::GrandChild.deep", "base.py::Base.step")] == 0.85


def test_own_class_beats_base_and_super_goes_to_base(tmp_path: Path):
    _write(tmp_path, INHERIT_REPO)
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("child.py::Child.own", "child.py::Child.shared")] == 0.90  # overridden in Child itself
    assert calls[("child.py::Child.shared", "base.py::Base.shared")] == 0.85  # super().shared() skips Child.shared
    assert ("child.py::Child.shared", "child.py::Child.shared") not in calls


def test_inheritance_cycles_do_not_hang(tmp_path: Path):
    _write(
        tmp_path,
        {
            "cyc.py": (
                "class A(B):\n    def go(self):\n        return self.missing()\n\n"
                "class B(A):\n    def other(self):\n        return 1\n"
            )
        },
    )
    run_scan(tmp_path)  # must terminate; `missing` simply stays unresolved
    assert not any(tgt.endswith("missing") for _, tgt in _edges(tmp_path, "CALLS"))


def test_javascript_this_and_super_follow_extends(tmp_path: Path):
    _write(
        tmp_path,
        {
            "web/base.js": "export class Base { render() { return 1; } paint() { return 2; } }\n",
            "web/other.js": "export class Other { render() { return 9; } }\n",
            "web/child.js": (
                "import { Base } from './base';\n"
                "export class Child extends Base {\n"
                "  go() { return this.render(); }\n"
                "  paint() { return super.paint(); }\n"
                "}\n"
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("web/child.js::Child.go", "web/base.js::Base.render")] == 0.85
    assert calls[("web/child.js::Child.paint", "web/base.js::Base.paint")] == 0.85


def _all_edges(root: Path) -> dict:
    return {rel: _edges(root, rel) for rel in ("IMPORTS", "CALLS", "INHERITS")}


def test_only_changed_files_are_reparsed(tmp_path: Path, monkeypatch):
    from ibwd.graph import resolution

    _write(tmp_path, PY_REPO)
    run_scan(tmp_path)

    parsed: list[str] = []
    real = resolution.extract_references
    monkeypatch.setattr(resolution, "extract_references", lambda abs_path, path: parsed.append(path) or real(abs_path, path))

    run_scan(tmp_path)
    assert parsed == []  # nothing changed: every file's references come from the cache

    (tmp_path / "pkg" / "core.py").write_text((tmp_path / "pkg" / "core.py").read_text() + "\ndef extra():\n    return helper()\n")
    run_scan(tmp_path)
    assert parsed == ["pkg/core.py"]


def test_incremental_rescan_gives_the_same_edges_as_a_fresh_scan(tmp_path: Path):
    inc, fresh = tmp_path / "inc", tmp_path / "fresh"
    _write(inc, {**PY_REPO, **INHERIT_REPO})
    run_scan(inc)

    # a mix of edits: change one file, add one, delete one, and touch an unrelated one
    _write(inc, {"pkg/app.py": PY_REPO["pkg/app.py"] + "\ndef newer():\n    return h()\n", "extra.py": "from base import Base\n\nclass X(Base):\n    def go(self):\n        return self.step()\n"})
    (inc / "other.py").unlink()
    run_scan(inc)

    files = {**PY_REPO, **INHERIT_REPO}
    files.pop("other.py")
    files["pkg/app.py"] = PY_REPO["pkg/app.py"] + "\ndef newer():\n    return h()\n"
    files["extra.py"] = "from base import Base\n\nclass X(Base):\n    def go(self):\n        return self.step()\n"
    _write(fresh, files)
    run_scan(fresh)

    assert _all_edges(inc) == _all_edges(fresh)


def test_reference_cache_rows_follow_deleted_files(tmp_path: Path):
    _write(tmp_path, {"a.py": "def a():\n    return 1\n", "b.py": "def b():\n    return 2\n"})
    run_scan(tmp_path)

    (tmp_path / "b.py").unlink()
    run_scan(tmp_path)

    conn = connect(tmp_path / ".ibwd" / "graph.db")
    assert [r["file_path"] for r in conn.execute("SELECT file_path FROM file_refs")] == ["a.py"]


def test_function_used_as_a_value_becomes_a_references_edge(tmp_path: Path):
    _write(
        tmp_path,
        {
            "hooks/reducer.ts": "export function reduceWithContext(state) { return state; }\n",
            "hooks/useScan.ts": (
                "import { reduceWithContext } from './reducer';\n"
                "export function useScan() { return useReducer(reduceWithContext, 0); }\n"
            ),
        },
    )
    run_scan(tmp_path)

    refs = _edges(tmp_path, "REFERENCES")
    assert refs[("hooks/useScan.ts::useScan", "hooks/reducer.ts::reduceWithContext")] == 0.95
    assert _edges(tmp_path, "CALLS") == {}  # it is referenced, not called


def test_value_references_never_use_the_loose_name_tiers(tmp_path: Path):
    _write(
        tmp_path,
        {
            "a.py": "def callback():\n    return 1\n",
            # `callback` here is an unrelated parameter and an unimported name: no edge may be guessed
            "b.py": "def use(callback):\n    return [callback]\n\ndef other():\n    return [callback]\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "REFERENCES") == {}


def test_python_value_reference_same_module_and_self_method(tmp_path: Path):
    _write(
        tmp_path,
        {
            "svc.py": (
                "def on_done():\n    return 1\n\n"
                "class Svc:\n"
                "    def handler(self):\n        return 2\n"
                "    def start(self):\n"
                "        register(on_done)\n"
                "        register(self.handler)\n"
            ),
        },
    )
    run_scan(tmp_path)
    refs = _edges(tmp_path, "REFERENCES")

    assert refs[("svc.py::Svc.start", "svc.py::on_done")] == 0.90
    assert refs[("svc.py::Svc.start", "svc.py::Svc.handler")] == 0.90


def test_references_are_returned_by_callers_and_labelled(tmp_path: Path):
    import asyncio, json
    from ibwd.mcp.server import mcp

    _write(
        tmp_path,
        {
            "hooks.ts": "export function reducer(s) { return s; }\n",
            "use.ts": "import { reducer } from './hooks';\nexport function useIt() { return useReducer(reducer, 0); }\n",
        },
    )
    import os
    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        run_scan(tmp_path)
        result = asyncio.run(mcp.call_tool("ibwd_callers", {"symbol": "reducer"}))
        data = result.structured_content.get("result") if getattr(result, "structured_content", None) else json.loads(result.content[0].text)
    finally:
        os.chdir(old)

    assert [(r["name"], r["relation"]) for r in data] == [("useIt", "REFERENCES")]


def test_fuzzy_tier_never_matches_single_word_or_underscore_only_differences(tmp_path: Path):
    _write(
        tmp_path,
        {
            "defs.py": (
                "class Warning:\n    pass\n\n"
                "class Holder:\n"
                "    def __get__(self):\n        return 1\n"
                "    def _update(self):\n        return 2\n"
            ),
            "use.py": (
                "def go(logger, d, cache):\n"
                "    logger.warning('x')\n"   # not the class Warning
                "    d.get('k')\n"            # not Holder.__get__
                "    cache.update({})\n"      # not Holder._update
            ),
        },
    )
    run_scan(tmp_path)

    assert not any(src == "use.py::go" for src, _ in _edges(tmp_path, "CALLS"))


def test_builtin_looking_method_names_are_not_matched_by_name_alone(tmp_path: Path):
    _write(
        tmp_path,
        {
            "registry.py": (
                "class Registry:\n"
                "    def items(self):\n        return []\n"
                "    def get(self, key):\n        return key\n"
                "    def own(self):\n        return self.items()\n"
            ),
            "use.py": (
                "def go(d, registry):\n"
                "    d.items()\n"           # a dict: must NOT link to Registry.items
                "    registry.get('k')\n"   # receiver is named like the class: suffix tier may link
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert ("use.py::go", "registry.py::Registry.items") not in calls
    assert calls[("use.py::go", "registry.py::Registry.get")] == 0.55  # suffix tier still works
    assert calls[("registry.py::Registry.own", "registry.py::Registry.items")] == 0.90  # self.items() is unaffected


def test_starred_call_resolves_to_a_calls_edge(tmp_path: Path):
    _write(
        tmp_path,
        {
            "m.py": (
                "def _ancestors(env, name):\n    return []\n\n"
                "class TocTree:\n"
                "    def get(self, name):\n"
                "        return [*_ancestors(self.env, name)]\n"
            )
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("m.py::TocTree.get", "m.py::_ancestors")] == 0.90
    assert _edges(tmp_path, "REFERENCES") == {}


def test_function_local_import_does_not_shadow_a_module_level_function(tmp_path: Path):
    _write(
        tmp_path,
        {
            "other.py": "def parse(x):\n    return x\n",
            "m.py": (
                "def parse(x):\n"
                "    from other import parse\n"   # local: only names inside this function
                "    return parse(x)\n"
                "\n"
                "class D:\n"
                "    def run(self):\n"
                "        return parse(1)\n"        # outside the function: the module-level `parse` in m.py
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("m.py::D.run", "m.py::parse")] == 0.90
    assert calls[("m.py::parse", "other.py::parse")] == 0.95  # inside the function the local import wins
    assert ("m.py::D.run", "other.py::parse") not in calls


def test_dynamic_imports_and_lazy_are_followed(tmp_path: Path):
    _write(
        tmp_path,
        {
            "mocks/db.ts": "export const initializeDb = async () => 1;\n",
            "mocks/other/db.ts": "export const initializeDb = async () => 2;\n",  # same name elsewhere: name alone is ambiguous
            "mocks/index.ts": (
                "export const enable = async () => {\n"
                "  const { initializeDb } = await import('./db');\n"
                "  await initializeDb();\n"
                "};\n"
            ),
            "routes/Page.tsx": "export default function Page() { return null; }\n",
            "routes/App.tsx": (
                "import { lazy } from 'react';\n"
                "const Lazy = lazy(() => import('./Page'));\n"
                "export function App() { return (<Lazy />); }\n"
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("mocks/index.ts::enable", "mocks/db.ts::initializeDb")] == 0.95
    assert calls[("routes/App.tsx::App", "routes/Page.tsx::Page")] == 0.95


def test_imports_through_barrel_files_reach_the_real_definition(tmp_path: Path):
    _write(
        tmp_path,
        {
            # two packages define `capitalize`; only the barrel import tells them apart
            "a/utils/capitalize.ts": "export const capitalize = (s: string) => s;\n",
            "a/utils/isValid.ts": "export function isValid(s: string) { return !!s; }\n",
            "a/utils/index.ts": "export * from './capitalize';\nexport { isValid as valid } from './isValid';\n",
            "a/main.ts": (
                "import { capitalize } from './utils';\n"
                "import { valid } from './utils/index';\n"
                "export function run() { return capitalize('x') && valid('y'); }\n"
            ),
            "b/utils/capitalize.ts": "export const capitalize = (s: string) => s.toUpperCase();\n",
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("a/main.ts::run", "a/utils/capitalize.ts::capitalize")] == 0.95
    assert calls[("a/main.ts::run", "a/utils/isValid.ts::isValid")] == 0.95  # renamed on re-export (valid -> isValid)
    assert ("a/main.ts::run", "b/utils/capitalize.ts::capitalize") not in calls


def test_reexport_cycles_terminate(tmp_path: Path):
    _write(
        tmp_path,
        {
            "x/a.ts": "export * from './b';\n",
            "x/b.ts": "export * from './a';\n",
            "x/use.ts": "import { nothing } from './a';\nexport function f() { return nothing(); }\n",
        },
    )
    run_scan(tmp_path)  # must not hang or recurse forever
    assert _edges(tmp_path, "CALLS") == {}


def test_package_module_does_not_resolve_stdlib_imports_to_a_sibling_file(tmp_path: Path):
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/utils/__init__.py": "",
            "pkg/utils/asyncio.py": "async def sleep(seconds):\n    return seconds\n",   # a sibling that merely shares a stdlib name
            "pkg/utils/use.py": "import asyncio\n\nasync def go():\n    await asyncio.sleep(1)\n",
        },
    )
    run_scan(tmp_path)

    assert not any(tgt == "pkg/utils/asyncio.py::sleep" for _, tgt in _edges(tmp_path, "CALLS"))
    assert ("pkg/utils/use.py", "pkg/utils/asyncio.py") not in _edges(tmp_path, "IMPORTS")


def test_script_outside_a_package_still_resolves_sibling_imports(tmp_path: Path):
    _write(
        tmp_path,
        {
            "tools/helper.py": "def f():\n    return 1\n",
            "tools/run.py": "import helper\n\ndef go():\n    return helper.f()\n",
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("tools/run.py::go", "tools/helper.py::f")] == 0.95


def test_logging_method_names_and_external_super_are_not_guessed(tmp_path: Path):
    _write(
        tmp_path,
        {
            "app.py": (
                "class Response:\n    def info(self):\n        return 1\n\n"
                "class Resolver(ExternalBase):\n"
                "    def getHost(self):\n"
                "        return super().getHost()\n"   # external base: must not resolve to this same method
                "    def log(self, logger):\n"
                "        logger.info('x')\n"           # logging.Logger.info, not Response.info
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert ("app.py::Resolver.log", "app.py::Response.info") not in calls
    assert ("app.py::Resolver.getHost", "app.py::Resolver.getHost") not in calls


def test_generic_base_classes_are_resolved_for_inheritance(tmp_path: Path):
    _write(
        tmp_path,
        {
            "base.py": "class Directive:\n    def get_location(self):\n        return 1\n",
            "other.py": "class Other:\n    def get_location(self):\n        return 2\n",  # makes the name ambiguous
            "obj.py": (
                "from base import Directive\n\n"
                "class CObject(Directive[int]):\n"          # subscripted (generic) base
                "    def run(self):\n"
                "        return self.get_location()\n"
            ),
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "INHERITS")[("obj.py::CObject", "base.py::Directive")] == 0.95
    assert _edges(tmp_path, "CALLS")[("obj.py::CObject.run", "base.py::Directive.get_location")] == 0.85


def test_python_reexport_through_package_init_is_followed(tmp_path: Path):
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "from .canvas import chunks\n",
            "pkg/canvas.py": "class chunks:\n    pass\n",
            "pkg/other.py": "def chunks():\n    return 1\n",   # a second `chunks`: only the re-export tells them apart
            "pkg/task.py": (
                "class Task:\n"
                "    def make(self):\n"
                "        from pkg import chunks\n"
                "        return chunks()\n"
            ),
        },
    )
    run_scan(tmp_path)

    assert _edges(tmp_path, "CALLS")[("pkg/task.py::Task.make", "pkg/canvas.py::chunks")] == 0.95


def test_calls_inside_the_first_definition_of_a_repeated_name_keep_their_owner(tmp_path: Path):
    _write(
        tmp_path,
        {
            "m.py": (
                "def _load():\n    return 1\n\n"
                "def _store(v):\n    return v\n\n"
                "class Conf:\n"
                "    @property\n"
                "    def conf(self):\n"
                "        return _load()\n"              # first definition of `conf`
                "\n"
                "    @conf.setter\n"
                "    def conf(self, value):\n"
                "        _store(value)\n"               # second definition, same qualified name
            ),
        },
    )
    run_scan(tmp_path)
    calls = _edges(tmp_path, "CALLS")

    assert calls[("m.py::Conf.conf", "m.py::_load")] == 0.90   # was lost: only the last definition's lines were known
    assert calls[("m.py::Conf.conf", "m.py::_store")] == 0.90
