# Changelog

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
