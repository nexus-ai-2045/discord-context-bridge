# Changelog

## Unreleased

- `PROCESS_BOUNDARY.md` を追加し、Discord Context Bridge を「公開チャンネルに入る前の文脈理解と下書き補助」に絞る開発境界を明文化しました。
- FDE / Obsidian / 記事 draft をこの public package の PR に混ぜない repo routing を明記しました。

## 0.10.0 - 2026-06-19

- `scripts/read_visible_discord_text.py` を追加し、Discord 可視本文取得 adapter の public-safe な入口を用意しました。
- `scripts/local_smoke.py --source-command` を追加し、adapter を挟んだ実運用風 smoke を実行できるようにしました。
- adapter は stdout に可視本文だけを出し、Discord webhook URL、token 風文字列、profile path を検出した場合は出力前に停止します。
- Discord login、token、cookie、webhook、送信 tool を使わない public-safe 境界は維持しています。

## 0.9.0 - 2026-06-19

- `watch-passport` を追加し、local observer command の出力が変わった時だけスレッド文脈カードを自動更新できるようにしました。
- `watch-passport` でも文脈庫キーと明示文脈ファイルを使えるようにしました。
- README に次MVPの Discord 本文取得 adapter ロードマップと skeleton 契約を追加しました。
- Discord login、token、cookie、webhook、送信 tool を使わない public-safe 境界は維持しています。

## 0.8.0 - 2026-06-19

- ローカル文脈庫 `context-library.json` を追加しました。
- `context-upsert`、`context-list`、`audit-context-store` を追加し、サーバー/チャンネル/スレッド文脈を保存・監査・再利用できるようにしました。
- `context-passport` で `--server-context-key`、`--channel-context-key`、`--thread-context-key` を使い、文脈庫から明示文脈を読み込めるようにしました。
- MCP でも文脈庫の保存、一覧、監査、キー参照を使えるようにしました。

## 0.7.0 - 2026-06-19

- `context-passport` に `--server-context`、`--channel-context`、`--thread-context` を追加しました。
- MCP `get_context_passport_from_visible_text` でも、サーバー文脈、チャンネル文脈、スレッド文脈を明示入力できるようにしました。
- 可視本文だけに頼らず、サーバールール、チャンネル目的、スレッド目的を文脈カードへ併用できるようにしました。
- 文脈ソースと明示文脈の使用有無を日本語ラベルで返すようにしました。

## 0.6.0 - 2026-06-19

- `context-passport` と MCP `get_context_passport_from_visible_text` を追加しました。
- Discord 可視テキストから、スレッド目的、直近の流れ、暗黙の前提候補、ルール注意、温度感、自然な入り方を確認できるようにしました。
- 文脈把握を返信前ガイドより前の MVP 中核として扱い、Discord 送信 disabled の境界は維持しています。
- `scripts/local_smoke.py` で文脈パスポートも確認するようにしました。

## 0.5.0 - 2026-06-19

- HTTP MCP server に `--require-safe-store` を追加しました。
- tunnel / ChatGPT connector 公開前の store 監査に失敗した場合、HTTP MCP server を起動しないようにしました。
- `import-clipboard` と `scripts/local_smoke.py --from-clipboard` で、Discord 上でコピーした可視テキストを local clipboard から取り込めるようにしました。
- `watch-clipboard` で clipboard の変化を監視し、コピーし直した可視テキストだけを取り込めるようにしました。
- `guide-reply` と MCP `guide_reply_from_visible_text` で、Discord 可視テキストと返信 draft から会話ガイドを作れるようにしました。
- `guide-reply --source-command` と `watch-guide` で、private な local observer command から可視テキストを自動取得し、返信ガイドを更新できるようにしました。

## 0.4.0 - 2026-06-19

- tunnel 公開前に local event store を監査する `audit-store` CLI と MCP tool を追加しました。
- Discord webhook URL、token 風文字列、Discord snowflake ID、local absolute path を検出します。

## 0.3.0 - 2026-06-19

- Discord の rich copy に含まれる timestamp metadata を読み飛ばせるようにしました。
- `import-visible-text --dry-run` と MCP `import_visible_discord_text(dry_run=true)` で、保存前 preview を確認できるようにしました。

## 0.2.0 - 2026-06-18

- ChatGPT connector 向けに streamable HTTP MCP entrypoint を追加しました。
- `discord-context-bridge-mcp-http --host 127.0.0.1 --port 8000 --path /mcp` で `/mcp` を公開できます。
- Discord token、cookie、webhook URL、送信 tool を使わない local-first 境界は維持しています。

## 0.1.0 - 2026-06-18

- Discord の可視テキストを local event store に取り込む最小 package を公開しました。
- briefing と送信前 review の CLI / MCP stdio entrypoint を追加しました。
