---
title: Discord Context Bridge TODO批判的再検証
type: review
status: completed
created: 2026-07-01
updated: 2026-07-05
owner: codex
publication_gate: human_review_required
external_action: none
---

# Discord Context Bridge TODO批判的再検証

> Historical note: このファイルは 2026-07-01 時点の批判的 closeout 証跡です。
> 現在の active TODO 正本は repo root の `ISSUE_LIST.md`、現在の順序正本は `ROADMAP.md` です。
> このファイルの Green 判定は、当時の専用repo local verification 範囲に限定されます。

## 結論

判定: Green (discord-context-bridge 専用repoスコープ)

ローカル実装、テスト、dashboard、GitHub PR 状態は green。
この判定は `discord-context-bridge` 専用repo単体に限定する。
`nexus_ai` 親repo、旧clone、別packageの public 配下整理は、このリポの残務判定に含めない。

## 実測

- `git status --short --branch`: `## main...origin/main`
- `gh pr list --state open --json number,title,headRefName,mergeStateStatus,isDraft,url`: `[]`
- `python -m pytest tests/test_private_adapter_probe.py tests/test_live_mvp_status_scripts.py -q`: `53 passed`
- `python scripts/private_adapter_probe.py`: adapter 未設定を `dependency_missing` として分類、`text_output=omitted`
- `python scripts/discord_inventory_dashboard.py --json`: `operational_status.now.state=done`、`residual_count=0`、open PR 0、raw本文/参加者名/secret/path は omitted

## 論点

| 優先 | 論点 | 批判的見立て | 解決案 |
|---|---|---|---|
| P0 | 修正を守るテストが弱い | 初回TODOはgreen結果を記録していたが、`--skip-http` と `cleanup_elapsed_ms` を直接守るテストがなかった。 | `ops_check.parse_args(["--skip-http"])` と timeout payload の `cleanup_elapsed_ms` をテスト化。実装済み。 |
| P0 | 正本の範囲が曖昧 | 専用repo単体を正本にする前提なら解消済み。親repo側の状態は別lane。 | DCB closeoutでは `<DCB_SSOT>` の `main` / `origin/main` 同期を正本条件にする。 |
| P1 | TODOが実装PR向けに寄りすぎ | live Discord運用まで保証したように読める余地があった。 | 運用保証範囲を「local fixture / smoke / safe inventory / metadata-only dashboard」までと明記。live送信や実Discord取得は別実行タスク。 |
| P1 | 旧cloneがまだ残る | DCB 専用repoスコープでは残務に含めない。 | 旧cloneや親repo snapshotは、必要なら別laneのアーカイブ/移管判断として扱う。 |
| P2 | status dashboardの次実装が曖昧 | `discord_inventory_dashboard.py` が `now/done/broken/blocked/next/github/residual` を返す状態になった。 | dashboard JSON を closeout の運用正本にする。 |

## 推奨オーケストレーション

### Lane A: 専用repo closeout

- 目的: DCB 専用repoのロードマップ、実装、テスト、運用保証を閉じる。
- 現在地: `main...origin/main` 同期、open PR 0、dashboard residual 0。
- 次アクション: なし。新しい実Discord本文取得は別実行タスクとして扱う。
- stopline: Discord送信、reaction、delete、GitHub repository visibility 変更。

### Lane B: 親repo public配下整理

- 目的: `Documents/nexus_ai` の `public/discord-context-bridge` と `public/note-publishing-suite` 削除状態を整理する。
- 現在地: 大量削除と他lane差分が混在。
- 次アクション: `public/` だけの復旧/移管方針を決める。
- stopline: 他lane差分の巻き戻し、広域restore、push。

### Lane C: 旧clone整理

- 目的: `_codex_work/discord-context-bridge-public-safe-events` を正本候補から外すか、差分抽出だけにする。
- 現在地: 未整理差分あり、古い履歴からorigin/mainへ直接rebaseしない方が安全。
- 次アクション: `git diff --name-status` と新worktreeとの差分比較。
- stopline: 旧clone削除、stash破棄、reset。

## 決定前提ステータス

| Premise | Source | Status | Evidence gap |
|---|---|---|---|
| DCB 専用repo main が正本 | `main...origin/main`、open PR 0、dashboard residual 0 | accepted | なし |
| 今回の運用保証はlocal fixture/smoke/safe inventory範囲 | targeted pytest、adapter probe、inventory dashboard | accepted | live Discord実機本文取得は別タスク |
| 親repo/旧cloneはDCB closeout範囲外 | ユーザー指定スコープ | accepted | 必要なら別laneで扱う |

## 次の1手

1. DCB 専用repoの closeout は完了扱いにする。
2. 実Discord URL 本文の取得は、private adapter / 画面操作 / 人間レビューを伴う別実行タスクとして開始する。
3. 親repoや旧cloneの整理は、この判定に混ぜず、必要時だけ別laneで棚卸しする。
