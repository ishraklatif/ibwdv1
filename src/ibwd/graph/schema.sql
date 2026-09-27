-- graph/schema.sql
CREATE TABLE IF NOT EXISTS nodes (
    id             INTEGER PRIMARY KEY,
    node_type      TEXT NOT NULL,   -- File, Directory, Class, Function, Method, Commit, ...
    name           TEXT NOT NULL,
    qualified_name TEXT,
    file_path      TEXT,
    kind           TEXT,            -- source | test | doc | config | vendor | generated | other (File nodes only)
    start_line     INTEGER,
    end_line       INTEGER,
    content_hash   TEXT,
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_hash ON nodes(content_hash);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
-- File/Directory: exactly one node per (node_type, file_path) — a
-- table-level UNIQUE here would also block multiple *symbol* nodes
-- (Class/Function/Method, added Sprint 2) that share a node_type + file_path
-- (e.g. two methods in one file), so this is scoped to just the two
-- container types via a partial index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_file_container_unique
    ON nodes(node_type, file_path) WHERE node_type IN ('File', 'Directory');
-- Symbol nodes are keyed by qualified_name for upserts instead; SQLite
-- treats NULL != NULL in a UNIQUE index, so File/Directory nodes
-- (qualified_name IS NULL) are unaffected by this one.
CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);

CREATE TABLE IF NOT EXISTS edges (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id     INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation      TEXT NOT NULL,   -- CONTAINS, DEFINES, IMPORTS, CALLS, INHERITS, REFERENCES, TOUCHED, TESTED_BY, RELATES_TO, ...
    confidence    REAL NOT NULL DEFAULT 1.0,
    source_type   TEXT NOT NULL DEFAULT 'static_analysis',  -- static_analysis | llm_inference
    created_at    TEXT DEFAULT (datetime('now')),
    -- resolved: import-map / same-module / inherited (and IMPORTS). candidate: unique-name / suffix hints, excluded from
    -- default callers / dependents / trace-path answers. resolution_tier keeps the mechanism; confidence is the heuristic score.
    resolution_status TEXT NOT NULL DEFAULT 'resolved',
    resolution_tier   TEXT,
    UNIQUE (source_id, target_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_relation ON edges(relation);

-- Added Sprint 3 follow-up: per-file extracted references (imports/calls/bases), keyed by content
-- hash, so a rescan re-parses only files that changed. `version` = the extraction logic version.
CREATE TABLE IF NOT EXISTS file_refs (
    file_path    TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    version      INTEGER NOT NULL,
    refs_json    TEXT NOT NULL
);

-- Reserved historical summary schema; no model-generated summaries are populated.
CREATE TABLE IF NOT EXISTS summaries (
    node_id            INTEGER PRIMARY KEY REFERENCES nodes(id),
    summary            TEXT,
    responsibilities   TEXT,
    architectural_role TEXT,
    concepts           TEXT,
    derived_from_hash  TEXT,
    grader_status      TEXT DEFAULT 'unchecked'  -- unchecked | passed | downgraded | dropped
);

-- Historical embedding proposal; Sprint 6 instead uses a separate semantic.db:
-- CREATE VIRTUAL TABLE vec_nodes USING vec0(node_id INTEGER PRIMARY KEY, embedding FLOAT[1024]);

-- Recursive CTE example, added Sprint 3, "callers of node N up to depth 3":
-- WITH RECURSIVE callers(id, depth) AS (
--   SELECT source_id, 1 FROM edges WHERE target_id = :node_id AND relation = 'CALLS'
--   UNION
--   SELECT e.source_id, c.depth + 1 FROM edges e
--   JOIN callers c ON e.target_id = c.id
--   WHERE e.relation = 'CALLS' AND c.depth < 3
-- )
-- SELECT DISTINCT id, depth FROM callers ORDER BY depth;
