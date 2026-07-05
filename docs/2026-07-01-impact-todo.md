---
title: Discord Context Bridge 高インパクトTODO棚卸し
type: tasks
status: completed
created: 2026-07-01
updated: 2026-07-05
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
| P1 | 完了 (2026-07-04) | 旧clone / 親repo削除状態の整理 | `_codex_work/discord-context-bridge-public-safe-events` と `Documents/nexus_ai/public/discord-context-bridge` の役割を分ける。 | 旧cloneの未整理差分、親repoの削除差分、専用repoの最新mainを比較する。 | Windows 側で実施。親repo内旧snapshotは全79 fileをblob検証 (75は専用repo履歴に存在、残4は旧cloneの未merge commitと一致=データロストゼロ) して retained 置き場へ退避。未merge commit は最新mainへrebaseし PR #34 として merge。専用repo main を唯一の正本に統一。 |
| P1 | 完了 | status dashboard の運用正本化 | `now / done / broken / blocked / next / github / residual` を1コマンドで安全に出す。 | dashboard script のfixtureとsecret/path/raw text非表示テストを追加する。 | `discord_inventory_dashboard.py --json` が `operational_status` を返す。working tree / origin/main 差分 / open PR / next action を public-safe に表示し、raw本文、参加者名、secret、local path を出さない。 |
| P1 | 完了 (2026-07-05) | private adapter 実機probeの失敗理由標準化 | `adapter_failed` を `failure_stage` と人間語reasonに分類する。 | timeout、permission、empty、dependency_missing のfixtureを追加する。 | `private_adapter_probe.py` は未設定を `dependency_missing`、timeout を `timeout`、permission / empty / not_found 系を個別 reason として返す。`tests/test_private_adapter_probe.py` と `tests/test_live_mvp_status_scripts.py` で `text_output=omitted` と本文非表示を固定。 |
| P2 | 完了 | 13工程E2Eの回帰保証拡張 | 13工程fixtureを中心に、review artifact、human gate、handoffの欠落を検出する。 | `fixture_13_step_e2e.py --json` のschema検査を増やす。 | `regression_contract` で review artifact / human gate / handoff packet の確認状態を固定。`ops_check.py` に status dashboard smoke も接続し、greenをMVP closeout条件として維持。 |

## 2026-07-05 運用保証 closeout

- スコープ: `discord-context-bridge` 専用repo単体。
- GitHub: PR #40 と PR #41 は人間承認後に merge 済み。open PR は 0。
- リポ状態: `main...origin/main` 同期、working tree clean。
- Discord送信 / reaction / delete: なし。
- 実Discord本文出力 / 保存: なし。probe / dashboard は `text_output=omitted` を維持。
- GitHub repository visibility 変更: なし。
- テスト: `python -m pytest tests/test_private_adapter_probe.py tests/test_live_mvp_status_scripts.py -q` は `53 passed`。
- probe: `python scripts/private_adapter_probe.py` は adapter 未設定を `dependency_missing` として安全に分類し、本文出力は `omitted`。
- dashboard: `python scripts/discord_inventory_dashboard.py --json` は `operational_status.now.state=done`、`residual_count=0`、open PR 0。
- 運用チェック: `python scripts/ops_check.py --skip-http` を closeout gate として維持する。

## 残務判定

`discord-context-bridge` 専用repoのロードマップ残務は、この棚卸し上は 0。
README の「次MVP: Discord 本文取得 adapter」は、public package が取得本体を抱え込まないための将来拡張方針であり、
このリポの現在MVP closeout残務には数えない。

実Discord URL の本文そのものを読む作業は、外部画面 / private adapter / 人間操作に依存する別実行タスク。
この closeout は「リポ実装、fixture / smoke、metadata-only 運用保証、public-safe 境界」までを完了条件にする。
