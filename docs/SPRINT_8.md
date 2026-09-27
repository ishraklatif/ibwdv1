# Sprint 8: portable setup and ordinary-work evidence

The existing `ibwd setup --repo PATH --client both` flow packages the shared engine,
bundled navigation skill, project MCP configuration and automatic reporting hooks.
Fresh project setup and updates are tested for both adapters, including quoted paths,
preservation of user instructions/settings/hooks, idempotence, and transport from another
working directory. Setup readiness checks the complete current tool set and reports the
installed Python, IBWD and MCP runtime versions. Client trust remains a client decision.
Moved interpreters or repositories still require reconciling conflicting server entries
and obsolete hooks as described in [new-device setup](NEW_DEVICE_SETUP.md).

## Automatic evidence

Stop/SessionEnd hooks publish session JSON/Markdown, `latest-CLIENT.md`, and
`comparison.md` plus `comparison.json` under `.ibwd/usage/`. No client or model is launched.

- Retrieval's actual input validation records freshness and whether a refresh ran.
  A generation ID alone does not establish freshness. Successful validation checks the
  indexed inventory at retrieval time; it does not certify semantic completeness.
- Exact response observation IDs link retained server requests to a transcript.
  Unattributed activity stays separate. Pending requests, errors, older records without
  freshness fields, and missing token totals remain visible rather than becoming success
  or zero usage. Scans are maintenance, not retrieval.
- Reports show returned item counts, evidence-file reference counts, payload bytes,
  server duration, truncation and semantic fallback counts, with known-counter coverage.
  File references can repeat across responses. Payload bytes are not model tokens;
  server duration excludes model/client time. No source, paths, queries or fallback error
  text is added to the server ledger. Project identity is a local path hash.
- Deterministic attention notes identify missing attribution, errors, semantic fallback
  and truncated queries. They do not claim an optimization saves tokens. Shell fallback
  is not inferred from a subsequent command.
- Cohorts separate client/model/effort/client-version/project/task-kind/condition and
  retain failed, incomplete, unknown and provisional sessions and reported rework.
  The comparison includes all available token snapshots, with missing totals counted.
  Unknown labels are not filled by an evaluator. No matched baseline is established,
  so the report describes adoption and retrieval efficiency without claiming savings.

## Explicit labels

Use the session key printed in a report to record your assessment:

```bash
ibwd usage-label SESSION_KEY --repo /path/to/repo --client codex \
  --task-kind implementation --condition enabled --outcome passed --rework no
```

Outcomes are `passed`, `failed`, `incomplete` or `unknown`; rework is `yes`, `no` or
`unknown`. Labels have `user_reported` provenance, not independently verified test
coverage. They apply to a captured transcript snapshot and expire when its parser
snapshot changes. Repeated hooks over unchanged input retain them. Reporting stays
provisional even after a passed label; final provider accounting is not guaranteed.
Labeling updates the saved session and comparison, and the latest report if it is that
session. It does not rerun work or an evaluator. Omitted label options become unknown.

## Compatibility and verification

| Layer | Locally tested | Limit |
| --- | --- | --- |
| Runtime | macOS 26.6.2 arm64, Python 3.13.9, IBWD 0.1.0, MCP Python SDK 2.1.1 | This is the tested environment, not a minimum-version matrix |
| Codex adapter | Fresh/update project TOML, bundled skill, command hooks, real SDK stdio transport and synthetic transcript fixture | No Codex binary/model session launched; client-version compatibility unverified |
| Claude Code adapter | Fresh/update project JSON, bundled skill, command hooks, real SDK stdio transport and synthetic transcript fixture | No Claude binary/model session launched; client-version compatibility unverified |
| Linux/WSL | Existing POSIX path/hook/locking contract retained | Not rerun on those operating systems in this sprint |
| Native Windows | Unsupported | Requires native path, locking and hook tests |

`tests/test_sprint8.py` covers end-to-end fresh/update setup, real retrieval refresh and
fallback observations, exact response-ID report joins, missing/duplicate/pending/error
evidence, explicit labels and invalidation, and project separation with unreadable
reports. Existing setup, hooks, transport and retrieval tests remain regression checks.
No paid jobs, model evaluation sessions, model downloads or benchmarks were run.
The full local suite recorded 351 passes and 11 optional-tool skips; its single nested
sandbox-isolation failure passed when rerun outside the enclosing sandbox.
Ordinary-work effectiveness and additional client/runtime compatibility remain to be
observed during needed work; this implementation does not manufacture that evidence.

The server ledger is a bounded recent window. A later comparison can have fewer linked
requests after eviction; session snapshots retain their capture-time summaries until
recaptured. Older reports remain readable but cannot retroactively gain missing freshness
observations. JSON and Markdown are individually atomic; a later hook repairs interrupted
multi-file publication. Labels detect parser snapshot changes, not all possible edits in
an append-only transcript's previously consumed middle.
