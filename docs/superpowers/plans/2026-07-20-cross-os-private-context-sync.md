# Cross-OS private context sync PR proposal

> **Review target:** DCB の保存済み Discord 文脈 artifact を Mac / Windows 間で共有するための PR 案です。実装前レビュー用であり、この file は token、cookie、Discord raw text、実 ID、local absolute path を含みません。

## Goal

Discord Context Bridge が作る private-local artifact を、Mac と Windows のどちらからでも再利用できるようにする。

Tailscale は通信路、Syncthing は同期機構、DCB は artifact 生成と metadata-only 判定を担当する。DCB は Tailscale / Syncthing を制御せず、同期済み directory を `shared_snapshot_root` として扱う。

## Non-goals

- Discord app cache、Chrome profile、Local Storage、Session Storage、cookie、token を同期しない。
- DCB public core から Tailscale / Syncthing を起動・設定・停止しない。
- raw Discord text、参加者名、実 guild/channel/message ID、local absolute path を public output や tracked docs に出さない。
- GitHub PR に private artifact を含めない。
- 同期済み cache hit だけで「完全保存」「最新 live」「運用保証」と呼ばない。

## Architecture

```text
Mac acquisition lane
  Discord visible text / REST backfill / desktop cache read-only
  -> DCB private artifact writer
  -> shared_snapshot_root

Windows acquisition lane
  Discord visible text / REST backfill / desktop cache read-only
  -> DCB private artifact writer
  -> shared_snapshot_root

shared_snapshot_root
  transported by Tailscale
  synchronized by Syncthing or another user-managed private sync tool
  consumed by cache-inventory / report-latest / context-passport
```

## Artifact classes

| Class | Sync allowed | Notes |
|---|---:|---|
| append-only `text-snapshots.ndjson` | yes | system of record for observed visible text |
| raw capture JSONL under private snapshot root | yes | private-only, never public output |
| normalized Markdown / digest projection | yes | derived view; can be regenerated |
| manifest / ledger / closeout metadata | yes | should preserve `partial` / `blocked` state |
| Discord app cache / browser profile | no | source cache only; machine-local |
| token / cookie / credential provider output | no | never synced or echoed |

## Proposed PR slice

### Task 1: Document the cross-OS sync contract

**Files:**
- Modify: `README.md`
- Modify: `docs/operating-contract.md`
- Modify: `docs/routes.md`
- Add or modify: `docs/cross-os-private-context-sync.md`

- [ ] Explain that `DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT` / `configure-local-cache --shared-snapshot-root` may point at a user-managed private sync folder.
- [ ] Name Tailscale as a transport option and Syncthing as a sync option without making either a DCB dependency.
- [ ] State that only DCB artifacts are synced; source caches and credentials stay machine-local.
- [ ] State that Markdown is a projection and append-only ledger remains the strongest local record.
- [ ] Add a read-only `local_store` / `shared_snapshot` route class in `docs/routes.md` so saved artifacts are not confused with ingest, control, or visual fallback routes.
- [ ] Add README examples with placeholder paths only. Do not include real local absolute paths.

### Task 2: Add metadata-only sync diagnostics

**Files:**
- Modify: `src/discord_context_bridge/local_config.py`
- Modify: `src/discord_context_bridge/cache_inventory.py`
- Modify: `src/discord_context_bridge/cli.py`
- Modify: `tests/test_local_config.py`
- Modify: `tests/test_cache_inventory.py`

- [ ] Add optional metadata field for `shared_snapshot_root_role`, with values such as `local`, `synced_private`, `unknown`.
- [ ] Keep default behavior unchanged when the field is absent.
- [ ] Ensure CLI output omits actual local paths and only returns source labels.
- [ ] Add tests proving malformed config remains blocked and paths are not printed.
- [ ] If implemented, handle Windows drive paths, UNC-like paths, symlink / reparse-point ambiguity, and path traversal attempts as explicit review points before accepting the change.

### Task 3: Prevent false completion claims

**Files:**
- Modify: `docs/operating-contract.md`
- Modify: `tests/test_cache_inventory.py`
- Optional modify: `src/discord_context_bridge/cache_inventory.py`

- [ ] If snapshot came from a synced root, report it as saved local artifact coverage, not live Discord freshness.
- [ ] Keep `cache-inventory.decision` limited to `use_local_snapshot`, `refresh_exact_url_snapshot`, or `capture_visible_or_read_only_adapter`.
- [ ] Add a regression test that synced snapshot metadata does not become `live_current`.
- [ ] Document that Syncthing conflict files do not automatically merge into the append-only ledger.
- [ ] Add a review requirement for single-writer mode or conflict-file detection before any two-way write workflow is treated as reliable.

### Task 4: Review and verification

**Files:**
- Modify: `docs/superpowers/plans/2026-07-20-cross-os-private-context-sync.md`

- [ ] For docs-only proposal, run `git diff --check` and a personal-path / secret wording scan.
- [ ] Run `python -m pytest tests/test_local_config.py tests/test_cache_inventory.py -q`.
- [ ] Run `python scripts/lint_ingest_route_policy.py --json`.
- [ ] Run `python scripts/verify_ssot_projection.py --json` after any operating contract / capability manifest change.
- [ ] Run `git diff --check`.
- [ ] Perform security review for path leakage, credential leakage, and false freshness claims.
- [ ] Before a real PR, run `python3 scripts/pr_scope_guard.py --base origin/main --head HEAD --json` and `python3 scripts/boundary_logic_check.py --json`.

## Acceptance criteria

- A user can configure a shared private artifact root without exposing the path in default output.
- DCB treats cross-OS sync as user-managed storage, not as a credential or browser-profile bridge.
- `cache-inventory` distinguishes saved artifact freshness from live Discord freshness.
- Public docs describe safe artifact classes and forbidden machine-local classes.
- Tests cover config precedence, path omission, malformed config, and synced-root freshness labels.
- Syncthing conflict files and multiple-writer races are documented as unresolved unless explicitly tested.
- Runtime skill projection is regenerated only if SSOT files require it.

## Fable2 review packet

Please review this PR proposal for:

1. **Privacy boundary:** Are there any paths where cookie, token, browser profile, Local Storage, Session Storage, or raw Discord text could leak into public output or Git?
2. **Architecture boundary:** Is DCB correctly treating Tailscale as transport and Syncthing as user-managed sync, rather than owning cross-machine sync itself?
3. **Freshness boundary:** Does the proposal prevent synced local snapshots from being mistaken for current live Discord state?
4. **Implementation size:** Should this stay docs-first, or is the optional `shared_snapshot_root_role` metadata worth including in the first PR?
5. **Verification:** Are the proposed tests sufficient before a draft PR is opened?
6. **Conflict handling:** Is single-writer documentation enough for the first PR, or must conflict-file detection ship with it?

Preferred review result:

```text
verdict: adopt | revise | reject | split
must_fix:
- ...
nice_to_have:
- ...
test_gaps:
- ...
security_notes:
- ...
```

## Open decision

First PR should likely be **docs-first + route/contract clarification + tests for existing config/output boundaries**.

Adding `shared_snapshot_root_role` or conflict-file detection should be included only if Fable2 agrees it reduces ambiguity without expanding the PR too much.
