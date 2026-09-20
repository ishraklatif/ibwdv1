#!/usr/bin/env python3
"""Build an oracle graph from a scip-python index, classifying every occurrence with Python's own `ast`.

Independent of IBWD's tree-sitter parser: symbol *identity* comes from scip-python (Pyright); the *syntactic role* and the
*lexical owner* of each occurrence come from `ast`. Nothing is inferred from "the next character".

Usage:
  scip_python_to_graph.py INDEX.scip REPO_ROOT MANIFEST.json OUT.json [--proto-dir DIR]

Setup (once, in a venv with `protobuf` and `grpcio-tools`):
  curl -sLO https://raw.githubusercontent.com/scip-code/scip/main/scip.proto
  python -m grpc_tools.protoc -I. --python_out=<DIR> scip.proto          # writes scip_pb2.py into --proto-dir
  scip-python index . --project-name NAME --project-version SHA --output OUT.scip   # in the repo; output elsewhere

Pipeline per reference occurrence:
  SCIP occurrence -> matching ast node -> resolved symbol identity -> syntactic relation -> original lexical owner
  -> Sprint 3 owner projection -> canonical pair

Relations (exactly one per occurrence, or dropped):
  CALLS       the occurrence is the callee of an ast.Call (`f()`, `obj.m()`, `Foo()`, decorator `@f(...)`)
  REFERENCES  a runtime read of a function/method/class that is not the callee (`outer(f)`, `map(f, xs)`, bare `@f`)
  INHERITS    the head of a base-class expression (`class C(Base)`, `Base[T]` -> Base)
  IMPORTS     the module named in an import statement (file -> that module's file)
  TYPE_USE    inside an annotation or a base's type arguments — never a runtime reference
resolution_basis:
  binding        the name is bound lexically: a bare name, `self`/`cls`/`super()`, a module attribute, `Class.method`
  type_declared  the receiver is an expression whose type had to be inferred (`x.m()`, `self.a.b.m()`, `f().m()`); the target
                 is the declared type's member — a POSSIBLE target, not proof of the runtime callee
Positions are converted between SCIP's text encoding and ast's UTF-8 byte offsets, so non-ASCII text before a reference is safe.
Ownership: a class body's own statements belong to the class; `original_owner` is the innermost enclosing def (nested functions keep `outer.<locals>.inner`); `projected_owner` is
the outermost non-nested def (Sprint 3's enclosing-symbol view). A nested *target* is never replaced by its enclosing function.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


def load_index(path: str, proto_dir: str):
    sys.path.insert(0, proto_dir)
    import scip_pb2  # noqa: E402

    index = scip_pb2.Index()
    index.ParseFromString(Path(path).read_bytes())
    return scip_pb2, index


def descriptor_tail(symbol: str) -> str:
    parts = symbol.split(" ", 4)
    return parts[4] if len(parts) == 5 else ""


def scip_col_to_char(line: str, col: int, encoding: str) -> int:
    """SCIP column (UTF-8 bytes or UTF-16 units) -> index into the Python str `line`."""
    if encoding == "UTF16":
        units = 0
        for i, ch in enumerate(line):
            if units >= col:
                return i
            units += 2 if ord(ch) > 0xFFFF else 1
        return len(line)
    if encoding == "UTF8":
        return len(line.encode("utf-8")[:col].decode("utf-8", errors="ignore"))
    return col  # "CHAR": already a character index


def detect_encoding(index, models: dict, declared: str) -> str:
    """Choose the column encoding by checking which one makes occurrences land on their symbol's own name.

    scip-python declares UTF8 but emits UTF-16 code-unit columns; trusting the declaration corrupts every position that
    follows non-ASCII text. Candidates are scored on lines that contain non-ASCII characters (where they differ).
    """
    scores = {"UTF8": [0, 0], "UTF16": [0, 0], "CHAR": [0, 0]}
    for doc in index.documents:
        model = models.get(doc.relative_path)
        if model is None:
            continue
        for occ in doc.occurrences:
            if occ.symbol.startswith("local ") or occ.range[0] >= len(model.lines) or len(occ.range) != 3:
                continue
            text = model.lines[occ.range[0]]
            if text.isascii():
                continue
            tail = descriptor_tail(occ.symbol)
            name = tail.rstrip(".:#)(").rsplit(".", 1)[-1].rsplit("#", 1)[-1].rsplit("/", 1)[-1].strip("`")
            if not name.isidentifier():
                continue
            for enc in scores:
                a = scip_col_to_char(text, occ.range[1], enc) if enc != "CHAR" else occ.range[1]
                b = scip_col_to_char(text, occ.range[2], enc) if enc != "CHAR" else occ.range[2]
                scores[enc][1] += 1
                scores[enc][0] += text[a:b] == name
    best = max(scores, key=lambda e: (scores[e][0] / scores[e][1]) if scores[e][1] else -1)
    return best if scores[best][1] and scores[best][0] else declared


def ast_col_to_char(line: str, byte_col: int) -> int:
    return len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


class FileModel:
    """Everything `ast` can tell us about one file, in (line, char-column) coordinates (1-based lines)."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.defs: list[dict] = []
        self.callee_ends: dict[tuple[int, int], ast.Call] = {}
        self.attribute_ends: dict[tuple[int, int], ast.Attribute] = {}   # end of `.name` -> its Attribute node (any use, not only calls)
        self.annotation_spans: list[tuple[tuple[int, int], tuple[int, int]]] = []
        self.base_heads: dict[tuple[int, int], str] = {}
        self.base_spans: list[tuple[tuple[int, int], tuple[int, int], str]] = []
        self.import_lines: set[int] = set()
        self.import_names: set[str] = set()   # names bound by import statements (module aliases, imported classes/functions)
        self.relative_imports: list[tuple[int, int, int, str]] = []   # (line, level, start_col, dotted module) for `from .x import y`
        self.decorator_ends: set[tuple[int, int]] = set()
        self.tree = None
        try:
            self.tree = ast.parse(text)
        except SyntaxError:
            return
        self._walk(self.tree, [], False, [])

    def _pos(self, lineno: int, byte_col: int) -> tuple[int, int]:
        line = self.lines[lineno - 1] if 0 < lineno <= len(self.lines) else ""
        return lineno, ast_col_to_char(line, byte_col)

    def _span(self, node):
        return self._pos(node.lineno, node.col_offset), self._pos(node.end_lineno, node.end_col_offset)

    def _end(self, node) -> tuple[int, int]:
        return self._pos(node.end_lineno, node.end_col_offset)

    def _add_annotation(self, node):
        if node is not None:
            self.annotation_spans.append(self._span(node))

    def _walk(self, node, prefix: list[str], in_function: bool, chain: list[str]):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                is_class = isinstance(child, ast.ClassDef)
                qual = ".".join([*chain, "<locals>", child.name]) if in_function else ".".join([*prefix, child.name])
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                self.defs.append({"qualname": qual, "start": start, "end": child.end_lineno or child.lineno, "def_line": child.lineno,
                                  "kind": "Class" if is_class else ("Method" if prefix and not in_function else "Function"),
                                  "nested": in_function})
                for d in child.decorator_list:
                    self.decorator_ends.add(self._end(d.func if isinstance(d, ast.Call) else d))
                if is_class:
                    for base in child.bases:
                        head = base.value if isinstance(base, ast.Subscript) else base
                        self.base_heads[self._end(head)] = qual
                        self.base_spans.append((self._span(base)[0], self._span(base)[1], qual))
                    self._walk(child, prefix if in_function else [*prefix, child.name], in_function, chain if in_function else chain)
                else:
                    a = child.args
                    for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                        if arg is not None:
                            self._add_annotation(arg.annotation)
                    self._add_annotation(child.returns)
                    new_chain = [*chain, child.name] if in_function else [*prefix, child.name]
                    self._walk(child, prefix, True, new_chain)
                continue
            if isinstance(child, ast.Attribute):
                self.attribute_ends[self._end(child)] = child
            if isinstance(child, ast.Call):
                # Only a plain name / attribute is the callee occurrence itself. For `(a if c else b)(x)` the expression ends at
                # `b`, which would make `b` look like the callee; branches of a conditional are value uses (possible callees).
                if isinstance(child.func, (ast.Name, ast.Attribute)):
                    self.callee_ends[self._end(child.func)] = child
            elif isinstance(child, ast.AnnAssign):
                self._add_annotation(child.annotation)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                self.import_lines.update(range(child.lineno, (child.end_lineno or child.lineno) + 1))
                for alias in child.names:
                    self.import_names.add(alias.asname or alias.name.split(".")[0])
                if isinstance(child, ast.ImportFrom) and child.level:
                    self.relative_imports.append((child.lineno, child.level, child.col_offset, child.module or ""))
            self._walk(child, prefix, in_function, chain)

    def in_annotation(self, start, end) -> bool:
        return any(a <= start and end <= b for a, b in self.annotation_spans)

    def owners(self, line: int):
        """(original owner def or None, projected owner def or None) for a line.

        A statement inside a function belongs to that function (nested: original = innermost, projected = outermost non-nested).
        A statement directly in a class body belongs to the innermost enclosing class, which Sprint 3 indexes as a symbol (it runs at
        import time). Module-level statements have no owner (the file)."""
        containing = [d for d in self.defs if d["start"] <= line <= d["end"] and d["kind"] != "Class"]
        if not containing:
            classes = [d for d in self.defs if d["start"] <= line <= d["end"] and d["kind"] == "Class"]
            if not classes:
                return None, None
            innermost = min(classes, key=lambda d: d["end"] - d["start"])
            return innermost, innermost
        original = min(containing, key=lambda d: d["end"] - d["start"])
        outer = [d for d in containing if not d["nested"]]
        projected = min(outer, key=lambda d: d["end"] - d["start"]) if outer else original
        return original, projected


def receiver_basis(model: "FileModel", fn: ast.Attribute, lexical_receivers: set) -> str:
    """`binding` when the receiver is lexically known (self/cls, a module alias, a class name, super()); otherwise the target was found
    through an inferred type (`obj.method` with `obj: Base`) and is only a POSSIBLE target."""
    obj = fn.value
    obj_start = (obj.lineno, ast_col_to_char(model.lines[obj.lineno - 1], obj.col_offset))
    if isinstance(obj, ast.Name) and (obj.id in ("self", "cls") or obj_start in lexical_receivers or obj.id in model.import_names):
        return "binding"
    if isinstance(obj, ast.Call) and isinstance(obj.func, ast.Name) and obj.func.id == "super":
        return "binding"
    return "type_declared"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("index"); ap.add_argument("repo"); ap.add_argument("manifest"); ap.add_argument("out")
    ap.add_argument("--proto-dir", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    manifest = json.loads(Path(args.manifest).read_text())
    scope = set(manifest["included_files"])
    scip, index = load_index(args.index, args.proto_dir)
    Definition, WriteAccess = scip.SymbolRole.Definition, scip.SymbolRole.WriteAccess
    declared = "UTF16" if "16" in scip.TextEncoding.Name(index.metadata.text_document_encoding).upper() else "UTF8"

    models = {f: FileModel((repo / f).read_text(encoding="utf-8", errors="replace")) for f in sorted(scope) if f.endswith(".py")}
    encoding = detect_encoding(index, models, declared)
    symbols: list[dict] = []
    for f, m in models.items():
        for d in m.defs:
            symbols.append({"id": f"{f}::{d['qualname']}", "file": f, "line": d["def_line"], "end_line": d["end"], "name": d["qualname"],
                            "kind": d["kind"], "nested": d["nested"]})
        symbols.append({"id": f, "file": f, "line": 1, "name": Path(f).name, "kind": "File"})

    module_def_file: dict[str, str] = {}   # scip module symbol -> the file that defines it (exact; no name mangling)
    canon_by_def: dict[str, str] = {}
    nested_canon: set[str] = set()   # symbols whose current definition is a nested function
    nested_seen: set[str] = set(); plain_seen: set[str] = set(); nested_target: dict[str, str] = {}
    for doc in index.documents:
        rel = doc.relative_path
        model = models.get(rel)
        if not model:
            continue
        by_line = {}
        for d in model.defs:
            by_line.setdefault(d["def_line"], d)
        for occ in doc.occurrences:
            if occ.symbol.startswith("local ") or not occ.symbol_roles & Definition:
                continue
            tail = descriptor_tail(occ.symbol)
            if tail.endswith("/__init__:"):
                module_def_file[occ.symbol] = rel
            # scip-python defines a @property as a term (`Task#backend.`) but refers to it as `Task#backend().`; a def under
            # `if TYPE_CHECKING:` is a term too. A term symbol defined ON a `def` line names that function under both spellings.
            d_at_line = by_line.get(occ.range[0] + 1)
            if tail.endswith(".") and not tail.endswith("().") and d_at_line is not None and d_at_line["kind"] != "Class":
                for spelling in (occ.symbol, occ.symbol[:-1] + "()."):
                    canon_by_def.setdefault(spelling, f"{rel}::{d_at_line['qualname']}")
            if tail.endswith("().") or tail.endswith("#"):
                d = by_line.get(occ.range[0] + 1)
                if d is not None:
                    # scip-python gives a nested function the same symbol as a method of the same name (`State#_event().` for both the
                    # method and `_create_dispatcher.<locals>._event`). A `self._event()` call can never reach the nested one, so a
                    # non-nested definition always wins; among equals (accessor pairs, overloads) the last definition wins.
                    # A colliding symbol's bare-name uses (`return esc`) bind to the nested function, its attribute uses (`self.esc`)
                    # to the method; occurrences below are routed accordingly.
                    (nested_seen if d["nested"] else plain_seen).add(occ.symbol)
                    if d["nested"]:
                        nested_target[occ.symbol] = f"{rel}::{d['qualname']}"
                    if occ.symbol not in canon_by_def or not d["nested"] or occ.symbol in nested_canon:
                        canon_by_def[occ.symbol] = f"{rel}::{d['qualname']}"
                        (nested_canon.add if d["nested"] else nested_canon.discard)(occ.symbol)

    colliding = nested_seen & plain_seen
    occurrences: list[dict] = []
    for doc in index.documents:
        rel = doc.relative_path
        model = models.get(rel)
        if model is None or model.tree is None:
            continue
        # receiver classification: a module/class symbol immediately before `.name` means a lexical (binding) receiver
        lexical_receivers = {
            (o.range[0] + 1, scip_col_to_char(model.lines[o.range[0]], o.range[1], encoding))
            for o in doc.occurrences
            if o.range[0] < len(model.lines) and (descriptor_tail(o.symbol).endswith(("/__init__:", "#")))
        }
        for occ in doc.occurrences:
            if occ.symbol_roles & Definition or occ.symbol.startswith("local "):
                continue
            line = occ.range[0] + 1
            text = model.lines[line - 1] if line - 1 < len(model.lines) else ""
            start_char = scip_col_to_char(text, occ.range[1], encoding)
            end_line = (occ.range[2] + 1) if len(occ.range) == 4 else line
            end_text = model.lines[end_line - 1] if end_line - 1 < len(model.lines) else ""
            end_char = scip_col_to_char(end_text, occ.range[3] if len(occ.range) == 4 else occ.range[2], encoding)
            start, end = (line, start_char), (end_line, end_char)
            tail = descriptor_tail(occ.symbol)
            relation = basis = target_id = class_id = None

            if line in model.import_lines:
                if tail.endswith("/__init__:"):
                    dest = module_def_file.get(occ.symbol)
                    if dest and dest != rel and dest in scope:
                        relation, basis, target_id = "IMPORTS", "path", dest
                if relation is None:
                    continue
            elif occ.symbol in canon_by_def:
                target_id = canon_by_def[occ.symbol]
                if occ.symbol in colliding and start_char > 0 and text[start_char - 1] != ".":
                    target_id = nested_target[occ.symbol]      # a bare name reaches the nested function, not the method
                if occ.symbol_roles & WriteAccess:
                    continue
                if end in model.base_heads:
                    relation, basis = "INHERITS", "binding"
                    class_id = f"{rel}::{model.base_heads[end]}"
                elif any(a <= start and end <= b for a, b, _ in model.base_spans) or model.in_annotation(start, end):
                    relation = "TYPE_USE"
                elif end in model.callee_ends:
                    call = model.callee_ends[end]
                    relation = "CALLS"
                    fn = call.func
                    basis = "binding" if isinstance(fn, ast.Name) else (receiver_basis(model, fn, lexical_receivers) if isinstance(fn, ast.Attribute) else "type_declared")
                else:
                    relation = "REFERENCES"
                    attr = model.attribute_ends.get(end)
                    basis = receiver_basis(model, attr, lexical_receivers) if attr is not None else "binding"
            else:
                continue

            original, projected = model.owners(line)
            occurrences.append({
                "file": rel, "start": list(start), "end": list(end), "target_id": target_id,
                "original_owner_id": f"{rel}::{original['qualname']}" if original else rel,
                "projected_owner_id": f"{rel}::{projected['qualname']}" if projected else rel,
                "relation": relation, "resolution_basis": basis, "class_id": class_id,
            })

    for f, model in models.items():
        for line, level, col, module in model.relative_imports:
            base = Path(f).parent
            for _ in range(level - 1):
                base = base.parent
            parts = module.split(".") if module else []
            candidates = [str(base.joinpath(*parts)) + ".py", str(base.joinpath(*parts) / "__init__.py")] if parts else [str(base / "__init__.py")]
            dest = next((c.removeprefix("./") for c in candidates if c.removeprefix("./") in scope), None)
            if dest and dest != f:
                occurrences.append({"file": f, "start": [line, col], "end": [line, col], "target_id": dest, "original_owner_id": f,
                                    "projected_owner_id": f, "relation": "IMPORTS", "resolution_basis": "path", "class_id": None})

    edges: dict[tuple, dict] = {}
    symbol_ids = {s["id"] for s in symbols if not s.get("nested")}   # nested definitions are not indexed symbols
    nested_target_occurrences = []
    for o in occurrences:
        if o["relation"] == "TYPE_USE":
            continue
        source = o["file"] if o["relation"] == "IMPORTS" else (o["class_id"] if o["relation"] == "INHERITS" else o["projected_owner_id"])
        target = o["target_id"]
        if target not in symbol_ids:
            nested_target_occurrences.append({**o, "note": "target is a nested definition, not an indexed symbol; never replaced by its enclosing function"})
            continue
        if source == target and o["relation"] != "CALLS":
            continue
        e = edges.setdefault((source, target, o["relation"]), {"source": source, "target": target, "relation": o["relation"],
                                                                "basis": set(), "file": o["file"], "line": o["start"][0]})
        e["basis"].add(o["resolution_basis"])
    out_edges = []
    for e in edges.values():
        e["basis"] = "type_declared" if e["basis"] == {"type_declared"} else "binding"
        out_edges.append(e)

    out = {
        "oracle": f"scip-python {index.metadata.tool_info.version} (Pyright) + ast roles/owners",
        "adapter": "benchmarks/tools/scip_python_to_graph.py",
        "manifest_sha256": manifest["manifest_sha256"], "repo_sha": manifest["repo_sha"], "text_encoding": encoding, "declared_text_encoding": declared,
        "complete": False,
        "status": "static semantic oracle; runtime dispatch not covered; type_declared edges are POSSIBLE targets",
        "symbols": symbols,
        "edges": sorted(out_edges, key=lambda e: (e["source"], e["target"], e["relation"])),
        "occurrences": occurrences,
        "nested_target_occurrences": nested_target_occurrences,
    }
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    rel_counts: dict[str, int] = {}
    for e in out_edges:
        rel_counts[e["relation"]] = rel_counts.get(e["relation"], 0) + 1
    basis_counts = {b: sum(1 for e in out_edges if e["basis"] == b and e["relation"] == "CALLS") for b in ("binding", "type_declared")}
    print(f"{len(symbols)} symbols, {len(occurrences)} occurrences | edges {rel_counts} | CALLS by basis {basis_counts} | "
          f"nested-target occurrences {len(nested_target_occurrences)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
