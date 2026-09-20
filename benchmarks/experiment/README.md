# Experiment sprint3-ab-v2 (frozen; supersedes v1, which was frozen before the total-spend ceiling existed and never ran a paid session)

**Status:** free graph validation reported complete (build 27, `../SPRINT3_free_stage_report.md`); harness built, tested without model calls,
isolation and tool inventory verified, configuration frozen (`experiment.json`, tag `sprint3-ab-v2-frozen`), preflight passes.
**Paid pilot and the 120-session run have NOT been executed** (they need explicit spending authorisation: `--i-authorise-spending`).

| what | where |
|---|---|
| frozen configuration and hashes | `experiment.json` (model, effort = CLI default, CLI 2.1.278, tools, timeout 600 s, budget $1.00/session, retry policy, seed, prompt template, task/grader/harness hashes, graph inputs) |
| 20 task specifications | `tasks/*.json`, index with hashes `tasks_index.json`, valid-symbol lists `symbols/` |
| grader | `../grade_sprint3_answers.py` (deterministic; tests `tests/test_grader.py`) |
| runner / modules | `../run_sprint3_ab.py`, `../ab/` (schedule, execute, transcript, store, run, mock), `../ab_prepare.py` |
| summarizer | `../summarize_sprint3_ab.py` (hand-calculated fixture in `tests/test_ab_runner.py`) |
| isolation and inventory evidence | `../evidence/isolation_check.json`, `../evidence/tool_inventory_check.json` |
| preparation record | `../preparation_record.json` |

Commands: `preflight` (prints planned sessions and spending controls, starts nothing), `mock RUN_DIR`, `pilot RUN_DIR --i-authorise-spending` (hard $4.00 total ceiling across retries; unknown-cost attempts reserve their full $1 cap),
`run RUN_DIR --i-authorise-spending --max-total-usd N` (resumable; the ceiling is mandatory), `summarize RUN_DIR OUT_DIR`.
Pilot rule (fixed before any result): the alphabetically first headline repository of each language, its Q1 task, both conditions, repeat 1
(scrapy-q1 and bulletproof-react-q1), 4 sessions outside the 120-session dataset.
