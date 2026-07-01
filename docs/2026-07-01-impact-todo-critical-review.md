---
title: Discord Context Bridge TODO批判的再検証
type: review
status: active
created: 2026-07-01
owner: codex
publication_gate: human_review_required
external_action: none
---

# Discord Context Bridge TODO批判的再検証

## 結論

判定: Yellow

ローカル実装と検証はgreen。意思決定前提としては、旧cloneと親repo削除状態の扱いが未決のためYellow。
このTODOを「専用repoの次PR候補」として使うのは可能。ただし「nexus_ai全体の残務ゼロ」や
「旧clone/親repo整理まで完了」とは扱わない。

## 実測

- `python -m pytest tests -q`: `185 passed`
- `python scripts/ops_check.py --skip-http`: 全チェック成功
- `python scripts/discord_inventory_dashboard.py --json`: `ok=true`、raw本文/参加者名/secret/path は omitted
- `git rev-list --left-right --count HEAD...origin/main`: `0 0`
- 親repo `Documents/nexus_ai`: `public/discord-context-bridge` と `public/note-publishing-suite` が削除状態

## 論点

| 優先 | 論点 | 批判的見立て | 解決案 |
|---|---|---|---|
| P0 | 修正を守るテストが弱い | 初回TODOはgreen結果を記録していたが、`--skip-http` と `cleanup_elapsed_ms` を直接守るテストがなかった。 | `ops_check.parse_args(["--skip-http"])` と timeout payload の `cleanup_elapsed_ms` をテスト化。実装済み。 |
| P0 | 正本の範囲が曖昧 | 新worktreeは専用repoとして健全だが、親repo側の削除状態を「解決済み」と言うと前提ロンダリングになる。 | 専用repo lane と親repo整理 lane を分ける。親repo復旧は別TODO/別承認で扱う。 |
| P1 | TODOが実装PR向けに寄りすぎ | 運用保証の言葉が、live Discord運用まで保証したように読める余地がある。 | 「local fixture / smoke / safe inventory まで」と明記し、live送信や実Discord取得は保証外に残す。 |
| P1 | 旧cloneがまだ残る | `_codex_work/discord-context-bridge-public-safe-events` には未整理差分があり、正本と誤認しやすい。 | 旧cloneを archive / compare / delete candidate に分類し、必要差分だけ新worktreeへ移す。 |
| P1 | 親repoのpublic配下削除が大きい | `public/discord-context-bridge` だけでなく `public/note-publishing-suite` も削除状態。範囲が広い。 | 親repo側は `public/` 整理専用laneを作り、復旧するかsubmodule/外部repo参照へ移すか決める。 |
| P2 | status dashboardの次実装が曖昧 | `discord_inventory_dashboard.py` はあるが、TODO上の `now/done/broken/blocked/next/github/residual` 正本化とはまだ一致しない。 | 既存dashboardを拡張するか、`post_merge_closeout_report.py` と統合するかを先に設計する。 |

## 推奨オーケストレーション

### Lane A: 専用repo PR候補

- 目的: 今回のP0修正とTODO/批判的レビューを専用repoで閉じる。
- 現在地: local green、branchは `codex/discord-context-bridge-impact-todo-20260701`。
- 次アクション: 差分レビュー、必要ならcommit、push/PRは人間承認後。
- stopline: push、PR作成、公開共有。

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
| 新worktreeは専用repoの正本候補として使える | `origin/main` 起点、`0 0`、tests green | accepted | push/PR前のGitHub権限確認 |
| 今回の運用保証はlocal fixture/smoke範囲 | pytest、ops_check、inventory dashboard | accepted | live Discord実機確認は未実施 |
| 親repoのpublic削除状態は未解決 | `Documents/nexus_ai` git status | needs verification | 削除意図、復旧方針、関連lane所有者 |
| 旧cloneを正本として扱わない | ahead/behindと未整理差分 | needs verification | 旧clone内の未移植価値差分 |

## 次の1手

1. Lane Aを閉じるなら、まずローカルcommit候補として差分をレビューする。
2. Lane Bは別枠で、親repoの `public/` 削除状態だけを対象に棚卸しする。
3. Lane Cは旧cloneを archive candidate として、未移植差分だけ抽出する。
