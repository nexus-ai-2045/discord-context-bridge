# Tech Debt Audit - Discord Context Bridge

Generated: 2026-07-09

## Executive Summary

- The largest risk is not broken tests; it is stale planning language. Older closeout docs say residual zero while current roadmap still has active speed, intake, parser, and rehearsal work.
- `src/discord_context_bridge/core.py:489` through `src/discord_context_bridge/core.py:2840` carries too many product surfaces in one module.
- `tests/test_core.py:1854` and nearby large test clusters make the test suite slow and hard to localize.
- `ops_check.py` now has fast/full/release profiles; the remaining speed work is measuring the actual intake/cache/parser path and shrinking slow tests.
- Product goal is correct but needs a sharper operating split: residual-zero safety proof is not the same as roadmap completion.
- Security boundary is mostly strong: scans and tests preserve metadata-only output, but local-private cache/backfill ideas must stay out of public artifacts until sanitized.

## Architectural Mental Model

The repo is a local-first Discord context bridge. It does not send to Discord. It takes visible text, local snapshots, or safe metadata and turns them into context passports, reply guidance, review artifacts, and send-closeout packets. The primary safety contract is metadata-only output: raw Discord text, participants, secrets, real IDs, and local paths stay out of public-facing output.

The implementation is currently concentrated in a Python package with a large `core.py`, a large CLI facade, a broad smoke/preflight script, and a set of docs that double as both roadmap and historical closeout evidence. Tests are extensive and valuable, but many critical behaviors are packed into a few large test files.

## Findings

| ID | Category | File:Line | Severity | Effort | Description | Recommendation |
|---|---|---:|---|---|---|---|
| F001 | Documentation drift | `docs/2026-07-01-impact-todo.md:48` | High | S | Historical closeout says roadmap residual is 0, but current active roadmap still has M1/M2/M3 work. | Mark historical closeout docs as superseded and keep active TODO in `ISSUE_LIST.md`. |
| F002 | Documentation drift | `README.md:103` | High | S | README residual TODO focused on send rehearsal only, omitting the faster intake and speed work the user currently needs. | Point README to `ISSUE_LIST.md` and name the current P0s. |
| F003 | Performance | `scripts/ops_check.py:290` | Done | M | `ops_check` now separates fast, full, and release profiles. | Keep fast as the normal development gate and continue measuring full-profile cost. |
| F004 | Test performance | `tests/test_live_mvp_status_scripts.py:753` | Medium | M | The slowest test takes about 3.3 seconds by itself. | Replace real subprocess/sleep-heavy paths with tighter fixtures or mark as full-profile only. |
| F005 | Architecture | `src/discord_context_bridge/core.py:489` | High | L | URL intake logic lives in the same module as snapshot, context, review, and send closeout logic. | Split into `intake.py`, `snapshot_ledger.py`, `context.py`, `send_closeout.py`, keeping exports stable. |
| F006 | Architecture | `src/discord_context_bridge/core.py:1289` | High | M | Snapshot ledger is now important enough to deserve its own module and tests. | Move append-only observation event logic out of `core.py`. |
| F007 | Architecture | `src/discord_context_bridge/core.py:1873` | Medium | M | Context passport logic shares a module with lower-level hashing, URL, and cache utilities. | Isolate context construction from persistence/audit helpers. |
| F008 | Architecture | `src/discord_context_bridge/core.py:2373` | Medium | M | Review artifact rendering is coupled to core domain helpers. | Move Markdown rendering into a presentation module. |
| F009 | Architecture | `src/discord_context_bridge/core.py:2840` | Medium | M | Send operation status lives alongside read-only intake logic, blurring the read/rehearsal boundary. | Move send rehearsal helpers under a separate module with explicit stopline tests. |
| F010 | Test debt | `tests/test_core.py:1854` | High | M | `test_core.py` is a large mixed suite; failures do not localize to a feature area quickly. | Split into `test_snapshot_ledger.py`, `test_intake.py`, `test_context.py`, `test_send_closeout.py`. |
| F011 | Roadmap quality | `ROADMAP.md:1` | Medium | S | Roadmap previously mixed done, future, stopline, implementation list, and meta features in one long file. | Keep roadmap as sequence and move active tasks to `ISSUE_LIST.md`. |
| F012 | Product risk | `ISSUE_LIST.md:1` | High | S | Active TODO did not previously distinguish residual-zero safety proof from actual product completion. | Make `ISSUE_LIST.md` the active TODO source and explicitly reject "residual zero = roadmap done". |
| F013 | Security hygiene | `SECURITY.md:14` | Medium | M | Repo correctly says caches and event stores must not be committed, but cache backfill ideas can easily persist local paths or IDs. | Treat cache backfill as private-only until fixture IDs, local path handling, and public-safe scans are hardened. |
| F014 | Observability | `ROADMAP.md:1` | Medium | M | Speed and quality targets existed as prose, but not as machine-enforced budget checks. | Add timing budget output and threshold checks to fast/full gates. |
| F015 | Generated artifacts | `dist/skills/codex/SKILL.md:1` | Low | S | Runtime skills are generated, but drift is easy if docs change without export. | Keep runtime skill sync lint in ops checks and mention generated-file workflow in roadmap. |

## Top 5 If You Fix Nothing Else

1. Build the M1 "message found -> bridge intake" command path before doing more live-adapter work.
2. Split `core.py` by responsibility while preserving public exports.
3. Split `test_core.py` by feature so slow and failing areas are obvious.
4. Keep `ISSUE_LIST.md` as the active TODO source and mark historical closeout files as history.
5. Add timing budget output for fast/full gates after the profiles settle.

## Quick Wins

- [x] Add `--profile fast` to `scripts/ops_check.py`.
- [x] Move dashboard-heavy checks out of the fast profile.
- [ ] Add a timing budget summary to `ops_check`.
- [ ] Split the first snapshot ledger tests out of `tests/test_core.py`.
- [ ] Keep README residual TODO linked to `ISSUE_LIST.md`.

## Things That Look Bad But Are Actually Fine

- The repo has many safety and boundary tests. That is intentional for a public-safe Discord-adjacent tool.
- `dist/skills/*/SKILL.md` duplicates content, but it is generated runtime projection rather than hand-maintained copy.
- Historical `docs/chat-context-*.md` files repeat context. They are useful audit trail as long as active TODO status is not taken from them.
- Local-private adapter docs mention cache and OCR paths. That is acceptable when the public package contract remains metadata-only.

## Open Questions

- Should `ops_check --profile fast` be the default, with `--profile full` required for PR?
- Should cache backfill become a private adapter repo/tool instead of living in this public-safe core?
- What is the first real sample set for parser quality, and where should private raw snapshots live?
- Is M5 send rehearsal blocked only on a test channel, or also on a named human reviewer?
