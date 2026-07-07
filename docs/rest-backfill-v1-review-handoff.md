---
title: REST backfill v1 review handoff
status: active
updated: '2026-07-07'
---

# REST backfill v1 review handoff

## レビュー目的

この PR は、Discord URL / スレッド全文取得で使う read-only の主経路として Bot REST backfill を追加する。

PR #3 の完全保存ゲートを前提にし、実装側では次を固定する。

- bot token は環境変数の存在だけを使い、値を stdout / manifest / runtime skill に出さない。
- REST backfill の raw message は local private artifact にだけ保存する。
- 通常出力は metadata-only にし、message count、attachment candidate count、full / partial、rate limit 状態だけを返す。
- Chrome profile から user token、cookie、localStorage を抽出して使う経路は入れない。
- Discord send / reaction / edit / delete は引き続き無効。

## 目視レビューで見る点

- `scripts/discord_rest_backfill.py` が GET のみで、write API や send 系操作を持たないこと。
- `src/discord_context_bridge/core.py` の manifest が raw 本文、snowflake、local path、token 値を返さないこと。
- `thread-capture-plan` が REST route ready と full capture confirmed を混同していないこと。
- `discord_main_route_smoke.py` の bot private ingest smoke が、REST 推奨化後も fixture / private command 用 smoke として残っていること。
- generated runtime skills は `docs/operating-contract.md` と `capability/manifest.yaml` から再生成されたもので、手編集ではないこと。

## 元の Discord レス作業へ戻る道筋

1. PR #3 とこの PR を目視レビューする。
2. Bot REST route を使う場合は、ローカル環境に bot token を設定して `discord_rest_backfill.py` を対象 URL に対して実行する。
3. token がない場合は、private command / manual visible text のどちらで取得するかを決める。
4. 取得後は `coverage-report` と `thread-capture-plan` で `full_thread_confirmed` と freshness を確認する。
5. full capture にならない場合は、`partial` / `blocked` として closeout に理由を残す。
6. 文脈が揃ってから `context-passport`、必要なら `review-draft` / `guide-reply` に戻る。
7. Discord への送信は Codex が行わず、送信直前で対象・本文・操作を再確認する。

## 残 TODO

- 実 Discord 対象での live REST backfill は未実行。bot token と対象チャンネル権限の確認が必要。
- rate limit 実機挙動は fixture で contract 固定済みだが、実 API の retry 運用は未検証。
- 添付ファイルの実ダウンロードは v1 範囲外。現時点では attachment candidate count と ledger への接続まで。
- 既存の open PR #3 とこの PR の merge 順を守る。先に PR #3、次にこの PR。
- 元の Discord 返信本文はまだ作成・投入していない。全文取得または partial/block closeout 後に返信レビューへ戻る。
