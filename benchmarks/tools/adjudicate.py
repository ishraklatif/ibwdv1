#!/usr/bin/env python3
"""Write an evidenced adjudication record for every disagreement that needs one (Python repositories).

For each resolved false positive (any relation) and each supported-scope false negative in `<repo>_relations.json` this tool
tries a fixed list of *category detectors*. A detector only fires when it can verify its condition against the actual source
(ast) and the raw SCIP occurrences, and it returns the evidence it checked. Anything no detector can verify becomes
`unresolved_disagreement` - it is never counted as proven correctness.

Record: {repo, repo_sha, source, target, relation, file, line, kind, verdict, category, explanation, evidence}
Verdicts: ibwd_defect | oracle_error | legitimate_out_of_scope | unresolved_disagreement

Usage: adjudicate.py REPO_DIR MANIFEST RELATIONS.json SCIP_INDEX PROTO_DIR OUT.json   (run with the oracle venv: needs protobuf)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[5] if len(sys.argv) > 5 else ".")
import scip_pb2  # noqa: E402


def load(repo: Path, manifest: dict, relations: dict, scip_path: str):
    index = scip_pb2.Index()
    index.ParseFromString(Path(scip_path).read_bytes())
    return {d.relative_path: d for d in index.documents}


class Ctx:
    def __init__(self, repo: Path, docs: dict):
        self.repo, self.docs, self._src, self._tree = repo, docs, {}, {}

    def lines(self, file: str) -> list[str]:
        if file not in self._src:
            self._src[file] = (self.repo / file).read_text(encoding="utf-8", errors="replace").splitlines()
        return self._src[file]

    def tree(self, file: str) -> ast.Module:
        if file not in self._tree:
            self._tree[file] = ast.parse("\n".join(self.lines(file)))
        return self._tree[file]

    def find_def(self, file: str, qual: str):
        """The def/class node for a dotted qualname; looks through module/class-level if/try blocks."""
        def flat(nodes):
            for n in nodes:
                yield n
                if isinstance(n, (ast.If, ast.Try, ast.With)):
                    yield from flat(n.body)
                    yield from flat(getattr(n, "orelse", []))
                    yield from flat(getattr(n, "finalbody", []))
                    for h in getattr(n, "handlers", []):
                        yield from flat(h.body)

        nodes, node = self.tree(file).body, None
        for part in qual.split("."):
            node = next((n for n in flat(nodes) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part), None)
            if node is None:
                return None
            nodes = node.body
        return node

    def scip_at(self, file: str, line: int, name: str) -> list[str]:
        """SCIP symbols whose occurrence covers `name` on this line (UTF-16 columns)."""
        doc, text = self.docs.get(file), self.lines(file)[line - 1]
        out = []
        for m in re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text):
            c0 = len(text[: m.start()].encode("utf-16-le")) // 2
            out += [o.symbol for o in (doc.occurrences if doc else []) if o.range[0] == line - 1 and o.range[1] == c0]
        return out


def sites(ctx: Ctx, file: str, qual: str | None, simple: str) -> list[tuple[int, str]]:
    """Lines inside the source symbol (or the whole file for a module-level source) that mention `simple`."""
    lines = ctx.lines(file)
    lo, hi = 1, len(lines)
    if qual:
        node = ctx.find_def(file, qual)
        if node is None:
            return []
        lo, hi = node.lineno, node.end_lineno
    return [(i, lines[i - 1].strip()) for i in range(lo, hi + 1) if re.search(r"(?<![A-Za-z0-9_])" + re.escape(simple) + r"(?![A-Za-z0-9_])", lines[i - 1])]


def split_id(sid: str) -> tuple[str, str | None]:
    f, _, q = sid.partition("::")
    return f, (q or None)


# --------------------------------------------------------------------------- detectors: each returns (verdict, category, explanation, evidence, file, line) or None


def d_submodule_import(ctx, x, repo):
    if x["relation"] != "IMPORTS":
        return None
    src, tgt = x["source"], x["target"]
    tree, hit = ctx.tree(src), None
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            pkg = Path(src).parent
            for _ in range(max(n.level - 1, 0)):
                pkg = pkg.parent
            base = pkg if n.level else Path(*(n.module or "").split("."))
            if n.level and n.module:
                base = base / Path(*n.module.split("."))
            for a in n.names:
                cand = [str(base / f"{a.name}.py"), str(base / a.name / "__init__.py")]
                if tgt in cand:
                    init = ctx.repo / base / "__init__.py"
                    bound = False
                    if init.exists():
                        for top in ast.walk(ast.parse(init.read_text(encoding="utf-8", errors="replace"))):
                            if isinstance(top, (ast.FunctionDef, ast.ClassDef)) and top.name == a.name:
                                bound = True
                            elif isinstance(top, ast.Assign) and any(isinstance(t, ast.Name) and t.id == a.name for t in top.targets):
                                bound = True
                    if not bound:
                        hit = (n.lineno, f"from {'.' * n.level}{n.module or ''} import {a.name}", str(base / '__init__.py'))
    if hit is None:
        return None
    return ("oracle_error", "submodule_import",
            "`from pkg import sub` imports the submodule file when `sub` is not bound in the package's __init__ (Python import semantics); "
            "the oracle records only an edge to the package __init__.",
            {"import_statement": hit[1], "package_init": hit[2], "name_bound_in_package_init": False, "target_is_submodule_file": True}, src, hit[0])


def scip_at_col(ctx: Ctx, file: str, line: int, col: int) -> list[str]:
    text, doc = ctx.lines(file)[line - 1], ctx.docs.get(file)
    c0 = len(text[:col].encode("utf-16-le")) // 2
    return [o.symbol for o in (doc.occurrences if doc else []) if o.range[0] == line - 1 and o.range[1] == c0]


def d_builtin_name_collision(ctx, x, repo):
    """`self.set(...)` / `self.type`: scip-python reports builtins/<name># for an attribute that is a method of the class."""
    if x["relation"] not in ("CALLS", "REFERENCES") or x["kind"] != "FP_resolved":
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    if not tq or "." not in tq or sf != tf:
        return None
    simple = tq.split(".")[-1]
    for line, text in sites(ctx, sf, sq, simple):
        for m in re.finditer(r"\bself\." + re.escape(simple) + r"\b", text):
            col = ctx.lines(sf)[line - 1].index(text) + m.start() + len("self.")
            syms = scip_at_col(ctx, sf, line, col)
            if syms and all(f"builtins/{simple}#" in s for s in syms):
                return ("oracle_error", "scip_builtin_name_collision",
                        f"`self.{simple}` is a member of the class (`{tq}` is defined in the same class) but scip-python reports the builtin `{simple}` type for the attribute.",
                        {"source_line": f"{sf}:{line}: {text}", "scip_symbol": syms[0][-60:], "class_defines": tq}, sf, line)
    return None


def d_class_named_like_builtin(ctx, x, repo):
    """A class named like a builtin (`class TimeoutError`, `class Warning`): scip-python emits the BUILTIN symbol at the class definition."""
    if x["relation"] not in ("CALLS", "REFERENCES") or x["kind"] != "FP_resolved":
        return None
    tf, tq = split_id(x["target"])
    if not tq or "." in tq:
        return None
    node = ctx.find_def(tf, tq)
    if node is None or not isinstance(node, ast.ClassDef):
        return None
    def_syms = ctx.scip_at(tf, node.lineno, tq)
    if not any(f"builtins/{tq}#" in s for s in def_syms):
        return None
    sf, sq = split_id(x["source"])
    for line, text in sites(ctx, sf, sq, tq):
        syms = ctx.scip_at(sf, line, tq)
        if any(f"/{tq}#" in s and "builtins/" not in s for s in syms):
            return ("oracle_error", "class_named_like_builtin",
                    f"class `{tq}` shadows a builtin name; scip-python emits `builtins/{tq}#` for its definition, so the oracle has no definition to bind the "
                    f"(correctly resolved) use to.",
                    {"definition": f"{tf}:{node.lineno}", "definition_symbol": def_syms[0][-50:], "use": f"{sf}:{line}: {text}", "use_symbol": syms[0][-60:]}, sf, line)
    return None


def d_unreachable_type_checking_else(ctx, x, repo):
    if x["relation"] != "CALLS" or x["kind"] != "FP_resolved":
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    if sq or sf != tf or not tq:
        return None
    for node in ast.walk(ctx.tree(sf)):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for sub in [n for stmt in node.orelse for n in ast.walk(stmt)]:
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == tq:
                    return ("oracle_error", "unreachable_type_checking_else",
                            "the call is in the `else:` branch of `if TYPE_CHECKING:`, the branch that runs at runtime; Pyright treats it as unreachable and reports nothing.",
                            {"call": f"{sf}:{sub.lineno}: {ctx.lines(sf)[sub.lineno - 1].strip()}", "test": f"{sf}:{node.lineno}: if TYPE_CHECKING:"}, sf, sub.lineno)
    return None


def d_type_checking_split_definition(ctx, x, repo):
    """`if TYPE_CHECKING: name: Annotation  else: def name(...)`: the def is the runtime definition; Pyright sees only the annotation."""
    if x["relation"] != "CALLS" or x["kind"] != "FP_resolved":
        return None
    tf, tq = split_id(x["target"])
    if not tq or "." not in tq:
        return None
    cls = ctx.find_def(tf, tq.rsplit(".", 1)[0])
    if cls is None:
        return None
    simple = tq.split(".")[-1]
    for stmt in cls.body:
        if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Name) and stmt.test.id == "TYPE_CHECKING":
            in_else = [n for n in stmt.orelse if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == simple]
            declared = [n for n in stmt.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == simple]
            if in_else and declared:
                sf, sq = split_id(x["source"])
                site = sites(ctx, sf, sq, "self." + simple)
                return ("oracle_error", "type_checking_split_definition",
                        "the method is defined in the `else:` (runtime) branch of `if TYPE_CHECKING:`; the type-checking branch only annotates the name as an "
                        "attribute, so the oracle binds the call to a variable, not to the def that exists at runtime.",
                        {"annotation": f"{tf}:{declared[0].lineno}: {ctx.lines(tf)[declared[0].lineno - 1].strip()}", "runtime_def": f"{tf}:{in_else[0].lineno}",
                         "call": f"{sf}:{site[0][0]}: {site[0][1]}" if site else None}, sf, site[0][0] if site else in_else[0].lineno)
    return None


def d_isinstance_narrowing(ctx, x, repo):
    """`self.m()` after `isinstance(self, Sub)`: Pyright narrows self to a subclass, IBWD binds to the lexically enclosing class."""
    if x["relation"] != "CALLS":
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    if not sq or not tq or "." not in tq or sf != tf:
        return None
    node = ctx.find_def(sf, sq)
    if node is None:
        return None
    subclass = tq.rsplit(".", 1)[0] if x["kind"] == "FN" else None
    narrowed = [c for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "isinstance"
                and c.args and isinstance(c.args[0], ast.Name) and c.args[0].id == "self" and len(c.args) > 1 and isinstance(c.args[1], ast.Name)]
    if not narrowed:
        return None
    names = {c.args[1].id for c in narrowed}
    if x["kind"] == "FN" and subclass not in names:
        return None
    simple = tq.split(".")[-1]
    site = sites(ctx, sf, sq, "self." + simple)
    if not site:
        return None
    return ("legitimate_out_of_scope", "isinstance_narrowing_of_self",
            "the method calls `self.m()` after an `isinstance(self, Sub)` test. Pyright reports the narrowed subclass's method; IBWD binds to the enclosing "
            "class's own method. Both are statically valid targets of dynamic dispatch through `self`; the oracle records only one member of the union.",
            {"isinstance": f"{sf}:{narrowed[0].lineno}: {ctx.lines(sf)[narrowed[0].lineno - 1].strip()}", "call": f"{sf}:{site[0][0]}: {site[0][1]}"}, sf, site[0][0])


def d_class_alias_base(ctx, x, repo):
    if x["relation"] != "INHERITS":
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    cls = ctx.find_def(sf, sq or "")
    if cls is None or not isinstance(cls, ast.ClassDef):
        return None
    for base in cls.bases:
        if isinstance(base, ast.Name):
            assigns = [n for n in ast.walk(ctx.tree(sf)) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == base.id for t in n.targets)]
            values = []
            for a in assigns:
                v = a.value.value if isinstance(a.value, ast.Subscript) else a.value
                values.append(getattr(v, "id", None))
            if assigns and set(values) == {tq.split(".")[-1]}:
                return ("oracle_error", "class_alias_base",
                        f"the base `{base.id}` is a module-level alias of `{tq}` in every branch; the class does inherit from it. The oracle sees a variable, not a class.",
                        {"alias_assignments": [f"{sf}:{a.lineno}: {ctx.lines(sf)[a.lineno - 1].strip()}" for a in assigns], "class": f"{sf}:{cls.lineno}: {ctx.lines(sf)[cls.lineno - 1].strip()}"}, sf, cls.lineno)
    return None


def d_missing_scip_occurrence(ctx, x, repo):
    if x["relation"] != "CALLS" or x["kind"] != "FP_resolved":
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    if not tq or "." not in tq or sf != tf:
        return None
    simple = tq.split(".")[-1]
    if ctx.find_def(tf, tq) is None:
        return None
    for line, text in sites(ctx, sf, sq, simple):
        if f"self.{simple}(" in text and not ctx.scip_at(sf, line, simple):
            return ("oracle_error", "missing_scip_occurrence",
                    f"`self.{simple}()` calls a method defined in the same class, but scip-python emits no occurrence at that position.",
                    {"call": f"{sf}:{line}: {text}", "definition": f"{tf}:{ctx.find_def(tf, tq).lineno}", "scip_occurrences_at_name": 0}, sf, line)
    return None


def d_implicit_class_names(ctx, x, repo):
    """`__doc__` / `__module__` / `__name__` inside a class body: scip-python reports the class symbol for these implicit namespace names."""
    if x["relation"] != "REFERENCES" or x["kind"] != "FN":
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    if not sq:
        return None
    for simple in ("__doc__", "__module__", "__name__", "__qualname__"):
        for line, text in sites(ctx, sf, sq, simple):
            syms = ctx.scip_at(sf, line, simple)
            if syms and tq and (syms[0].endswith("#") or "." in tq):
                return ("oracle_error", "implicit_class_namespace_name",
                        f"`{simple}` in a class body is an implicit namespace name, not a use of the class or of a member named `{simple.strip('_')}`; "
                        "scip-python reports the class symbol for it.",
                        {"site": f"{sf}:{line}: {text}", "scip_symbol": syms[0][-50:]}, sf, line)
    return None


def d_conditional_binding(ctx, x, repo):
    """The name is bound by several statements in different branches (import / def / assignment): no single static target."""
    if x["relation"] not in ("CALLS", "REFERENCES"):
        return None
    sf, sq = split_id(x["source"]); tf, tq = split_id(x["target"])
    if not tq or "." in tq:
        return None
    tree = ctx.tree(tf)
    direct = {id(n) for n in tree.body}
    binders = []   # (line, kind, in_conditional_block)

    def visit(nodes, conditional):
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == tq:
                binders.append((n.lineno, "def", conditional))
            elif isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == tq for t in n.targets):
                binders.append((n.lineno, "assignment", conditional))
            elif isinstance(n, ast.ImportFrom) and any((a.asname or a.name) == tq for a in n.names):
                binders.append((n.lineno, "import", conditional))
            if isinstance(n, (ast.If, ast.Try)):
                visit(n.body, True); visit(getattr(n, "orelse", []), True); visit(getattr(n, "finalbody", []), True)
                for h in getattr(n, "handlers", []):
                    visit(h.body, True)

    visit(tree.body, False)
    kinds = {k for _, k, _ in binders}
    if len(binders) >= 2 and "def" in kinds and len(kinds) >= 2 and any(c for _, _, c in binders):
        site = sites(ctx, sf, sq, tq)
        return ("legitimate_out_of_scope", "conditional_binding",
                f"`{tq}` is bound by more than one statement in different branches ({', '.join(f'{k}@{l}' for l, k, _ in binders)}); which one is bound is decided at "
                "import time, so no single definition is a definite target.",
                {"bindings": [f"{tf}:{l} {k}" for l, k, _ in binders], "site": f"{sf}:{site[0][0]}: {site[0][1]}" if site else None}, sf, site[0][0] if site else binders[0][0])
    return None


DETECTORS = [d_submodule_import, d_builtin_name_collision, d_class_named_like_builtin, d_unreachable_type_checking_else,
             d_type_checking_split_definition, d_isinstance_narrowing, d_class_alias_base, d_missing_scip_occurrence,
             d_implicit_class_names, d_conditional_binding]


def main() -> int:
    repo_dir, manifest_path, rel_path, scip_path, _proto, out_path = sys.argv[1:7]
    repo, manifest = Path(repo_dir), json.loads(Path(manifest_path).read_text())
    relations = json.loads(Path(rel_path).read_text())
    ctx = Ctx(repo, load(repo, manifest, relations, scip_path))
    records, counts = [], {}
    for x in relations["disagreements"]:
        needs = (x["kind"] == "FP_resolved") or (x["kind"] == "FN" and x.get("in_supported_scope"))
        if not needs:
            continue
        result = None
        for det in DETECTORS:
            try:
                result = det(ctx, x, repo)
            except Exception as exc:  # a detector must never crash the run: the record just stays unresolved
                result = None
                print(f"detector {det.__name__} failed on {x['source']} -> {x['target']}: {exc}", file=sys.stderr)
            if result:
                break
        rec = {"repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "source": x["source"], "target": x["target"], "relation": x["relation"],
               "kind": x["kind"], "tier": x.get("tier")}
        if result:
            verdict, category, explanation, evidence, file, line = result
            rec.update(file=file, line=line, verdict=verdict, category=category, explanation=explanation, evidence=evidence)
        else:
            rec.update(file=split_id(x["source"])[0], line=None, verdict="unresolved_disagreement", category="unclassified",
                       explanation="no category detector could verify a cause; investigate individually. NOT counted as correct.", evidence={})
        counts[(rec["verdict"], rec["category"])] = counts.get((rec["verdict"], rec["category"]), 0) + 1
        records.append(rec)
    Path(out_path).write_text(json.dumps({"repo": manifest["repo"], "repo_sha": manifest["repo_sha"], "records": records}, indent=1) + "\n")
    for (verdict, cat), n in sorted(counts.items()):
        print(f"{manifest['repo']:8} {verdict:24} {cat:38} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
