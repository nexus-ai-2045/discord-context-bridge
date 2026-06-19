# Discord Context Bridge routes

この文書は、Discord Context Bridge の取得・制御・fallback 経路を混ぜないための路線図です。
目的は Discord を直接見続けることではなく、こちら側で文脈カード、返信前 gate、quick verdict を扱うことです。

## 経路の分類

| route class | route | 役割 | 使う場面 | 禁止境界 |
|---|---|---|---|---|
| `main` | `bot_private_ingest` | 受け取った Discord 本文を文脈カード / 返信前 gate へ流す本線 | `@discord` bot channel server や private adapter から本文を受け取れる時 | 本文、参加者名、token、snowflake 値を出力しない |
| `control` | `discord_configure` | bot token 設定の入口 | token が未設定で、ユーザーが明示的に設定する時 | token 値を表示しない。自動変更しない |
| `control` | `discord_access` | DM / group allowlist の入口 | pairing、allowlist、group access を明示管理する時 | snowflake 値を表示しない。自動変更しない |
| `visual_fallback` | `computer_use_discord` | 画面状態の人間確認 fallback | bot route が詰まり、今どの画面かだけ確認したい時 | 送信、reaction、delete、raw本文抽出をしない |
| `last_fallback` | `ocr_region` | region 指定の pixel fallback | bot route と visual確認だけでは足りない時 | full-screen capture をしない。region 必須。本文は出さない |

## 運用順序

1. `discord_plugin_route_status.py --json` で、今使うべき route を確認する。
2. `recommended_route=bot_private_ingest` なら、bot channel server / private adapter から本文を渡す。
3. `discord_configure_or_access` なら、`discord:configure` または `discord:access` の明示操作で control plane を直す。
4. bot route が使えない時だけ、Computer Use で画面状態を確認する。
5. それでも足りない時だけ、OCR region fallback を使う。

## status command

```bash
python3 scripts/discord_plugin_route_status.py --json
```

この command は route の状態だけを返します。

- `route_class=main`: 本線。文脈カード / 返信前 gate に流してよい。
- `route_class=control`: 設定・許可の制御面。本文取得ではない。
- `route_class=visual_fallback`: 画面確認用。自動送信や本文抽出には使わない。
- `route_class=last_fallback`: 最終 fallback。明示 region と安全境界が必須。

## main route smoke

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

## stoplines

- Discord send / reaction / delete はしない。
- token / cookie / webhook / browser profile を出力しない。
- raw Discord 本文 / 参加者名 / snowflake 値を出力しない。
- access.json の変更は、ユーザーが明示した plugin command なしではしない。
