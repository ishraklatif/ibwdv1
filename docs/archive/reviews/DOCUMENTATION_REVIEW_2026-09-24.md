# Documentation review — 2026-09-24

Scope: a dated documentation audit of the project state on 2026-09-24. Its count and inventory are historical, not a current file list.
Third-party dependency READMEs under `node_modules`, virtual-environment packages, Git internals and generated caches are excluded.
`AGENTS.md` links to `CLAUDE.md`; the two Sprint 3 finish-plan copies were verified byte-identical.
All unique project document bodies were read; repeated historical content was not treated as independent evidence.

The active result is [TOKEN_EFFICIENCY_ROADMAP.md](../../development/ROADMAP.md).
This audit does not rerun or independently recertify historical benchmarks.
For current documentation navigation, start with the repository [README](../../../README.md).

| Document | Finding / treatment |
| --- | --- |
| [README.md](../../../README.md) | Mixed shipped and future capabilities, old sprint order and broad performance language; replaced with a current product overview |
| [DEVELOPMENT.md](../../development/DEVELOPMENT.md) | Correct no-spend/dual-client direction; linked to the revised roadmap |
| [AGENTS.md](../../../AGENTS.md) | Symlink to shared instructions; preserve the shared mechanism |
| [CLAUDE.md](../../../CLAUDE.md) | Useful routing and source-verification rules; update owner direction and distinguish shipped structure from future semantics |
| [IBWD_v1_EXECUTION_PLAN.md](../sprints/IBWD_v1_EXECUTION_PLAN.md) | Historical eight-sprint plan; mark future sequencing superseded, preserve body |
| [SPRINT_1.md](../sprints/SPRINT_1.md) | Small historical agent demo; keep sample-size limits and results |
| [SPRINT_2.md](../sprints/SPRINT_2.md) | Explicit nested-function omission and partial result; preserve |
| [SPRINT_3.md](../sprints/SPRINT_3.md) | Distinguish delivered graph/free validation from untested paid efficiency gate; replace obsolete next step |
| [SPRINT_3_ADDENDUM_PATHFINDING.md](../sprints/SPRINT_3_ADDENDUM_PATHFINDING.md) | Historical path design; unsupported deletion inference and proposed cosine A* semantics are not active guidance |
| [SPRINT_3_FINISH_PLAN.md](../sprints/SPRINT_3_FINISH_PLAN.md) | Useful oracle/accounting corrections; historical paid progression and prior screening snapshot |
| [KNOWN_LIMITATIONS.md](../../reference/KNOWN_LIMITATIONS.md) | Essential evidence about tests, type-inferred receivers and runtime gaps; keep authoritative for graph scope |
| [SCHEMA.md](../../reference/SCHEMA.md) | Some prose conflates extraction and call-resolution confidence; old sprint labels and migration descriptions need clarification |
| [Original research report](../research/RESEARCH_FINDINGS.md) | Retain research history; do not treat third-party claims, old leaderboard positions or hardware/model recommendations as current proof |
| [docs/DAILY_USE.md](../../guides/DAILY_USE.md) | Shipped setup/reporting workflow; continue explicit scans until freshness changes ship |
| [docs/NEW_DEVICE_SETUP.md](../../guides/NEW_DEVICE_SETUP.md) | Reusable installation, per-device environments, macOS/Linux/WSL boundary; link device-agnostic roadmap |
| [docs/USAGE_MEASUREMENT.md](../../guides/USAGE_MEASUREMENT.md) | Correctly separates observations from savings; nested calls and child accounting are current gaps |
| [ibwd-sprint3-kit/README.md](../../../ibwd-sprint3-kit/README.md) | Historical verification kit; preserve |
| [Kit finish plan](../../../ibwd-sprint3-kit/SPRINT_3_FINISH_PLAN.md) | Identical historical plan copy; preserve |
| [benchmarks/SPRINT3_gate_definition.md](../../../benchmarks/SPRINT3_gate_definition.md) | Frozen gate, accounting and corpus policies; unchanged |
| [benchmarks/SPRINT3_free_stage_report.md](../../../benchmarks/SPRINT3_free_stage_report.md) | Historical build-27 results and limitations; unchanged |
| [benchmarks/SPRINT3_protocol.md](../../../benchmarks/SPRINT3_protocol.md) | Older protocol already superseded by the frozen gate; do not run its example paid/destructive commands |
| [benchmarks/experiment/README.md](../../../benchmarks/experiment/README.md) | Later frozen harness exists; no paid run authorized; unchanged |

## Main corrections

- Optimize evidence retrieval and total successful-work usage before adding model layers. Move budgeting ahead of embeddings.
- Separate setup readiness, live connection, actual retrieval and measured benefit. They require different evidence.
- Keep output tokens, serialized bytes, processed context, cached tokens, dollars and subscription allowance distinct.
- Use exact source and compiler/test evidence to reduce rework. A smaller incomplete answer does not meet the objective.
- Treat local models as optional resource-constrained adapters, portable across devices, not a mandatory 30B stack.
- Treat confidence scores as heuristics. Deterministic extraction does not establish semantic certainty.
- A generated edge referencing an existing node can still be false; schema validation is not a hallucination-proof grader.
- A cosine-distance A* heuristic does not establish optimal semantic relevance; retain tested path semantics unless explicitly redesigned.
- Preserve frozen benchmark artifacts and their untested gate. Change the development objective prospectively and visibly.

## Additional evidence inspected

Current implementation: MCP server, scan pipeline, traversal, graph storage, setup, index diagnostics and usage/reporting code.
Existing work-repository summary reports were read without copying raw transcripts into project documentation.
One Codex snapshot exposes only orchestration calls; one Claude snapshot exposes a scan without retrieval.
Neither is a comparable baseline or proof of savings. The roadmap addresses this observation gap explicitly.

Official client/model/runtime documentation and primary project documentation were checked for the recommendations.
Links are next to the supported claims in the roadmap. No paid models, model downloads or benchmark runs were used for this review.
