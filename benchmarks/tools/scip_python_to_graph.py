#!/usr/bin/env python3
"""Turn a scip-python index into an oracle graph in the format compare_pairs.py / the kit's verify_repo.py expect.

Independent of IBWD's tree-sitter parser: symbol resolution comes from scip-python (Pyright), and caller ranges
from Python's own `ast`. Output symbol ids use IBWD's canonical form "file::Outer.inner".

Usage:
  scip_python_to_graph.py INDEX.scip REPO_ROOT AUDIT.json OUT.json [--proto-dir DIR]

Setup (once, in a venv that has `protobuf` and `grpcio-tools`):
  curl -sLO https://raw.githubusercontent.com/scip-code/scip/main/scip.proto
  python -m grpc_tools.protoc -I. --python_out=<DIR> scip.proto          # writes scip_pb2.py into --proto-dir
  scip-python index . --project-name NAME --project-version SHA --output OUT.scip   # in the repo, output elsewhere

Classification of a reference occurrence (SCIP itself does not distinguish calls from other reads):
  * on an import line                                   -> IMPORTS  (file -> file that defines the symbol)
  * next non-space character is "("                     -> CALLS
  * preceded by "@" (a decorator, not itself called)    -> REFERENCES
  * otherwise a read of a function/method symbol        -> REFERENCES (value use; annotations excluded)
Class instantiation `Foo(...)` is a CALLS edge to the class, as in IBWD. Calls made inside nested functions are
attributed to the outermost enclosing function/method (the same roll-up IBWD documents), and module-level ones to the file.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


def load_index(path: str, proto_dir: str):
    sys.path.insert(0, proto_dir)
    import scip_pb2  # noqa: E402

    index = scip_pb2.Index()
    index.ParseFromString(Path(path).read_bytes())
    return scip_pb2, index


def python_symbols(repo: Path, rel: str):
    """[(start_line, end_line, qualname, kind, def_line)] for functions and classes, via ast (1-based lines)."""
    try:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out: list[tuple[int, int, str, str, int]] = []

    def visit(node, prefix, in_function):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = ".".join([*prefix, child.name])
                kind = "Class" if isinstance(child, ast.ClassDef) else ("Method" if prefix and not in_function else "Function")
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                if not in_function:  # nested functions/classes are not owners; their calls roll up
                    out.append((start, child.end_lineno or child.lineno, qual, kind, child.lineno))
                if isinstance(child, ast.ClassDef):
                    visit(child, [*prefix, child.name], in_function)
                else:
                    visit(child, prefix, True)
            else:
                visit(child, prefix, in_function)

    visit(tree, [], False)
    return out


_DESCRIPTOR = re.compile(r"`[^`]*`|[^/#.()\[\]:!]+|[/#.():!\[\]]")


def descriptor_tail(symbol: str) -> str:
    """The part of a scip symbol after the package: e.g. "`scrapy.crawler`/Crawler#crawl()." """
    parts = symbol.split(" ", 4)
    return parts[4] if len(parts) == 5 else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("index"); ap.add_argument("repo"); ap.add_argument("audit"); ap.add_argument("out")
    ap.add_argument("--proto-dir", default=str(Path(__file__).resolve().parent))
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    scope = set(json.loads(Path(args.audit).read_text())["production_files"])
    scip, index = load_index(args.index, args.proto_dir)
    Definition = scip.SymbolRole.Definition

    # 1. ast symbols per in-scope file, and the canonical id of each SCIP definition
    syms_by_file = {f: python_symbols(repo, f) for f in scope if f.endswith(".py")}
    symbols = []
    for f, items in syms_by_file.items():
        for start, end, qual, kind, def_line in items:
            symbols.append({"id": f"{f}::{qual}", "file": f, "line": def_line, "end_line": end, "name": qual, "kind": kind})
    for f in sorted(scope):
        if f.endswith(".py"):
            symbols.append({"id": f, "file": f, "line": 1, "name": Path(f).name, "kind": "File"})
    canon_by_def: dict[str, str] = {}  # scip symbol -> canonical id
    file_of_symbol: dict[str, str] = {}
    module_file: dict[str, str] = {}   # scip module symbol -> file
    for doc in index.documents:
        rel = doc.relative_path
        by_line = {s[4]: s for s in syms_by_file.get(rel, [])}
        for occ in doc.occurrences:
            if not occ.symbol_roles & Definition or occ.symbol.startswith("local "):
                continue
            line = occ.range[0] + 1
            tail = descriptor_tail(occ.symbol)
            file_of_symbol[occ.symbol] = rel
            if tail.endswith("()." ) or tail.endswith("#"):
                hit = by_line.get(line)
                if hit is not None:
                    canon_by_def[occ.symbol] = f"{rel}::{hit[2]}"
        if rel.endswith(".py"):
            mod = rel[:-3].replace("/", ".").removesuffix(".__init__")
            module_file[mod] = rel

    # 2. references -> edges
    edges: dict[tuple, dict] = {}
    for doc in index.documents:
        rel = doc.relative_path
        if rel not in scope or not rel.endswith(".py"):
            continue
        lines = (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        owners = sorted(syms_by_file.get(rel, []), key=lambda s: (s[1] - s[0]))
        for occ in doc.occurrences:
            if occ.symbol_roles & Definition or occ.symbol.startswith("local "):
                continue
            line = occ.range[0] + 1
            end_col = occ.range[3] if len(occ.range) == 4 else occ.range[2]
            text = lines[line - 1] if line - 1 < len(lines) else ""
            stripped = text.lstrip()
            after = text[end_col:].lstrip()
            before = text[: occ.range[1]].rstrip()
            is_import_line = stripped.startswith(("from ", "import "))
            target = canon_by_def.get(occ.symbol)
            tail = descriptor_tail(occ.symbol)

            if is_import_line:
                dest = file_of_symbol.get(occ.symbol)
                if dest is None and tail.endswith("/__init__:"):
                    mod = tail.strip("`/__init__:").rstrip("/").strip("`")
                    dest = module_file.get(re.sub(r"[`/]|__init__:", "", tail))
                if dest and dest != rel and dest in scope:
                    edges.setdefault((rel, dest, "IMPORTS"), {"source": rel, "target": dest, "relation": "IMPORTS", "file": rel, "line": line})
                continue
            if target is None:
                continue  # external / builtin / local variable
            # a repeated `def name(` / `class name` (typing.overload stubs, redefinitions) is a definition site, not a use
            if re.match(r"(async\s+)?def\s+\w+|class\s+\w+", stripped) and occ.range[1] <= len(text) - len(stripped) + len(stripped.split("(")[0]):
                continue
            if after.startswith("("):
                relation = "CALLS"
            elif before.endswith("@"):
                relation = "REFERENCES"
            elif tail.endswith("()."):
                if before.endswith((":", "->", "|")):  # annotation position
                    continue
                relation = "REFERENCES"
            else:
                continue
            owner = next((s for s in owners if s[0] <= line <= s[1]), None)
            source = f"{rel}::{owner[2]}" if owner else rel
            if source == target and relation != "CALLS":
                continue
            edges.setdefault((source, target, relation), {"source": source, "target": target, "relation": relation, "file": rel, "line": line})

    out = {
        "oracle": f"scip-python {index.metadata.tool_info.version} (Pyright semantic resolution) + ast owner ranges",
        "complete": False,
        "status": "static semantic oracle; runtime dispatch not covered; REFERENCES classification is heuristic",
        "symbols": symbols,
        "edges": sorted(edges.values(), key=lambda e: (e["source"], e["target"], e["relation"])),
    }
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(symbols)} symbols, {len(out['edges'])} edges "
          f"({sum(e['relation']=='CALLS' for e in out['edges'])} CALLS, {sum(e['relation']=='IMPORTS' for e in out['edges'])} IMPORTS, "
          f"{sum(e['relation']=='REFERENCES' for e in out['edges'])} REFERENCES)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
