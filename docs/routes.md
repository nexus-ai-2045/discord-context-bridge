# Discord Context Bridgeの経路

この文書は、Discord Context Bridge の取得・制御・fallback 経路を混ぜないための路線図です。
目的は Discord を直接見続けることではなく、こちら側で文脈カード、返信前 gate、quick verdict を扱うことです。

## 経路の分類

| route class | route | 役割 | 使う場面 | 禁止境界 |
|---|---|---|---|---|
| `main` | `bot_private_ingest` | 受け取った Discord 本文を文脈カード / 返信前 gate へ流す本線 | `@discord` bot channel server や private adapter から本文を受け取れる時 | 本文、参加者名、token、snowflake 値を出力しない |
| `control` | `discord_configure` | bot token / secret-command provider 設定の入口 | token provider が未設定で、ユーザーが明示的に設定する時 | token 値や secret-command stdout を表示しない。自動変更しない |
| `control` | `discord_access` | DM / group allowlist の入口 | pairing、allowlist、group access を明示管理する時 | snowflake 値を表示しない。自動変更しない |
| `visual_fallback` | `computer_use_discord` | 画面状態の人間確認 fallback | bot route が詰まり、今どの画面かだけ確認したい時 | 送信、reaction、delete、raw本文抽出をしない |

## 運用順序

1. `discord_plugin_route_status.py --json` で、今使うべき route を確認する。
2. `recommended_route=bot_private_ingest` なら、bot channel server / private adapter から本文を渡す。
3. `discord_configure_or_access` なら、`discord:configure`、secret-command provider、または `discord:access` の明示操作で control plane を直す。
4. bot route が使えない時だけ、Computer Use で画面状態を確認する。
5. Discord本文取得で OCR / screenshot / vision へ拡張しない。必要なら DCB ではなく別 task として Type1 明示承認を取る。

Discord URL / 返信下書きの通常 ingress では、別 repository の Discord bot、ai-party、
ChatGPT connector、外部 MCP へ自動で切り替えない。上記 route が未設定なら
`not_configured` / `control_plane_not_ready` / `dependency_missing` の reason を返し、
スコープを広げる場合はユーザーの明示承認を取る。

## 状態確認command

```bash
python3 scripts/discord_plugin_route_status.py --json
```

対象ごとの状態確認では `--expected-url` を必ず渡します。未指定の一般statusは主経路を
絶対に `ready` にしません。

```bash
python3 scripts/discord_bot_live_verify.py --expected-url "<Discord対象URL>" --json
python3 scripts/discord_bot_route_preflight.py --expected-url "<Discord対象URL>"
python3 scripts/discord_plugin_route_status.py --expected-url "<Discord対象URL>" --json
```

`discord_bot_live_verify.py` が `live-verification.json` の唯一producerです。明示されたURLを
内部でguild・channelへ正規化し、Bot本人、対象guild、対象channelをDiscord APIのGETだけで
実測します。本文取得、Bot探索、権限変更、送信は行いません。3確認がすべて成功した時だけ、
現在credentialと対象種別へ署名したreceiptをmode `0600` でatomic保存します。

この command は route の状態だけを返します。
`DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND` がある場合、Keychain / credential store など repo 外の承認済み secret provider として扱います。
status command は provider 種別だけを返し、token 値、command stdout、vault内部情報は返しません。

tokenが見つかった状態は `configured` にすぎず、Bot経路の利用可能性を意味しません。
`live-verification.json` のprivate receiptが、現在credentialのdigest、expected targetを
consumer側で再計算したbinding、Bot本人確認、
対象guild所属、対象channel読取、有効期限をすべて満たした時だけ `live_verified` とします。
receiptがない既定状態は `credential_configured_but_live_unverified` で停止し、
`rest_backfill` と `bot_private_ingest` を `ready` にしません。receiptはmode `0600` の通常fileだけを
読み、symlink、期限切れ、credential変更、不完全な確認はfail-closedにします。公開出力には
credential digest、対象digest、実ID、URL、receipt pathを含めません。
receiptの `verified_at` から `expires_at` までの有効窓は24時間以内に限定します。

live verificationが受理するchannel typeの正本は
`SUPPORTED_TARGET_CHANNEL_TYPES`です。対象はtext（0）、announcement（5）、
announcement thread（10）、public thread（11）、private thread（12）、forum（15）、
media（16）に限定し、voice、category、stage、directoryなどはproducerとconsumerの
両方で拒否します。本文履歴APIを直接使える種別は
`MESSAGE_HISTORY_CHANNEL_TYPES`としてtext、announcement、各threadだけに分離します。

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
