"""A parameter / local variable / nested def shadows a same-named module-level symbol: calling it is a variable call.

Seen in Celery and Sphinx: `def send(self, utcoffset=utcoffset): utcoffset()`, `setup = getattr(mod, "setup"); setup(app)`.
"""
from __future__ import annotations

from pathlib import Path

from ibwd.graph.database import connect
from ibwd.scan import run_scan

SOURCE = '''\
from lib import imported


def target():
    return 1


def via_param_default(target=target):
    return target()          # the parameter, not the module-level function


def via_local_assignment(mod):
    target = getattr(mod, "target", None)
    return target()          # a local variable


def via_walrus(mod):
    if (imported := mod.x):
        return imported()    # shadows the imported name too


def via_nested_def():
    def target():
        return 2

    return target()          # the nested def


def via_comprehension(items):
    return [target() for target in items]   # the loop variable


def via_global_decl():
    global target
    return target()          # `global` means it is NOT local: still the module-level function


def not_shadowed():
    return target()          # plain module-level call


def other_scope_local():
    target = 3               # local to *this* function only
    return target


def uses_self_still_works():
    return not_shadowed()
'''


def _edges(tmp_path: Path) -> set[tuple[str, str, str]]:
    (tmp_path / "lib.py").write_text("def imported():\n    return 1\n")
    (tmp_path / "app.py").write_text(SOURCE)
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    rows = conn.execute(
        "SELECT s.name AS s, t.name AS t, e.relation AS r, e.resolution_status AS st FROM edges e "
        "JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id WHERE e.relation = 'CALLS' AND e.resolution_status = 'resolved'"
    ).fetchall()
    conn.close()
    return {(r["s"], r["t"], r["r"]) for r in rows}


def test_local_bindings_do_not_resolve_to_module_level_symbols(tmp_path: Path):
    edges = _edges(tmp_path)
    for shadowed in ("via_param_default", "via_local_assignment", "via_walrus", "via_nested_def", "via_comprehension"):
        assert not [e for e in edges if e[0] == shadowed], shadowed


def test_global_declaration_and_plain_calls_still_resolve(tmp_path: Path):
    edges = _edges(tmp_path)
    assert ("via_global_decl", "target", "CALLS") in edges
    assert ("not_shadowed", "target", "CALLS") in edges
    assert ("uses_self_still_works", "not_shadowed", "CALLS") in edges


def test_a_local_in_another_function_does_not_leak(tmp_path: Path):
    edges = _edges(tmp_path)
    assert ("not_shadowed", "target", "CALLS") in edges          # `other_scope_local`'s `target = 3` is not visible here


def test_self_and_method_calls_are_unaffected(tmp_path: Path):
    (tmp_path / "m.py").write_text(
        "class A:\n    def a(self, x):\n        return self.b(x)\n\n    def b(self, x):\n        return x.b()\n"
    )
    run_scan(tmp_path)
    conn = connect(tmp_path / ".ibwd" / "graph.db")
    rows = {(r[0], r[1]) for r in conn.execute(
        "SELECT s.name, t.name FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id WHERE e.relation = 'CALLS'")}
    conn.close()
    assert ("a", "b") in rows                                     # self is never treated as a shadowing local
