# Graph Schema Reference

Future sprint numbers in this reference originated in the historical plan; see
[the active roadmap](docs/TOKEN_EFFICIENCY_ROADMAP.md) for current sequencing.
The `summaries` table exists as reserved storage, but no shipped local-model summarization pipeline populates it.

**File:** `src/ibwd/graph/schema.sql`
**Engine:** SQLite (single-file database at `.ibwd/graph.db`)
**Initialized by:** `ibwd.graph.database.connect()` for writers; MCP retrieval opens a read-only published snapshot.

Sprint 3B adds `index_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)` during staged publication.
`generation` identifies the published snapshot; `config_digest` fingerprints import-resolution inputs. A per-repository
lock coordinates graph/manifest/generation publication and retrieval. A publication marker makes interrupted updates explicitly
stale. See [Sprint 3B](docs/SPRINT_3B.md) for the version-2 retrieval envelope and cursor contract.

Sprint 4 adds `lexical_version` to index metadata, plus `evidence_files(path PRIMARY KEY, scope, content_hash, omission)`
and the FTS5 virtual table `evidence_fts(path, scope, start_line, end_line, heading, terms)`. Only `terms` is searchable;
other columns retain exact evidence locations and scope. Lexical evidence is incrementally updated in the staged graph DB
and published in the same generation. Test/doc/config evidence does not create production graph nodes or edges.
See [Sprint 4](docs/SPRINT_4.md) for chunk/exclusion limits and the separate generation-bound packet cache.

## 1. Overview

IBWD represents a codebase as a property graph persisted in two relational
tables:

| Table  | Represents                                                        |
|--------|--------------------------------------------------------------------|
| `nodes`| An entity in the codebase — a directory, a file, or a symbol (class, function, method). |
| `edges`| A directed, typed relationship between two entities.               |

A third table, `summaries`, attaches optional LLM-derived narrative
metadata to individual nodes. These tables share a single SQLite file and
are created idempotently (`CREATE TABLE IF NOT EXISTS`). Existing schema
changes still require explicit migration logic; see the write-path section.

This document specifies the schema's structure, its constraints and their
rationale, the write path that enforces them, and a worked example.

## 2. Table: `nodes`

```sql
CREATE TABLE IF NOT EXISTS nodes (
    id             INTEGER PRIMARY KEY,
    node_type      TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT,
    file_path      TEXT,
    kind           TEXT,
    start_line     INTEGER,
    end_line       INTEGER,
    content_hash   TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);
```

### 2.1 Column reference

| Column           | Type    | Nullable | Applies to                    | Description |
|------------------|---------|----------|--------------------------------|-------------|
| `id`             | INTEGER | No       | All                             | Surrogate primary key; SQLite `ROWID` alias. |
| `node_type`      | TEXT    | No       | All                             | Discriminator: `Directory`, `File`, `Class`, `Function`, `Method`. Additional types (e.g. `Commit`) are reserved for future sprints. |
| `name`           | TEXT    | No       | All                             | Local, non-unique display name (file/directory basename, or symbol identifier). |
| `qualified_name` | TEXT    | Yes      | Symbol nodes only               | Globally unique identifier in the form `{file_path}::{Outer.Inner}`. `NULL` for `File`/`Directory` nodes. |
| `file_path`      | TEXT    | Yes      | File, Directory, Symbol         | Repo-relative path. For a `File`/`Directory` node this *is* the entity; for a symbol node it identifies the containing file. |
| `kind`           | TEXT    | Yes      | File nodes only                 | Content classification: `source`, `test`, `doc`, `config`, `vendor`, `generated`, `other`. Only `source` files get symbols and edges. |
| `start_line`     | INTEGER | Yes      | Symbol nodes only               | 1-indexed line where the definition begins. |
| `end_line`       | INTEGER | Yes      | Symbol nodes only               | 1-indexed line where the definition ends. |
| `content_hash`   | TEXT    | Yes      | File, Symbol                    | Hash of source content, used to detect no-op rescans without re-parsing. |
| `created_at`     | TEXT    | No       | All                              | UTC timestamp, set once on insert. |
| `updated_at`     | TEXT    | No       | All                              | UTC timestamp, refreshed on every upsert. |

`node_type` is a single discriminator column rather than a table-per-type
design: it lets one query traverse directories, files, and symbols
uniformly (e.g. `WHERE node_type IN ('Class','Function','Method')`),
at the cost of several columns being meaningful only for a subset of rows
(documented above under "Applies to").

### 2.2 Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_hash ON nodes(content_hash);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
```

Non-unique secondary indexes supporting the primary lookup patterns:
filtering by entity type, resolving a node by path, detecting unchanged
content on rescan, and name-based symbol search.

### 2.3 Uniqueness constraints

Two distinct uniqueness rules apply to `nodes`, because `File`/`Directory`
rows and symbol rows are deduplicated on different keys.

**Rule 1 — one row per (`node_type`, `file_path`) for container nodes.**

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_file_container_unique
    ON nodes(node_type, file_path) WHERE node_type IN ('File', 'Directory');
```

A given file or directory path may have at most one `File` node and at
most one `Directory` node. This is enforced via a *partial* unique index
rather than a table-level constraint, because a table-level
`UNIQUE(node_type, file_path)` would also restrict `Class`/`Function`/
`Method` rows — which legitimately share both a `node_type` and a
`file_path` when a file defines multiple symbols of the same kind (e.g.
two functions in one module).

**Rule 2 — one row per `qualified_name` for symbol nodes.**

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
```

Symbol nodes are deduplicated by `qualified_name` instead, since that is
what is unique per symbol (`file_path` is not — a file may define many
symbols). SQLite's unique-index semantics treat `NULL <> NULL`, so
`File`/`Directory` rows (`qualified_name IS NULL`) never collide with each
other or with this index, and Rule 1 and Rule 2 do not interfere.

Together, the two rules give idempotent re-scanning: re-running the scan
on an unchanged tree updates existing rows in place rather than
duplicating them.

## 3. Table: `edges`

```sql
CREATE TABLE IF NOT EXISTS edges (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id     INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation      TEXT NOT NULL,
    confidence    REAL NOT NULL DEFAULT 1.0,
    source_type   TEXT NOT NULL DEFAULT 'static_analysis',
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE (source_id, target_id, relation)
);
```

### 3.1 Column reference

| Column        | Type    | Nullable | Description |
|---------------|---------|----------|-------------|
| `id`          | INTEGER | No       | Surrogate primary key. |
| `source_id`   | INTEGER | No       | Foreign key to `nodes.id`; the relationship's origin. |
| `target_id`   | INTEGER | No       | Foreign key to `nodes.id`; the relationship's destination. |
| `relation`    | TEXT    | No       | Relationship type. Currently emitted: `CONTAINS` (directory→directory, directory→file), `DEFINES` (file→symbol), `IMPORTS` (file→file), `CALLS` (symbol-or-file→symbol; a module-level call is attributed to its `File` node; a JSX tag such as `<Card />` counts as a call to the component), `INHERITS` (class→class), `REFERENCES` (symbol-or-file→symbol; a function used as a value, e.g. `useReducer(fn)`). Reserved for later sprints: `TOUCHED`, `TESTED_BY`, `RELATES_TO`. |
| `confidence`  | REAL    | No       | Extraction/resolution heuristic score; static call resolution also uses values below `1.0` (see §3.3). |
| `source_type` | TEXT    | No       | `static_analysis` (parsed from source) or `llm_inference` (model-derived, not yet emitted by any sprint as of this schema). |
| `created_at`  | TEXT    | No       | UTC timestamp, set on insert. |

### 3.2 Referential integrity

Both foreign keys are declared `ON DELETE CASCADE`. Combined with
`PRAGMA foreign_keys = ON` (set at connection time in
`database.connect()`), this guarantees that deleting a node also deletes
every edge that references it as source or target — the graph cannot
contain an edge pointing at a nonexistent node.

### 3.3 Uniqueness and provenance

`UNIQUE (source_id, target_id, relation)` ensures at most one edge of a
given relation type exists between any ordered pair of nodes. Rescanning
an unchanged relationship updates the existing row (`confidence`,
`source_type`) rather than inserting a duplicate.

The `confidence` / `source_type` pair distinguishes extraction provenance from
heuristic resolution scores. `CONTAINS`/`DEFINES` can carry `1.0`; resolved `CALLS`
use heuristic scores such as `0.95` for import-map resolution while retaining
`source_type = 'static_analysis'`. Deterministic extraction does not imply
semantic certainty. Future model-derived hints must remain separately labelled
`llm_inference`; a numeric score does not make them verified facts.

### 3.4 Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);
```

Supports traversal in both directions (parents of a node, children of a
node) and filtering by relationship type.

**Resolution status (added after Sprint 3).** Every edge is `resolved` (import-map, same-module, inherited; all `IMPORTS`) or
`candidate` (unique-name, suffix, inherited-uncertain). Default `ibwd_callers` / `ibwd_dependents` / `ibwd_trace_path` follow `resolved` edges only;
`include_candidates=true` adds candidates, clearly labelled. Fuzzy edges are not created unless `IBWD_EXPERIMENTAL_FUZZY=1`.
`resolution_tier` keeps the mechanism and `confidence` the heuristic score; neither is a calibrated probability.

## 3b. Table: `file_refs` (Sprint 3 follow-up)

Cache of each source file's extracted (unresolved) references — imports, call sites, value uses, base
classes, default export — as JSON, so an incremental scan re-parses only files whose content changed and
re-runs cheap in-memory resolution over the rest.

| Column         | Type    | Notes |
|----------------|---------|-------|
| `file_path`    | TEXT PK | Repo-relative path. Rows for deleted files are removed on the next scan. |
| `content_hash` | TEXT    | The file's hash when the references were extracted. |
| `version`      | INTEGER | Extraction-logic version (`EDGE_BUILD_VERSION`); a mismatch forces a re-parse. |
| `refs_json`    | TEXT    | Serialized `FileReferences`. |

Created with `CREATE TABLE IF NOT EXISTS`, so existing databases upgrade without a rebuild.

## 4. Table: `summaries` (reserved for future inference)

```sql
CREATE TABLE IF NOT EXISTS summaries (
    node_id            INTEGER PRIMARY KEY REFERENCES nodes(id),
    summary            TEXT,
    responsibilities   TEXT,
    architectural_role TEXT,
    concepts           TEXT,
    derived_from_hash  TEXT,
    grader_status      TEXT DEFAULT 'unchecked'
);
```

A 1:1 extension table keyed on `node_id`, holding LLM-generated narrative
metadata layered on top of the structural facts in `nodes`/`edges`.

| Column               | Description |
|----------------------|-------------|
| `node_id`            | Primary key and foreign key to `nodes.id`; one summary row per node. |
| `summary`            | Free-text description of the node's purpose. |
| `responsibilities`   | What the node is responsible for, as inferred by the model. |
| `architectural_role` | The node's role within the broader system architecture. |
| `concepts`           | Domain or technical concepts associated with the node. |
| `derived_from_hash`  | The `nodes.content_hash` value the summary was generated from; a mismatch on rescan signals the summary is stale and needs regeneration. |
| `grader_status`      | Quality-check state: `unchecked`, `passed`, `downgraded`, `dropped`. |

## 5. Planned extension: `vec_nodes` (Sprint 5, not yet active)

```sql
-- CREATE VIRTUAL TABLE vec_nodes USING vec0(node_id INTEGER PRIMARY KEY, embedding FLOAT[1024]);
```

Reserved for embedding-based similarity search over nodes via the
`sqlite-vec` extension. Present in `schema.sql` as a comment; not created
by the current schema.

## 6. Query pattern: transitive traversal

The schema comment below (Sprint 3) is a reference pattern for depth-bounded
traversal, not part of the schema itself — it documents how to answer
"callers of node N up to depth 3" using a recursive CTE over `edges`:

```sql
WITH RECURSIVE callers(id, depth) AS (
  SELECT source_id, 1 FROM edges WHERE target_id = :node_id AND relation = 'CALLS'
  UNION
  SELECT e.source_id, c.depth + 1 FROM edges e
  JOIN callers c ON e.target_id = c.id
  WHERE e.relation = 'CALLS' AND c.depth < 3
)
SELECT DISTINCT id, depth FROM callers ORDER BY depth;
```

## 7. Worked example

Given the following source tree:

```
myrepo/
└── src/
    └── app.py
```

with `app.py` containing:

```python
def greet():
    return "hi"
```

a scan produces the following rows.

**`nodes`**

| id | node_type | name   | qualified_name    | file_path  | kind   | start_line |
|----|-----------|--------|--------------------|------------|--------|------------|
| 1  | Directory | .      | NULL               | .          | NULL   | NULL       |
| 2  | Directory | src    | NULL               | src        | NULL   | NULL       |
| 3  | File      | app.py | NULL               | src/app.py | source | NULL       |
| 4  | Function  | greet  | src/app.py::greet  | src/app.py | NULL   | 1          |

**`edges`**

| id | source_id | target_id | relation |
|----|-----------|-----------|----------|
| 1  | 1         | 2         | CONTAINS |
| 2  | 2         | 3         | CONTAINS |
| 3  | 3         | 4         | DEFINES  |

The edge rows, read against `nodes`, form a single chain: the repository
root contains `src`, `src` contains `app.py`, and `app.py` defines
`greet`.

If `greet` is subsequently removed from `app.py`, node `4` is deleted;
the `ON DELETE CASCADE` foreign key on `edges` removes edge `3` in the
same transaction, so no dangling edge remains.

## 8. Write path (`src/ibwd/graph/database.py`)

The schema's constraints are enforced through the upsert functions used by
the scan pipeline, each targeting a specific constraint:

| Function              | Conflict target                              | Enforces |
|------------------------|-----------------------------------------------|----------|
| `upsert_node()`         | `ON CONFLICT (node_type, file_path) WHERE node_type IN ('File','Directory')` | §2.3 Rule 1 |
| `upsert_symbol_node()`  | `ON CONFLICT (qualified_name)`                | §2.3 Rule 2 |
| `upsert_edge()`         | `ON CONFLICT (source_id, target_id, relation)`| §3.3 |
| `delete_node_by_path()` | `DELETE FROM nodes WHERE file_path = ?`       | Relies on cascade delete (§3.2) to remove dependent edges |

`connect()` opens `.ibwd/graph.db`, sets `PRAGMA foreign_keys = ON`
(required for cascade deletes to take effect — SQLite does not enforce
foreign keys by default), and applies `schema.sql` via `executescript()`
on every connection. `IF NOT EXISTS` makes creation idempotent but does not migrate
existing column definitions. `connect()` also adds missing resolution-status/tier
columns explicitly; future schema changes need a deliberate migration or rebuild strategy.

`src/ibwd/graph/queries.py` builds the higher-level scan reconciliation
(`sync_files`, `sync_symbols`) and read queries (`find_symbol`,
`list_symbols`, `find_files`) on top of these primitives, and is the
layer that decides *what* to write, while `database.py` and this schema
define *how* it is stored.

## 9. Revision history

| Sprint | Change |
|--------|--------|
| 1      | `nodes`, `edges` tables for `Directory`/`File` and `CONTAINS` edges. |
| 2      | `Class`/`Function`/`Method` node types, `qualified_name` uniqueness, `DEFINES` edges. |
| 3      | No schema change (edge-build version 3: JSX tags count as calls; tsconfig `paths` aliases resolve JS/TS imports). `IMPORTS`/`CALLS`/`INHERITS` edges now emitted (confidence 0.35–1.0, `source_type='static_analysis'`) and rebuilt wholesale on any source change; `PRAGMA user_version` records the edge-build version so older graphs are rebuilt on the next scan. Recursive-CTE traversal implemented in `retrieval/traversal.py`. |
| 3c     | `edges` gains `resolution_status` (`resolved` | `candidate`, default `resolved`) and `resolution_tier` (`import_map`, `same_module`, `inherited`, `inherited_uncertain`, `unique_name`, `suffix`, `path`); added in place by an idempotent `ALTER TABLE` at connect time, so existing databases upgrade without a rebuild. `confidence` is kept and is a *heuristic score*. Edge-build version 27 (14 at the policy change; 15–27 are resolver/extraction fixes found by the oracle comparison, listed in `benchmarks/SPRINT3_free_stage_report.md` §2). `inherited_uncertain` (candidate) marks an inherited dunder call behind an external base class. |
| 3b     | Additive: `file_refs` table (per-file extracted references keyed by content hash, so rescans re-parse only changed files); `REFERENCES` relation; File `kind` gains `vendor` / `generated`; edge-build version 7. |
| 5      | `vec_nodes` virtual table planned (not yet created). |
| Future inference | `summaries` storage is already reserved; the generation pipeline remains unimplemented. |
