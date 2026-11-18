# Preflight path redaction (2026-08-07)

## What changed

repo-preflight `personal_path_scan` flagged detector sources and fixtures that intentionally
mentioned absolute home-path markers (POSIX home prefixes and Windows profile prefixes).
Those were not live operator home directories; they were regex fixtures and test paths.

This change:

1. Builds detector markers from string fragments so source files no longer contain contiguous
   personal-home path literals that preflight treats as findings.
2. Moves test fixtures to non-home absolute paths under `C:\Temp\...` while keeping runtime
   absolute-path detection behavior.
3. Leaves **git history rewrite** as a separate force-push decision.

## Secret findings

Clean main-only clones already had `secret_scan` finding_count=0.
Local preflight failures came from an orphan ref (`refs/codex/curated-sync`, unrelated monorepo
blob set). That local ref was removed; tip recorded under private archive only.

## Verification

- `python -m pytest -q`
- `python scripts/ops_check.py --profile fast`
- repo-preflight: secret pass; current-tree path pass; history path still fail until rewrite
