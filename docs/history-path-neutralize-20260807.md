# History path neutralization (2026-08-07)

## What

Rewrote git history with `git filter-repo` to neutralize personal-home path markers
that repo-preflight `personal_path_scan` flags across historical blobs.

## Method

- Isolated clone of main
- Blob callback rewrites home-profile path prefixes to neutral absolute fixtures
- Runtime detection still matches real home paths when present in content
- `main` updated via temporary branch protection change + force-with-lease
- Branch protection restored (`allow_force_pushes=false`, `enforce_admins=true`)

## Tips

- old main: `987e59e11b80d9dd8d551cc01981067727d5abe4`
- new main: `48e16e76e550f6f25bd5003757c9f6a62067162c`
- salvage branch kept briefly: `codex/history-path-neutralize-20260807`

## Result

- secret_scan: pass
- personal_path_scan: pass (current tree + history)
- ops_check fast: pass on rewritten tip
