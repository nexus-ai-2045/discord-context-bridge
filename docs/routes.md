# Discord Context Bridgeの経路

この文書は、Discord Context Bridge の取得・制御・fallback 経路を混ぜないための路線図です。
目的は Discord を直接見続けることではなく、こちら側で文脈カード、返信前 gate、quick verdict を扱うことです。

## 経路の分類

取得・承認の正本は [operating-contract.md](operating-contract.md)。以下はその案内であり、可視取得の承認を追加しない。

| 経路 | 役割 | 停止条件 |
|---|---|---|
| ローカル `target-cache-lookup` | canonical保存先の対象確認。通常照会で外部cacheを全走査しない | 保存前の外部cacheは未探索。発見・容量棚卸しが必要なら `cache-inventory` |
| 既存ボット・private adapter | 対象へのread-onlyアクセスが確認済みの場合の取得 | 設定済みだけでアクセス成功としない |
| 内部ブラウザの可視DOM | Botなしの明示取得の既定。取得後は直ちに保存・対象確認 | ingress前、未承認、対象切替、login redirect、保存失敗なら停止 |
| Chromeの可視DOM | 内部ブラウザが使えない時の承認済み二次経路 | 勝手な起動・移行・認証変更をしない |
| Computer Use | 本人が別途明示承認した範囲の操作 | 自動fallbackにしない。OCRを本文取得にしない |

## 運用順序

1. 既存のingress・対象・鮮度・route判定を通す。通常のcanonical対象確認は `target-cache-lookup`、全体棚卸しは `cache-inventory` と分ける。
2. 保存済み文脈が不足すれば、既存ボット/adapterの可否を確認し、内部ブラウザの承認済み可視DOM経路へ進む。
3. 取得した本文は `bridge-intake` または [明示相談](explicit-reply-consultation.md)へ渡し、保存receiptと観測URLの対象一致を確認する。
4. 返信前には起点・返信対象・直前10件、短いthreadでは履歴終端、未解決参照を確認する。毎回の全文captureを要求せず、不足なら候補生成を停止する。
5. LCS正本と対人skillを参照して本人への短い案を作る。ローカル単語判定の `guide-reply` とAIによる相談支援は別工程。

本文・資格情報を別サービスへ渡す経路を追加せず、投稿は本人の判断とする。常駐UI・自動発動はこの経路に含めない。

## 状態確認command

```bash
python3 scripts/discord_plugin_route_status.py --json
```

この command は route の状態だけを返します。
`DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND` がある場合、Keychain / credential store など repo 外の承認済み secret provider として扱います。
status command は provider 種別だけを返し、token 値、command stdout、vault内部情報は返しません。

bot tokenの選択順は、process環境変数、`DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND`、
`DISCORD_CONTEXT_BRIDGE_CHANNEL_DIR/.env` の順です。channelの `.env` はshellとして実行せず、
mode `0600` の通常fileからbot token用の完全一致keyを1件だけ読みます。
symlink、権限が広いfile、重複key、不正なtoken値はfail-closedにします。preflightと実取得は
同じprovider判定を使うため、設定済み表示だけが成功して実取得で欠落する状態を許しません。

- `route_class=main`: 本線。文脈カード / 返信前 gate に流してよい。
- `route_class=control`: 設定・許可の制御面。本文取得ではない。
- `route_class=visual_fallback`: 画面確認用。自動送信や本文抽出には使わない。
- `route_class=last_fallback`: 最終 fallback。明示 region と安全境界が必須。

## main経路のsmoke

`main` route の運用保証は、status と private ingest をまとめて確認します。

```bash
cat /private/path/from-discord-channel.txt | \
  python3 scripts/discord_main_route_smoke.py --json
```

成功条件は次の通りです。

- `route_ready=true`
- `ingest_ready=true`
- `parsed >= min_parsed`
- `context_ready=true`
- `quick_verdict` が `go` / `wait` / `ask-context` / `risky` のいずれか
- `text_output=omitted`
- `outbound_actions=disabled`

実イベントがまだ届いているかだけを確認する場合は、channel event probe を使います。
これは本文や file name を出さず、text event 候補数と media 件数だけを返します。

```bash
python3 scripts/discord_channel_event_probe.py --json
```

`failure_stage=no_text_event_source` の場合、bot channel server / private adapter から smoke に渡せる本文イベントが
まだ届いていない状態です。`source_empty` や parser failure とは分けて扱います。

## E2E確認

fixture / private text と実イベント probe をまとめて見る場合は E2E check を使います。

```bash
python3 scripts/e2e_discord_route_check.py \
  --input tests/fixtures/discord_rich_copy.txt \
  --json
```

実イベント到達まで完了条件に含める場合は `--require-channel-event` を付けます。
この時に `blocked_stage=no_text_event_source` なら、ingest や parser ではなく text event 未着が原因です。

## 停止境界

- Discord send / reaction / delete はしない。
- token / cookie / webhook / browser profile を出力しない。
- raw Discord 本文 / 参加者名 / snowflake 値を出力しない。
- access.json の変更は、ユーザーが明示した plugin command なしではしない。


## ボットの対象別アクセス確認

本文取得の前に [ボットの対象別アクセス確認](bot-live-verification.md) を行う。
対象未指定、記録なし、資格情報や対象の不一致、期限切れの場合は、設定済みでも取得経路を利用可能と判定しない。
