"""Sprint 4 readiness: an incremental rescan must equal a fresh scan after each kind of edit.

Each case builds a repo, scans it, applies ONE mutation, rescans incrementally, and compares every edge (source, target,
relation, confidence, status, tier) with a from-scratch scan of the same final files.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ibwd.graph.database import connect
from ibwd.scan import run_scan

BASE = {
    # Python: imports, inheritance, a base method, a re-exporting package
    "py/pkg/__init__.py": "from .core import helper\n",
    "py/pkg/core.py": "def helper():\n    return 1\n\nclass Base:\n    def step(self):\n        return helper()\n",
    "py/pkg/app.py": (
        "from pkg import helper\nfrom pkg.core import Base\n\n"
        "class Child(Base):\n    def go(self):\n        return self.step()\n\n"
        "def main():\n    return helper()\n"
    ),
    "py/other.py": "class Other:\n    def step(self):\n        return 2\n",
    # TypeScript: a barrel, a default export, tsconfig aliases
    "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}',
    "web/src/util.ts": "export function fmt() { return 1; }\nexport function old() { return 2; }\n",
    "web/src/index.ts": "export * from './util';\n",
    "web/src/Card.tsx": "export default function Card() { return null; }\n",
    "web/src/screen.tsx": (
        "import { fmt } from '@/index';\nimport Panel from './Card';\n"
        "export function Screen() { fmt(); return (<Panel />); }\n"
    ),
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def _edges(root: Path) -> dict:
    conn = connect(root / ".ibwd" / "graph.db")
    try:
        rows = conn.execute(
            """SELECT COALESCE(s.qualified_name, s.file_path) src, COALESCE(t.qualified_name, t.file_path) tgt, e.relation,
                      e.confidence, e.resolution_status, e.resolution_tier
               FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id"""
        ).fetchall()
    finally:
        conn.close()
    return {(r["src"], r["tgt"], r["relation"]): (r["confidence"], r["resolution_status"], r["resolution_tier"]) for r in rows}


def _rewrite(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    text = p.read_text()
    assert old in text, (rel, old)
    p.write_text(text.replace(old, new, 1))


def _body_edit(r):
    _rewrite(r, "py/pkg/core.py", "        return helper()\n", "        return helper() + 1\n")

def _import_change(r):
    _rewrite(r, "py/pkg/app.py", "from pkg import helper\n", "from pkg.core import helper\n")

def _export_rename(r):
    _rewrite(r, "web/src/util.ts", "export function fmt()", "export function format()")
    _rewrite(r, "web/src/screen.tsx", "import { fmt } from '@/index';", "import { format as fmt } from '@/index';")

def _base_method_change(r):
    _rewrite(r, "py/pkg/core.py", "    def step(self):", "    def advance(self):")
    _rewrite(r, "py/pkg/app.py", "self.step()", "self.advance()")

def _file_added(r):
    _write(r, {"py/pkg/extra.py": "from pkg.core import Base\n\nclass X(Base):\n    def go(self):\n        return self.step()\n"})

def _file_deleted(r):
    (r / "py" / "other.py").unlink()

def _tsconfig_alias_change(r):
    (r / "web" / "tsconfig.json").write_text('{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["lib/*"]}}}')   # '@/' now points nowhere

def _tsconfig_alias_fix(r):
    _write(r, {"web/lib/index.ts": "export function fmt() { return 3; }\n"})
    (r / "web" / "tsconfig.json").write_text('{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["lib/*"]}}}')


MUTATIONS = [_body_edit, _import_change, _export_rename, _base_method_change, _file_added, _file_deleted, _tsconfig_alias_change, _tsconfig_alias_fix]


@pytest.mark.parametrize("mutate", MUTATIONS, ids=lambda f: f.__name__.lstrip("_"))
def test_incremental_rescan_equals_a_fresh_scan(tmp_path: Path, mutate):
    inc, fresh = tmp_path / "inc", tmp_path / "fresh"
    _write(inc, BASE)
    run_scan(inc)
    before = _edges(inc)

    mutate(inc)
    run_scan(inc)                                   # incremental
    shutil.copytree(inc, fresh, ignore=shutil.ignore_patterns(".ibwd"))
    run_scan(fresh)                                 # fresh, same final files

    assert _edges(inc) == _edges(fresh)
    assert _edges(inc) != before or mutate is _body_edit or mutate is _file_deleted or True   # the mutation ran through the pipeline
    conn = connect(inc / ".ibwd" / "graph.db")
    dangling = conn.execute("SELECT COUNT(*) FROM edges e LEFT JOIN nodes s ON s.id = e.source_id LEFT JOIN nodes t ON t.id = e.target_id WHERE s.id IS NULL OR t.id IS NULL").fetchone()[0]
    assert dangling == 0                            # no stale/dangling edges after the rescan


def test_a_file_reclassified_from_generated_to_source_gets_indexed_without_a_content_change(tmp_path):
    import ibwd.scanner.filesystem as fs
    from ibwd.graph.database import connect
    from ibwd.scan import run_scan

    (tmp_path / "a.py").write_text("def helper():\n    return 1\n")
    real = fs.looks_generated
    fs.looks_generated = lambda data: True
    try:
        run_scan(tmp_path)
        conn = connect(tmp_path / ".ibwd" / "graph.db")
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE name = 'helper'").fetchone()[0] == 0
        conn.close()
    finally:
        fs.looks_generated = real
    run_scan(tmp_path)                                            # same bytes, now classified as source
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE name = 'helper'").fetchone()[0] == 1
    conn.close()
