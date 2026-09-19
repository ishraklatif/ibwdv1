"""Turn extracted references into IMPORTS / CALLS / INHERITS edges.

CALLS (and INHERITS) resolution is a 5-tier cascade — the first tier that
yields a match wins, and the tier's confidence is stored on the edge:

    1. import-map match     0.95   name/receiver bound by an import to a repo file
    2. same-module match    0.90   defined in the same file / same class (self.x)
    3. unique-name match    0.75   exactly one symbol of that name in the repo
    4. suffix match         0.55   name matches and the receiver looks like its class/module
    5. fuzzy match          0.35   name matches ignoring case/underscores, exactly one hit

Confidence reflects how sure we are of the *resolution*; every edge is still
source_type=static_analysis because it's derived deterministically from source.
Ambiguous matches (several equally-good candidates) are left unresolved rather
than guessed.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ibwd.graph.database import upsert_edge
from ibwd.graph.modules import ModuleResolver, language_of
from ibwd.scanner.references import UNKNOWN_RECEIVER, FileReferences, ImportRef, extract_references

# Bump when extraction/resolution logic changes so existing graphs get rebuilt
# on the next scan (stored in the DB's PRAGMA user_version).
EDGE_BUILD_VERSION = 2

REFERENCE_RELATIONS = ("IMPORTS", "CALLS", "INHERITS")

CONF_IMPORT_MAP = 0.95
CONF_SAME_MODULE = 0.90
CONF_UNIQUE_NAME = 0.75
CONF_SUFFIX = 0.55
CONF_FUZZY = 0.35

_SELF_RECEIVERS = {"self", "cls", "this"}
_CALL_KINDS = frozenset({"Function", "Method", "Class"})
_CLASS_ONLY = frozenset({"Class"})


@dataclass(frozen=True)
class Sym:
    id: int
    node_type: str  # Class | Function | Method
    name: str
    qualname: str  # dotted path within the file: "User.greet"
    file_path: str
    start_line: int
    end_line: int

    @property
    def container(self) -> str:
        """Enclosing class name for a method, else the module (file stem) name."""
        if "." in self.qualname:
            return self.qualname.rsplit(".", 2)[-2]
        path = PurePosixPath(self.file_path)
        return path.parent.name if path.stem in ("__init__", "index") else path.stem


@dataclass(frozen=True)
class ResolvedBinding:
    file: str | None  # repo file the binding points at (None = external / unresolved)
    member: str | None  # None = the module itself
    external: bool = False


def _norm(name: str) -> str:
    return name.lower().replace("_", "")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


class SymbolIndex:
    def __init__(self, syms: list[Sym]):
        self.by_name: dict[str, list[Sym]] = defaultdict(list)
        self.by_norm: dict[str, list[Sym]] = defaultdict(list)
        self.by_file: dict[str, list[Sym]] = defaultdict(list)
        self.by_qual: dict[tuple[str, str], Sym] = {}
        for sym in syms:
            self.by_name[sym.name].append(sym)
            self.by_norm[_norm(sym.name)].append(sym)
            self.by_file[sym.file_path].append(sym)
            self.by_qual[(sym.file_path, sym.qualname)] = sym

    def top_level(self, file_path: str, name: str) -> Sym | None:
        return self.by_qual.get((file_path, name))

    def innermost(self, file_path: str, line: int, name: str | None = None, node_type: str | None = None) -> Sym | None:
        best: Sym | None = None
        for sym in self.by_file.get(file_path, ()):
            if not sym.start_line <= line <= sym.end_line:
                continue
            if name is not None and sym.name != name:
                continue
            if node_type is not None and sym.node_type != node_type:
                continue
            if best is None or (sym.end_line - sym.start_line) < (best.end_line - best.start_line):
                best = sym
        return best


def _unique(cands: list[Sym]) -> Sym | None:
    return cands[0] if len(cands) == 1 else None


def resolve_ref(
    name: str,
    receiver: str | None,
    *,
    file_path: str,
    owner_qualname: str | None,
    bindings: dict[str, ResolvedBinding],
    index: SymbolIndex,
    kinds: frozenset[str] = _CALL_KINDS,
) -> tuple[Sym, float] | None:
    """Resolve one call/base-class reference through the 5-tier cascade."""

    def ok(sym: Sym | None) -> bool:
        return sym is not None and sym.node_type in kinds

    root_binding = bindings.get(receiver.split(".")[0]) if receiver is not None else None

    # -- Tier 1: import map ---------------------------------------------
    if receiver is None:
        binding = bindings.get(name)
        if binding is not None:
            if binding.external:
                return None
            if binding.file and binding.member is not None:
                member = name if binding.member == "default" else binding.member
                sym = index.top_level(binding.file, member) or index.top_level(binding.file, name)
                if ok(sym):
                    return sym, CONF_IMPORT_MAP
    else:
        binding = bindings.get(receiver)
        if binding is not None:
            if binding.external:
                return None
            if binding.file and binding.member is None:  # mod.func()
                sym = index.top_level(binding.file, name)
                if ok(sym):
                    return sym, CONF_IMPORT_MAP
            elif binding.file and binding.member is not None:  # ImportedClass.method()
                cls = index.top_level(binding.file, binding.member)
                if cls is not None and cls.node_type == "Class":
                    sym = index.by_qual.get((binding.file, f"{cls.qualname}.{name}"))
                    if ok(sym):
                        return sym, CONF_IMPORT_MAP
        if root_binding is not None and root_binding.external:
            return None  # os.path.join(...) etc. — an external package, never a repo symbol

    # -- Tier 2: same module --------------------------------------------
    if receiver is None:
        sym = index.top_level(file_path, name)
        if ok(sym):
            return sym, CONF_SAME_MODULE
    elif receiver in _SELF_RECEIVERS:
        if owner_qualname and "." in owner_qualname:
            sym = index.by_qual.get((file_path, f"{owner_qualname.rsplit('.', 1)[0]}.{name}"))
            if ok(sym):
                return sym, CONF_SAME_MODULE
    elif receiver != UNKNOWN_RECEIVER:
        sym = index.by_qual.get((file_path, f"{receiver}.{name}"))
        if ok(sym):
            return sym, CONF_SAME_MODULE

    # Dunder methods (__init__, __call__, ...) are too ubiquitous to match by name alone.
    if _is_dunder(name) or name == "constructor":
        return None

    def eligible(sym: Sym) -> bool:
        if sym.node_type not in kinds:
            return False
        if receiver is None:
            return sym.node_type != "Method"  # a bare `foo()` can never be a method call
        # `obj.foo()` can only reach a module-level function when `obj` is a module
        # (an import alias); otherwise it's a method — this is what keeps
        # `@mcp.tool()` from linking to an unrelated top-level `def tool()`.
        return not (sym.node_type == "Function" and root_binding is None)

    # -- Tier 3: unique name in repo ------------------------------------
    sym = _unique([s for s in index.by_name.get(name, ()) if eligible(s)])
    if sym is not None:
        return sym, CONF_UNIQUE_NAME

    # -- Tier 4: suffix (receiver resembles the class / module name) -----
    if receiver is not None and receiver != UNKNOWN_RECEIVER:
        hint = _norm(receiver.split(".")[-1])
        sym = _unique([s for s in index.by_name.get(name, ()) if eligible(s) and _norm(s.container) == hint])
        if sym is not None:
            return sym, CONF_SUFFIX

    # -- Tier 5: fuzzy (case / underscore-insensitive) -------------------
    sym = _unique([s for s in index.by_norm.get(_norm(name), ()) if eligible(s) and s.name != name])
    if sym is not None:
        return sym, CONF_FUZZY

    return None


def _resolve_imports(
    path: str,
    imports: list[ImportRef],
    resolver: ModuleResolver,
    index: SymbolIndex,
    add_import_edge,
) -> dict[str, ResolvedBinding]:
    """Resolve a file's imports: emit IMPORTS edges, return its local-name bindings."""
    bindings: dict[str, ResolvedBinding] = {}
    is_python = language_of(path) == "python"

    for imp in imports:
        target, conf = resolver.resolve(imp.spec, imp.level, path)
        if target and target != path:
            add_import_edge(target, conf)

        for binding in imp.bindings:
            if not is_python:
                bindings[binding.local] = ResolvedBinding(target, binding.member, external=target is None)
                continue

            if binding.member is None:  # import a.b [as c]
                bindings[binding.local] = ResolvedBinding(target, None, external=target is None)
                continue

            # from m import n: n may be a symbol in m or a submodule m.n
            sub_spec = f"{imp.spec}.{binding.member}" if imp.spec else binding.member
            sub, sub_conf = resolver.resolve(sub_spec, imp.level, path)
            if target and index.top_level(target, binding.member) is not None:
                bindings[binding.local] = ResolvedBinding(target, binding.member)
            elif sub:
                if sub != path:
                    add_import_edge(sub, sub_conf)
                bindings[binding.local] = ResolvedBinding(sub, None)
            elif target:
                bindings[binding.local] = ResolvedBinding(target, binding.member)
            else:
                bindings[binding.local] = ResolvedBinding(None, binding.member, external=True)

    return bindings


def rebuild_reference_edges(conn: sqlite3.Connection, repo_root: Path) -> dict[str, int]:
    """Re-derive every IMPORTS/CALLS/INHERITS edge from source. Returns counts by relation.

    Resolution depends on the whole repo's symbols and imports (a change in one
    file can newly resolve a call in another), so this always rebuilds all
    three relations from every supported source file rather than patching.
    """
    file_ids: dict[str, int] = {}
    for row in conn.execute("SELECT id, file_path FROM nodes WHERE node_type = 'File' AND kind = 'source'"):
        if language_of(row["file_path"]) is not None:
            file_ids[row["file_path"]] = row["id"]

    syms: list[Sym] = []
    for row in conn.execute(
        "SELECT id, node_type, name, qualified_name, file_path, start_line, end_line "
        "FROM nodes WHERE node_type IN ('Class', 'Function', 'Method')"
    ):
        if row["file_path"] not in file_ids or not row["qualified_name"]:
            continue
        qualname = row["qualified_name"].split("::", 1)[-1]
        syms.append(
            Sym(row["id"], row["node_type"], row["name"], qualname, row["file_path"], row["start_line"], row["end_line"])
        )
    index = SymbolIndex(syms)
    resolver = ModuleResolver(file_ids)

    edges: dict[tuple[int, int, str], float] = {}

    def add_edge(source_id: int, target_id: int, relation: str, confidence: float) -> None:
        key = (source_id, target_id, relation)
        if confidence > edges.get(key, 0.0):
            edges[key] = confidence

    parsed: dict[str, FileReferences] = {}
    for path in file_ids:
        refs = extract_references(repo_root / path, path)
        if refs is not None:
            parsed[path] = refs

    for path, refs in parsed.items():
        file_id = file_ids[path]
        bindings = _resolve_imports(
            path,
            refs.imports,
            resolver,
            index,
            lambda target, conf, file_id=file_id: add_edge(file_id, file_ids[target], "IMPORTS", conf),
        )

        for call in refs.calls:
            owner = index.innermost(path, call.line)
            resolved = resolve_ref(
                call.name,
                call.receiver,
                file_path=path,
                owner_qualname=owner.qualname if owner else None,
                bindings=bindings,
                index=index,
            )
            if resolved:
                target, conf = resolved
                add_edge(owner.id if owner else file_id, target.id, "CALLS", conf)

        for base in refs.bases:
            cls = index.innermost(path, base.class_line, name=base.class_name, node_type="Class")
            if cls is None:  # class nested in a function — not an indexed symbol
                continue
            resolved = resolve_ref(
                base.name,
                base.receiver,
                file_path=path,
                owner_qualname=None,
                bindings=bindings,
                index=index,
                kinds=_CLASS_ONLY,
            )
            if resolved and resolved[0].id != cls.id:
                add_edge(cls.id, resolved[0].id, "INHERITS", resolved[1])

    placeholders = ",".join("?" * len(REFERENCE_RELATIONS))
    conn.execute(f"DELETE FROM edges WHERE relation IN ({placeholders})", REFERENCE_RELATIONS)
    counts = dict.fromkeys(REFERENCE_RELATIONS, 0)
    for (source_id, target_id, relation), confidence in edges.items():
        upsert_edge(conn, source_id, target_id, relation, confidence, "static_analysis")
        counts[relation] += 1

    conn.execute(f"PRAGMA user_version = {EDGE_BUILD_VERSION}")
    conn.commit()
    return counts
