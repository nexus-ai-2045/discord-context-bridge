---
title: Discord Context Bridge 高インパクトTODO棚卸し
type: tasks
status: active
created: 2026-07-01
owner: codex
publication_gate: human_review_required
external_action: none
---

# Discord Context Bridge 高インパクトTODO棚卸し

## 判断軸

1. public-safe / read-only 境界を壊す事故を減らす。
2. 13工程MVPを、fixture、smoke、運用チェックで説明できる状態にする。
3. ローカルで設計、テスト計画、実装、テスト、運用保証まで閉じられる。
4. Discord送信、GitHub push / PR、公開共有は人間レビューまで止める。

## 高インパクト順

| 優先 | 状態 | TODO | 計画デザイン | テスト計画 | 実装 / テスト / 運用保証 |
|---|---|---|---|---|---|
| P0 | 完了 | 運用チェックCLIとREADME記述のズレ解消 | `ops_check.py` をREADME掲載コマンドでそのまま実行できるようにする。 | `python scripts/ops_check.py --skip-http` を実行し、既存の全チェックがgreenになることを見る。 | `--skip-http` を受理。`python scripts/ops_check.py --skip-http` 成功。 |
| P0 | 完了 | Windows timeout test の安定化 | timeout判定時間とkill後のcleanup時間を分け、運用判断を不安定な後始末時間に依存させない。 | `test_route_retry_decider_local_command_timeout_kills_process_group` と全pytestを実行する。 | `elapsed_ms` はtimeout判定時間、`cleanup_elapsed_ms` は後始末時間として分離。`195 passed`。 |
| P0 | 完了 | 正本worktreeの分離 | 汚れた旧cloneを直接rebaseせず、`origin/main` から新しいローカルworktreeを切る。 | `git status` と `git rev-list --left-right --count HEAD...origin/main` で同期状態を見る。 | `codex/recover-discord-bridge-local-20260702` は `origin/main` 起点で作成済み。 |
| P1 | 未着手 | 旧clone / 親repo削除状態の整理 | `_codex_work/discord-context-bridge-public-safe-events` と `Documents/nexus_ai/public/discord-context-bridge` の役割を分ける。 | 旧cloneの未整理差分、親repoの削除差分、専用repoの最新mainを比較する。 | 今回は正本作業を新worktreeへ退避。旧cloneと親repoは未変更。 |
| P1 | 未着手 | status dashboard の運用正本化 | `now / done / broken / blocked / next / github / residual` を1コマンドで安全に出す。 | dashboard script のfixtureとsecret/path/raw text非表示テストを追加する。 | raw本文、参加者名、secret、local path を出さない運用表示にする。 |
| P1 | 未着手 | private adapter 実機probeの失敗理由標準化 | `adapter_failed` を `failure_stage` と人間語reasonに分類する。 | timeout、permission、empty、dependency_missing のfixtureを追加する。 | 実Discord本文を出さず `text_output=omitted` を維持。 |
| P2 | 未着手 | 13工程E2Eの回帰保証拡張 | 13工程fixtureを中心に、review artifact、human gate、handoffの欠落を検出する。 | `fixture_13_step_e2e.py --json` のschema検査を増やす。 | `ops_check.py` のgreenをMVP closeout条件として維持。 |

## 今回の実行結果

- 外部操作: `git fetch` のみ。push、PR作成、公開操作はなし。
- Discord送信 / reaction / delete: なし。
- Git push / PR / 公開操作: なし。
- 実装: `scripts/ops_check.py`、`scripts/discord_route_retry_decider.py`
- テスト: `python -m pytest tests -q`、`python -m compileall src tests scripts`、`python scripts/ops_check.py --skip-http`
- 運用保証: README掲載形の運用チェックがローカルで成功する状態まで確認済み。
