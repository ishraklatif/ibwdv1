"""Export the graph as canonical JSON for comparison against independent oracles.

Symbol identity is stable and independent of SQLite row ids: a symbol is "{file}::{Outer.inner}"
(its qualified_name); a File node (module-level calls, IMPORTS) is just its repo-relative path.

Edges carry the pair (source, target, relation), the heuristic confidence, the resolution tier and the
resolution_status (resolved | candidate). IBWD edges do not store callsite positions, so comparisons are at
(caller symbol -> target symbol) granularity: they can show a *relation* is missing, not that an
individual callsite was.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ibwd.graph.resolution import (
    CONF_FUZZY,
    CONF_IMPORT_MAP,
    CONF_INHERITED,
    CONF_SAME_MODULE,
    CONF_SUFFIX,
    CONF_UNIQUE_NAME,
    EDGE_BUILD_VERSION,
    REFERENCE_RELATIONS,
)

_TIERS = {
    CONF_IMPORT_MAP: "import_map",
    CONF_SAME_MODULE: "same_module",
    CONF_INHERITED: "inherited",
    CONF_UNIQUE_NAME: "unique_name",
    CONF_SUFFIX: "suffix",
    CONF_FUZZY: "fuzzy",
}
FORMAT_VERSION = 1


def _tier(relation: str, confidence: float) -> str | None:
    if relation == "IMPORTS":
        return None
    return _TIERS.get(round(confidence, 2), "unknown")


def export_graph(conn: sqlite3.Connection, repo_name: str, repo_sha: str | None = None) -> dict:
    node_ids: dict[int, str] = {}
    symbols: list[dict] = []

    for row in conn.execute(
        "SELECT id, node_type, name, qualified_name, file_path, start_line, end_line, kind FROM nodes "
        "WHERE node_type IN ('File', 'Class', 'Function', 'Method')"
    ):
        if row["node_type"] == "File":
            if row["kind"] != "source":
                continue
            node_ids[row["id"]] = row["file_path"]
            symbols.append({"id": row["file_path"], "file": row["file_path"], "line": 1, "name": row["name"],
                            "qualname": None, "kind": "File"})
            continue
        symbol_id = row["qualified_name"]
        qualname = symbol_id.split("::", 1)[-1]
        node_ids[row["id"]] = symbol_id
        symbols.append({"id": symbol_id, "file": row["file_path"], "line": row["start_line"],
                        "end_line": row["end_line"], "name": row["name"], "qualname": qualname,
                        "kind": row["node_type"]})

    placeholders = ",".join("?" * len(REFERENCE_RELATIONS))
    edges: list[dict] = []
    for row in conn.execute(
        f"SELECT source_id, target_id, relation, confidence, resolution_status, resolution_tier FROM edges WHERE relation IN ({placeholders})",
        REFERENCE_RELATIONS,
    ):
        source, target = node_ids.get(row["source_id"]), node_ids.get(row["target_id"])
        if source is None or target is None:
            continue
        edges.append({"source": source, "target": target, "relation": row["relation"],
                      "confidence": row["confidence"], "tier": row["resolution_tier"] or _tier(row["relation"], row["confidence"]),
                      "resolution_status": row["resolution_status"]})

    symbols.sort(key=lambda s: (s["file"], s["line"] or 0, s["id"]))
    edges.sort(key=lambda e: (e["source"], e["target"], e["relation"]))
    return {
        "format": "ibwd-graph",
        "format_version": FORMAT_VERSION,
        "edge_build_version": EDGE_BUILD_VERSION,
        **detect_ibwd_version(),
        "repo": repo_name,
        "repo_sha": repo_sha,
        "scope": "source files only; test files, vendored and generated files are not indexed",
        "callsite_positions": False,
        "symbols": symbols,
        "edges": edges,
    }


def detect_ibwd_version() -> dict:
    """The IBWD checkout that produced an export: commit and whether it had uncommitted changes."""
    try:
        from git import Repo

        import ibwd

        repo = Repo(Path(ibwd.__file__).resolve().parent, search_parent_directories=True)
        return {"ibwd_commit": repo.head.commit.hexsha, "ibwd_dirty": repo.is_dirty(untracked_files=False)}
    except Exception:
        return {"ibwd_commit": None, "ibwd_dirty": None}


def detect_repo_sha(repo_root: Path) -> str | None:
    try:
        from git import Repo

        return Repo(repo_root).head.commit.hexsha
    except Exception:
        return None
