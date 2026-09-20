"""Turn extracted references into IMPORTS / CALLS / INHERITS / REFERENCES edges.

CALLS (and INHERITS) resolution is a 5-tier cascade — the first tier that
yields a match wins, and the tier's confidence is stored on the edge:

    1. import-map match     0.95   name/receiver bound by an import to a repo file
    2. same-module match    0.90   defined in the same file / same class (self.x)
    3. unique-name match    0.75   exactly one symbol of that name in the repo
    4. suffix match         0.55   name matches and the receiver looks like its class/module
    5. fuzzy match          0.35   same multi-word name in another style (fetch_data / fetchData), exactly one hit

Between tiers 2 and 3, `self.x()` / `this.x()` that is not on the calling class is looked up on its
resolved base classes, and `super().x()` on the bases only (0.85).

Confidence reflects how sure we are of the *resolution*; every edge is still
source_type=static_analysis because it's derived deterministically from source.
Ambiguous matches (several equally-good candidates) are left unresolved rather
than guessed.
"""

from __future__ import annotations

import sqlite3
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ibwd.graph.database import upsert_edge
from ibwd.graph.modules import ModuleResolver, language_of
from ibwd.scanner.references import (
    UNKNOWN_RECEIVER,
    FileReferences,
    ImportRef,
    extract_references,
    refs_from_json,
    refs_to_json,
)

# Bump when extraction/resolution logic changes so existing graphs get rebuilt
# on the next scan (stored in the DB's PRAGMA user_version).
EDGE_BUILD_VERSION = 16

REFERENCE_RELATIONS = ("IMPORTS", "CALLS", "INHERITS", "REFERENCES")

CONF_IMPORT_MAP = 0.95
CONF_SAME_MODULE = 0.90
CONF_UNIQUE_NAME = 0.75
CONF_SUFFIX = 0.55
CONF_FUZZY = 0.35
CONF_INHERITED = 0.85  # self.x()/super().x() found on a base class (between same-module and unique-name)
# Found on a resolved base class, but an unresolved (external) base precedes it in the class's declaration order, so the method
# may equally come from that base: a hint, not a resolved edge.
CONF_INHERITED_UNCERTAIN = 0.60

# Default edge policy (frozen, benchmarks/SPRINT3_gate_definition.md section 6): import-map, same-module and inherited edges
# are RESOLVED; unique-name and suffix are CANDIDATE hints that default queries never use; fuzzy is DISABLED unless the
# experimental flag IBWD_EXPERIMENTAL_FUZZY=1 is set (then it is created as a candidate).
_TIER_BY_CONFIDENCE = {
    0.95: "import_map", 0.90: "same_module", 0.85: "inherited", 0.75: "unique_name", 0.60: "inherited_uncertain", 0.55: "suffix", 0.35: "fuzzy",
}
CANDIDATE_TIERS = frozenset({"unique_name", "inherited_uncertain", "suffix", "fuzzy"})
# External bases that add no user-defined methods and so never make an inherited lookup uncertain.
_TRANSPARENT_BASES = frozenset({"object", "Generic", "Protocol", "ABC"})


def fuzzy_enabled() -> bool:
    return os.environ.get("IBWD_EXPERIMENTAL_FUZZY") == "1"


def classify_edge(relation: str, confidence: float) -> tuple[str, str]:
    """(resolution_tier, resolution_status) for an edge produced with this relation and heuristic score."""
    if relation == "IMPORTS":
        return "path", "resolved"
    tier = _TIER_BY_CONFIDENCE.get(round(confidence, 2), "unknown")
    return tier, ("candidate" if tier in CANDIDATE_TIERS else "resolved")


_SELF_RECEIVERS = {"self", "cls", "this"}

# Method names that builtins and the standard library define everywhere. `d.items()` or `s.split()` on an
# unknown receiver is overwhelmingly the builtin, so a repo method that merely shares the name must not
# win the unique-name / fuzzy tiers (measured on real repos: 100+ false callers each for pop, findall,
# setdefault, items). The suffix tier (receiver named like the class) can still resolve them.
_COMMON_METHODS = {
    "python": frozenset({
        "keys", "values", "items", "get", "pop", "popitem", "setdefault", "update", "clear", "copy", "fromkeys",
        "append", "extend", "insert", "remove", "index", "count", "sort", "reverse",
        "add", "discard", "union", "intersection", "difference", "issubset", "issuperset",
        "join", "split", "rsplit", "strip", "lstrip", "rstrip", "startswith", "endswith", "replace", "format",
        "encode", "decode", "lower", "upper", "find", "rfind", "splitlines", "partition", "title", "isdigit",
        "read", "write", "close", "open", "readline", "readlines", "flush", "seek", "tell",
        "match", "search", "findall", "finditer", "sub", "subn", "compile", "group", "groups", "fullmatch",
        "put", "get_nowait", "put_nowait", "acquire", "release", "wait", "notify", "start", "stop", "run",
        "debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log",  # logging.Logger
    }),
    "javascript": frozenset({
        "map", "filter", "reduce", "forEach", "find", "findIndex", "some", "every", "includes", "indexOf",
        "push", "pop", "shift", "unshift", "slice", "splice", "concat", "join", "split", "replace", "trim",
        "toString", "keys", "values", "entries", "then", "catch", "finally", "bind", "call", "apply",
        "has", "get", "set", "add", "delete", "clear", "test", "exec", "match", "startsWith", "endsWith",
        "toLowerCase", "toUpperCase", "sort", "reverse", "flat", "flatMap", "fill", "assign", "parse", "stringify",
    }),
}
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
    # A parameter / local variable / nested def: it shadows any same-named import or module-level symbol on these lines.
    local: bool = False


LOCAL = ResolvedBinding(None, None, local=True)


def _norm(name: str) -> str:
    return name.lower().replace("_", "")


_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _name_shape(name: str) -> tuple[tuple[str, ...], int, int] | None:
    """(lowercase word tokens, leading underscores, trailing underscores), or None if too generic to fuzzy-match.

    `fetch_data` and `fetchData` share the shape (("fetch", "data"), 0, 0). A single-word name (`warning`,
    `get`, `update`) is never fuzzy-matched — case-only or underscore-only differences there are different
    symbols (`logger.warning()` is not the class `Warning`; `d.get()` is not `__get__`).
    """
    stripped = name.strip("_")
    tokens = tuple(w.lower() for w in _WORD.findall(stripped))
    if len(tokens) < 2:
        return None
    return tokens, len(name) - len(name.lstrip("_")), len(name) - len(name.rstrip("_"))


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


class SymbolIndex:
    def __init__(self, syms: list[Sym]):
        self.by_name: dict[str, list[Sym]] = defaultdict(list)
        self.by_norm: dict[str, list[Sym]] = defaultdict(list)
        self.by_shape: dict[tuple, list[Sym]] = defaultdict(list)
        self.by_file: dict[str, list[Sym]] = defaultdict(list)
        self.by_qual: dict[tuple[str, str], Sym] = {}
        for sym in syms:
            self.by_name[sym.name].append(sym)
            self.by_norm[_norm(sym.name)].append(sym)
            shape = _name_shape(sym.name)
            if shape is not None:
                self.by_shape[shape].append(sym)
            self.by_file[sym.file_path].append(sym)
            self.by_qual[(sym.file_path, sym.qualname)] = sym

    def top_level(self, file_path: str, name: str) -> Sym | None:
        return self.by_qual.get((file_path, name))

    # file -> [(target file, [(imported, exported), ...] or None for `export *`)]; set by the rebuild
    reexports: dict[str, list[tuple[str, list[tuple[str, str]] | None]]] = {}
    # Python: file -> its module-level import bindings, so `from pkg import name` can follow `pkg/__init__.py`'s own import
    py_bindings: dict[str, dict] = {}
    # file -> [(qualname, start, end)] for every definition, including repeated qualnames (property setters, overloads)
    ranges: dict[str, list[tuple[str, int, int]]] = {}

    def owner_of(self, file_path: str, line: int) -> "Sym | None":
        """The innermost indexed symbol whose definition contains `line` (every definition counts, not just the last)."""
        best: tuple[int, str] | None = None
        for qualname, start, end in self.ranges.get(file_path, ()):
            if start <= line <= end and (best is None or end - start < best[0]):
                best = (end - start, qualname)
        return self.by_qual.get((file_path, best[1])) if best else None

    def exported(self, file_path: str, name: str, _seen: frozenset[str] = frozenset()) -> Sym | None:
        """`name` as importable from `file_path`: defined there, or re-exported (barrel files) from elsewhere."""
        sym = self.top_level(file_path, name)
        if sym is not None or file_path in _seen or len(_seen) > 8:
            return sym
        seen = _seen | {file_path}
        for target, names in self.reexports.get(file_path, ()):
            if names is None:  # export * from './x'
                found = self.exported(target, name, seen)
            else:
                found = next((self.exported(target, imported, seen) for imported, exp in names if exp == name), None)
            if found is not None:
                return found
        binding = self.py_bindings.get(file_path, {}).get(name)  # Python: `from .canvas import chunks` in __init__.py
        if binding is not None and binding.file and binding.member and binding.file != file_path:
            return self.exported(binding.file, binding.member, seen)
        return None

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


class _Opaque:
    """An unresolved (external) base class in a linearization: unknown, and never equal to any other base."""

    __slots__ = ()


class ClassHierarchy:
    """Declared bases per class (in source order; an unresolved external base is None) and their C3 linearization."""

    def __init__(self) -> None:
        self.bases: dict[int, list[Sym | None]] = defaultdict(list)
        self._mro: dict[int, list] = {}

    def mro(self, cls: Sym, _visiting: frozenset[int] = frozenset()) -> list:
        """C3 method resolution order starting at `cls`; external bases appear as `_Opaque` tokens."""
        cached = self._mro.get(cls.id)
        if cached is not None:
            return cached
        if cls.id in _visiting:  # inheritance cycle: stop here
            return [cls]
        visiting = _visiting | {cls.id}
        parents = [base if base is not None else _Opaque() for base in self.bases.get(cls.id, ())]
        seqs = [self.mro(p, visiting) if isinstance(p, Sym) else [p] for p in parents]
        seqs = [list(seq) for seq in [*seqs, parents] if seq]
        merged: list = [cls]
        while seqs:
            head = next((seq[0] for seq in seqs if not any(seq[0] in other[1:] for other in seqs)), None)
            if head is None:  # an inconsistent hierarchy Python itself would reject: fall back to declaration order
                head = seqs[0][0]
            merged.append(head)
            for seq in seqs:
                if seq[0] == head:
                    del seq[0]
            seqs = [seq for seq in seqs if seq]
        self._mro[cls.id] = merged
        return merged


def _inherited_method(
    file_path: str,
    class_qualname: str,
    name: str,
    index: SymbolIndex,
    hierarchy: ClassHierarchy,
    include_self: bool,
) -> tuple[Sym, bool] | None:
    """The first class in the MRO after (or, with include_self, at) this class that defines `name`.

    Returns (method, uncertain). `uncertain` is True when an unresolved (external) base precedes the defining class in the
    MRO: that base may define the method itself (`class Node(docutils.Element, Mixin)`), so the mixin's method is only a
    possibility.
    """
    start = index.by_qual.get((file_path, class_qualname))
    if start is None or start.node_type != "Class":
        return None
    uncertain = False
    for token in hierarchy.mro(start)[0 if include_self else 1:]:
        if isinstance(token, _Opaque):
            uncertain = True
            continue
        method = index.by_qual.get((token.file_path, f"{token.qualname}.{name}"))
        if method is not None:
            return method, uncertain
    return None


def resolve_ref(
    name: str,
    receiver: str | None,
    *,
    file_path: str,
    owner_qualname: str | None,
    bindings: dict[str, ResolvedBinding],
    index: SymbolIndex,
    kinds: frozenset[str] = _CALL_KINDS,
    hierarchy: ClassHierarchy | None = None,
    value_mode: bool = False,
) -> tuple[Sym, float] | None:
    """Resolve one call/base-class reference through the 5-tier cascade.

    value_mode (a function used as a value, not called) stops after the import-map / same-module /
    inheritance tiers: matching a mere *name* elsewhere in the repo is far too loose for something
    that may just be a variable.
    """

    def ok(sym: Sym | None) -> bool:
        return sym is not None and sym.node_type in kinds

    root_binding = bindings.get(receiver.split(".")[0]) if receiver is not None else None
    receiver_is_local = root_binding is not None and root_binding.local and receiver not in _SELF_RECEIVERS
    if receiver_is_local:
        root_binding = None

    # -- Tier 0: a local binding shadows everything ----------------------------
    # `def f(cb=cb): cb()` / `setup = getattr(mod, "setup"); setup(app)`: the callee is a variable, so no module-level symbol
    # (or unique-name guess) is a provable target. Value flow is out of scope; the edge would be a possible target at best.
    if receiver is None:
        local_binding = bindings.get(name)
        if local_binding is not None and local_binding.local:
            return None

    # -- Tier 1: import map ---------------------------------------------
    if receiver is None:
        binding = bindings.get(name)
        if binding is not None:
            if binding.external:
                return None
            if binding.file and binding.member is not None:
                member = name if binding.member == "default" else binding.member
                sym = index.exported(binding.file, member) or index.exported(binding.file, name)
                if ok(sym):
                    return sym, CONF_IMPORT_MAP
    else:
        binding = None if receiver_is_local else bindings.get(receiver)
        if binding is not None:
            if binding.external:
                return None
            if binding.file and binding.member is None:  # mod.func()
                sym = index.exported(binding.file, name)
                if ok(sym):
                    return sym, CONF_IMPORT_MAP
            elif binding.file and binding.member is not None:  # ImportedClass.method()
                cls = index.exported(binding.file, binding.member)
                if cls is not None and cls.node_type == "Class":
                    sym = index.by_qual.get((cls.file_path, f"{cls.qualname}.{name}"))
                    if ok(sym):
                        return sym, CONF_IMPORT_MAP
        if root_binding is not None and root_binding.external:
            return None  # os.path.join(...) etc. — an external package, never a repo symbol

    # -- Tier 2: same module --------------------------------------------
    if receiver is None:
        sym = index.top_level(file_path, name)
        if ok(sym):
            return sym, CONF_SAME_MODULE
    elif receiver in _SELF_RECEIVERS or receiver == "super":
        if owner_qualname and "." in owner_qualname:
            class_qualname = owner_qualname.rsplit(".", 1)[0]
            if receiver != "super":  # own class first; super() skips straight to the bases
                sym = index.by_qual.get((file_path, f"{class_qualname}.{name}"))
                if ok(sym):
                    return sym, CONF_SAME_MODULE
            if hierarchy is not None:
                found = _inherited_method(file_path, class_qualname, name, index, hierarchy, include_self=False)
                if found is not None and ok(found[0]):
                    return found[0], (CONF_INHERITED_UNCERTAIN if found[1] else CONF_INHERITED)
    elif receiver != UNKNOWN_RECEIVER and not receiver_is_local:
        sym = index.by_qual.get((file_path, f"{receiver}.{name}"))
        if ok(sym):
            return sym, CONF_SAME_MODULE

    if value_mode:
        return None

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

    # `super().x()` is decided by the resolved base classes above. If they don't define x (an external base such as
    # a framework class), guessing by name would land on an unrelated repo symbol — or on the calling method itself.
    if receiver == "super":
        return None

    # A builtin-looking method name on a non-self receiver: don't guess by name alone (tiers 3 and 5).
    generic_method = (
        receiver is not None
        and receiver not in _SELF_RECEIVERS
        and receiver != "super"
        and name in _COMMON_METHODS.get(language_of(file_path) or "", ())
    )

    # -- Tier 3: unique name in repo ------------------------------------
    if not generic_method:
        sym = _unique([s for s in index.by_name.get(name, ()) if eligible(s)])
        if sym is not None:
            return sym, CONF_UNIQUE_NAME

    # -- Tier 4: suffix (receiver resembles the class / module name) -----
    if receiver is not None and receiver != UNKNOWN_RECEIVER:
        hint = _norm(receiver.split(".")[-1])
        sym = _unique([s for s in index.by_name.get(name, ()) if eligible(s) and _norm(s.container) == hint])
        if sym is not None:
            return sym, CONF_SUFFIX

    # -- Tier 5: fuzzy (same multi-word name in a different naming style) --
    shape = None if (generic_method or not fuzzy_enabled()) else _name_shape(name)
    if shape is not None:
        sym = _unique([s for s in index.by_shape.get(shape, ()) if eligible(s) and s.name != name])
        if sym is not None:
            return sym, CONF_FUZZY

    return None


def _resolve_imports(
    path: str,
    imports: list[ImportRef],
    resolver: ModuleResolver,
    index: SymbolIndex,
    add_import_edge,
    default_exports: dict[str, str] | None = None,
    local_names: list[tuple[str, int, int]] | None = None,
) -> tuple[dict[str, ResolvedBinding], list[tuple[tuple[int, int], dict[str, ResolvedBinding]]]]:
    """Resolve a file's imports: emit IMPORTS edges, return (file-level bindings, function-local bindings).

    A function-local import only binds its names inside that function; treating it as file-wide made it
    shadow a same-named module-level function everywhere (seen in Sphinx).
    """
    bindings: dict[str, ResolvedBinding] = {}
    local: dict[tuple[int, int], dict[str, ResolvedBinding]] = {}
    is_python = language_of(path) == "python"

    for name, first, last in local_names or ():
        if name not in _SELF_RECEIVERS:
            local.setdefault((first, last), {})[name] = LOCAL

    for imp in imports:
        target, conf = resolver.resolve(imp.spec, imp.level, path)
        if target and target != path:
            add_import_edge(target, conf)
        into = local.setdefault(imp.scope, {}) if imp.scope else bindings

        for binding in imp.bindings:
            if not is_python:
                member = binding.member
                if member == "default" and target and default_exports and target in default_exports:
                    member = default_exports[target]  # `import Card from './Bar'` -> whatever Bar exports by default
                into[binding.local] = ResolvedBinding(target, member, external=target is None)
                continue

            if binding.member is None:  # import a.b [as c]
                into[binding.local] = ResolvedBinding(target, None, external=target is None)
                continue

            # from m import n: n may be a symbol in m or a submodule m.n
            sub_spec = f"{imp.spec}.{binding.member}" if imp.spec else binding.member
            sub, sub_conf = resolver.resolve(sub_spec, imp.level, path)
            if target and index.top_level(target, binding.member) is not None:
                into[binding.local] = ResolvedBinding(target, binding.member)
            elif sub:
                if sub != path:
                    add_import_edge(sub, sub_conf)
                into[binding.local] = ResolvedBinding(sub, None)
            elif target:
                into[binding.local] = ResolvedBinding(target, binding.member)
            else:
                into[binding.local] = ResolvedBinding(None, binding.member, external=True)

    return bindings, sorted(local.items(), key=lambda item: item[0][1] - item[0][0], reverse=True)


class _BindingViews:
    """Effective import bindings at a given line: file-level ones, overridden by any function-local
    imports whose function contains that line (innermost wins)."""

    def __init__(self, file_level: dict[str, ResolvedBinding], local: list):
        self.file_level, self.local, self._cache = file_level, local, {}

    def at(self, line: int) -> dict[str, ResolvedBinding]:
        active = tuple(scope for scope, _ in self.local if scope[0] <= line <= scope[1])
        if not active:
            return self.file_level
        view = self._cache.get(active)
        if view is None:
            view = dict(self.file_level)
            for scope, names in self.local:  # outermost first, so the innermost overrides
                if scope in active:
                    view.update(names)
            self._cache[active] = view
        return view


def rebuild_reference_edges(conn: sqlite3.Connection, repo_root: Path) -> dict[str, int]:
    """Re-derive every IMPORTS/CALLS/INHERITS edge from source. Returns counts by relation.

    Resolution depends on the whole repo's symbols and imports (a change in one
    file can newly resolve a call in another), so this always rebuilds all
    three relations from every supported source file rather than patching.
    """
    file_ids: dict[str, int] = {}
    file_hashes: dict[str, str] = {}
    for row in conn.execute("SELECT id, file_path, content_hash FROM nodes WHERE node_type = 'File' AND kind = 'source'"):
        if language_of(row["file_path"]) is not None:
            file_ids[row["file_path"]] = row["id"]
            file_hashes[row["file_path"]] = row["content_hash"]

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
    resolver = ModuleResolver(file_ids, repo_root)

    edges: dict[tuple[int, int, str], tuple[float, str, str]] = {}

    def add_edge(source_id: int, target_id: int, relation: str, confidence: float) -> None:
        key = (source_id, target_id, relation)
        if key not in edges or confidence > edges[key][0]:
            tier, status = classify_edge(relation, confidence)
            edges[key] = (confidence, tier, status)

    # Extracted references are cached per file (keyed by content hash + extraction version), so only
    # files that actually changed are re-parsed; everything else is loaded from the table.
    cached = {
        row["file_path"]: row
        for row in conn.execute("SELECT file_path, content_hash, version, refs_json FROM file_refs")
    }
    parsed: dict[str, FileReferences] = {}
    to_store: list[tuple[str, str, int, str]] = []
    for path in file_ids:
        row = cached.get(path)
        if row is not None and row["content_hash"] == file_hashes[path] and row["version"] == EDGE_BUILD_VERSION:
            parsed[path] = refs_from_json(row["refs_json"])
            continue
        refs = extract_references(repo_root / path, path)
        if refs is not None:
            parsed[path] = refs
            to_store.append((path, file_hashes[path], EDGE_BUILD_VERSION, refs_to_json(refs)))
    stale_rows = [(path,) for path in cached if path not in file_ids]
    conn.executemany("DELETE FROM file_refs WHERE file_path = ?", stale_rows)
    conn.executemany(
        "INSERT INTO file_refs (file_path, content_hash, version, refs_json) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (file_path) DO UPDATE SET content_hash = excluded.content_hash, "
        "version = excluded.version, refs_json = excluded.refs_json",
        to_store,
    )

    default_exports = {path: refs.default_export for path, refs in parsed.items() if refs.default_export}

    # Barrel files: `export * from './x'` / `export { a } from './x'`, so imports through them reach the real symbol.
    index.reexports = {}
    for path, refs in parsed.items():
        for reexport in refs.reexports:
            target, _ = resolver.resolve(reexport.spec, 0, path)
            if target and target != path:
                index.reexports.setdefault(path, []).append((target, reexport.names))

    # Pass 1: imports (IMPORTS edges + each file's local-name bindings)
    bindings_by_file: dict[str, _BindingViews] = {}
    for path, refs in parsed.items():
        file_id = file_ids[path]
        bindings_by_file[path] = _BindingViews(*_resolve_imports(
            path,
            refs.imports,
            resolver,
            index,
            lambda target, conf, file_id=file_id: add_edge(file_id, file_ids[target], "IMPORTS", conf),
            default_exports,
            refs.local_names,
        ))

    index.py_bindings = {
        path: views.file_level for path, views in bindings_by_file.items() if language_of(path) == "python"
    }
    index.ranges = {path: refs.def_ranges for path, refs in parsed.items()}

    # Pass 2: inheritance, before any calls, so self.x()/super().x() can walk to base classes
    hierarchy = ClassHierarchy()
    for path, refs in parsed.items():
        for base in refs.bases:
            cls = index.innermost(path, base.class_line, name=base.class_name, node_type="Class")
            if cls is None:  # class nested in a function — not an indexed symbol
                continue
            resolved = resolve_ref(
                base.name,
                base.receiver,
                file_path=path,
                owner_qualname=None,
                bindings=bindings_by_file[path].at(base.class_line),
                index=index,
                kinds=_CLASS_ONLY,
            )
            if resolved and resolved[0].id != cls.id:
                add_edge(cls.id, resolved[0].id, "INHERITS", resolved[1])
                hierarchy.bases[cls.id].append(resolved[0])
            elif not resolved and base.name not in _TRANSPARENT_BASES:
                hierarchy.bases[cls.id].append(None)

    def class_scope_member(owner: Sym | None, name: str, line: int) -> tuple[Sym, float] | None:
        """A bare name used directly in a class body resolves to a member defined earlier in that body before any import or
        module-level name (`subtask = signature` after `def signature(...)` refers to the method)."""
        if owner is None or owner.node_type != "Class" or language_of(owner.file_path) != "python":  # JS needs `this.`
            return None
        member = index.by_qual.get((owner.file_path, f"{owner.qualname}.{name}"))
        return (member, CONF_SAME_MODULE) if member is not None and member.start_line < line else None

    # Pass 3: calls
    for path, refs in parsed.items():
        file_id = file_ids[path]
        for call in refs.calls:
            owner = index.owner_of(path, call.line)
            resolved = (call.receiver is None and class_scope_member(owner, call.name, call.line)) or resolve_ref(
                call.name,
                call.receiver,
                file_path=path,
                owner_qualname=owner.qualname if owner else None,
                bindings=bindings_by_file[path].at(call.line),
                index=index,
                hierarchy=hierarchy,
            )
            if resolved:
                target, conf = resolved
                add_edge(owner.id if owner else file_id, target.id, "CALLS", conf)

        # Pass 4: functions used as values -> REFERENCES (never via the loose name tiers)
        for ref in refs.value_refs:
            owner = index.owner_of(path, ref.line)
            resolved = (ref.receiver is None and class_scope_member(owner, ref.name, ref.line)) or resolve_ref(
                ref.name,
                ref.receiver,
                file_path=path,
                owner_qualname=owner.qualname if owner else None,
                bindings=bindings_by_file[path].at(ref.line),
                index=index,
                hierarchy=hierarchy,
                value_mode=True,
            )
            if resolved and (owner is None or resolved[0].id != owner.id):
                add_edge(owner.id if owner else file_id, resolved[0].id, "REFERENCES", resolved[1])

    # Write only the difference from what's already stored (most rescans change few edges).
    placeholders = ",".join("?" * len(REFERENCE_RELATIONS))
    existing = {
        (row["source_id"], row["target_id"], row["relation"]): (row["confidence"], row["resolution_tier"], row["resolution_status"])
        for row in conn.execute(
            f"SELECT source_id, target_id, relation, confidence, resolution_tier, resolution_status FROM edges WHERE relation IN ({placeholders})",
            REFERENCE_RELATIONS,
        )
    }
    conn.executemany(
        "DELETE FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
        [key for key in existing if key not in edges],
    )
    for (source_id, target_id, relation), (confidence, tier, status) in edges.items():
        if existing.get((source_id, target_id, relation)) != (confidence, tier, status):
            upsert_edge(conn, source_id, target_id, relation, confidence, "static_analysis", status, tier)
    counts = dict.fromkeys(REFERENCE_RELATIONS, 0)
    for _, _, relation in edges:
        counts[relation] += 1

    conn.execute(f"PRAGMA user_version = {EDGE_BUILD_VERSION}")
    conn.commit()
    return counts
