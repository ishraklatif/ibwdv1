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
EDGE_BUILD_VERSION = 11

REFERENCE_RELATIONS = ("IMPORTS", "CALLS", "INHERITS", "REFERENCES")

CONF_IMPORT_MAP = 0.95
CONF_SAME_MODULE = 0.90
CONF_UNIQUE_NAME = 0.75
CONF_SUFFIX = 0.55
CONF_FUZZY = 0.35
CONF_INHERITED = 0.85  # self.x()/super().x() found on a base class (between same-module and unique-name)

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


def _inherited_method(
    file_path: str,
    class_qualname: str,
    name: str,
    index: SymbolIndex,
    bases_of: dict[int, list[Sym]],
    include_self: bool,
) -> Sym | None:
    """Search a class's resolved bases (breadth-first, cycle-safe) for a method called `name`."""
    start = index.by_qual.get((file_path, class_qualname))
    if start is None or start.node_type != "Class":
        return None
    seen = {start.id}
    queue: list[Sym] = [start] if include_self else list(bases_of.get(start.id, ()))
    depth = 0
    while queue and depth < 12:
        depth += 1
        nxt: list[Sym] = []
        for cls in queue:
            if cls.id in seen and cls.id != start.id:
                continue
            seen.add(cls.id)
            method = index.by_qual.get((cls.file_path, f"{cls.qualname}.{name}"))
            if method is not None:
                return method
            nxt.extend(b for b in bases_of.get(cls.id, ()) if b.id not in seen)
        queue = nxt
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
    bases_of: dict[int, list[Sym]] | None = None,
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
        binding = bindings.get(receiver)
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
            if bases_of:
                sym = _inherited_method(file_path, class_qualname, name, index, bases_of, include_self=False)
                if ok(sym):
                    return sym, CONF_INHERITED
    elif receiver != UNKNOWN_RECEIVER:
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
    shape = None if generic_method else _name_shape(name)
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
) -> tuple[dict[str, ResolvedBinding], list[tuple[tuple[int, int], dict[str, ResolvedBinding]]]]:
    """Resolve a file's imports: emit IMPORTS edges, return (file-level bindings, function-local bindings).

    A function-local import only binds its names inside that function; treating it as file-wide made it
    shadow a same-named module-level function everywhere (seen in Sphinx).
    """
    bindings: dict[str, ResolvedBinding] = {}
    local: dict[tuple[int, int], dict[str, ResolvedBinding]] = {}
    is_python = language_of(path) == "python"

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

    edges: dict[tuple[int, int, str], float] = {}

    def add_edge(source_id: int, target_id: int, relation: str, confidence: float) -> None:
        key = (source_id, target_id, relation)
        if confidence > edges.get(key, 0.0):
            edges[key] = confidence

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
        ))

    # Pass 2: inheritance, before any calls, so self.x()/super().x() can walk to base classes
    bases_of: dict[int, list[Sym]] = defaultdict(list)
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
                bases_of[cls.id].append(resolved[0])

    # Pass 3: calls
    for path, refs in parsed.items():
        file_id = file_ids[path]
        for call in refs.calls:
            owner = index.innermost(path, call.line)
            resolved = resolve_ref(
                call.name,
                call.receiver,
                file_path=path,
                owner_qualname=owner.qualname if owner else None,
                bindings=bindings_by_file[path].at(call.line),
                index=index,
                bases_of=bases_of,
            )
            if resolved:
                target, conf = resolved
                add_edge(owner.id if owner else file_id, target.id, "CALLS", conf)

        # Pass 4: functions used as values -> REFERENCES (never via the loose name tiers)
        for ref in refs.value_refs:
            owner = index.innermost(path, ref.line)
            resolved = resolve_ref(
                ref.name,
                ref.receiver,
                file_path=path,
                owner_qualname=owner.qualname if owner else None,
                bindings=bindings_by_file[path].at(ref.line),
                index=index,
                bases_of=bases_of,
                value_mode=True,
            )
            if resolved and (owner is None or resolved[0].id != owner.id):
                add_edge(owner.id if owner else file_id, resolved[0].id, "REFERENCES", resolved[1])

    # Write only the difference from what's already stored (most rescans change few edges).
    placeholders = ",".join("?" * len(REFERENCE_RELATIONS))
    existing = {
        (row["source_id"], row["target_id"], row["relation"]): row["confidence"]
        for row in conn.execute(
            f"SELECT source_id, target_id, relation, confidence FROM edges WHERE relation IN ({placeholders})",
            REFERENCE_RELATIONS,
        )
    }
    conn.executemany(
        "DELETE FROM edges WHERE source_id = ? AND target_id = ? AND relation = ?",
        [key for key in existing if key not in edges],
    )
    for (source_id, target_id, relation), confidence in edges.items():
        if existing.get((source_id, target_id, relation)) != confidence:
            upsert_edge(conn, source_id, target_id, relation, confidence, "static_analysis")
    counts = dict.fromkeys(REFERENCE_RELATIONS, 0)
    for _, _, relation in edges:
        counts[relation] += 1

    conn.execute(f"PRAGMA user_version = {EDGE_BUILD_VERSION}")
    conn.commit()
    return counts
