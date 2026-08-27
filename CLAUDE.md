## IBWD — codebase memory

This repo has an IBWD MCP server providing structural + semantic queries
over the codebase (tree-sitter-derived facts + local-LLM-inferred summaries,
added in later sprints). It's built incrementally — this file grows one
block per sprint.

**Use IBWD tools first for:**
- File discovery/categorization -> `ibwd_find_files` (kind: source/test/doc/config, or a name_pattern substring), instead of repeated `Glob` calls (Sprint 1)

**Fall back to Glob/Grep/Read for:**
- Reading full file contents or exact source needed to make an edit
- Anything not yet covered by an IBWD tool (most query types — this file
  will list more as later sprints ship)

**Always:**
- Run `ibwd_scan` (or `/graphify` once it exists) if results look stale
  relative to recent changes
- Never treat the graph as ground truth — it is a map to speed up
  navigation, not a replacement for reading the code you're about to change
