# Discord Context Bridge

Discord Context Bridge は、Discord で見えている会話テキストを、
AI アシスタントが安全に扱える文脈フィードへ変換する local-first な小さな橋です。

目的は Discord アカウントの自動操作ではありません。人間のユーザーが、現在の会話を理解し、
抜けている前提を見つけ、返信する前に自分の返信意図を確認できるようにすることです。

## 基本言語

この package の基本言語は日本語です。

- README、SECURITY、公開 checklist などの利用者向け文書は日本語を既定にします。
- CLI / API のユーザー向けメッセージは日本語を既定にします。
- コード識別子、JSON key、コマンド名、package 名は英語のまま維持します。
- 英語は protocol 互換、検索性、OSS 利用者への補助説明が必要な場所に限定します。

## できること

- 見えている、またはコピーした Discord テキストを local append-only event store に取り込みます。
- 直近メッセージから短い briefing を作ります。
- 返信を書く前に、足りない前提や文脈ズレを検出します。
- ユーザーの返信意図を直近会話と照合します。
- Discord への送信は既定で禁止します。

## しないこと

- Discord にログインしません。
- Discord user token、bot token、cookie、webhook URL を読みません。
- 単体で private data を scraping しません。
- Discord にメッセージを送信しません。
- 実 guild ID、channel ID、handle、private message 本文を含めません。

## 使い方

test を実行します。

```bash
python3 -m pytest tests -q
```

visible text fixture を取り込みます。

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  --store /tmp/discord-context-events.ndjson \
  import-visible-text \
  --input tests/fixtures/visible_text.txt \
  --guild example-community \
  --channel general
```

briefing を表示します。

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  --store /tmp/discord-context-events.ndjson \
  fast-briefing
```

返信意図を確認します。

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  --store /tmp/discord-context-events.ndjson \
  review-intent \
  --draft "これは公開時期の話で、足りない前提を確認してから返信します。"
```

## MCP server として使う

Codex / Claude から常用する場合は、CLI より MCP server を入口にします。

```bash
python3 -m pip install ".[mcp]"
discord-context-bridge-mcp
```

MCP tool は 3 つです。

- `import_visible_discord_text`: Discord の可視テキストを local event store に取り込みます。
- `get_fast_briefing`: 直近文脈の短い briefing を返します。
- `review_reply_before_send`: 送信前の返信 draft を直近文脈と照合します。

送信 tool はありません。返信は人間が Discord 側で送信する前提です。

### ChatGPT connector 用に HTTP で起動する

ChatGPT Apps / Connector から試す場合は、streamable HTTP MCP server として `/mcp` を出します。

```bash
python3 -m pip install ".[mcp]"
discord-context-bridge-mcp-http --host 127.0.0.1 --port 8000 --path /mcp
```

local で起動したあと、Secure MCP Tunnel、ngrok、Cloudflare Tunnel などで HTTPS URL を作り、
ChatGPT の connector URL に `https://.../mcp` を登録します。

注意:

- tunnel で外部公開する前に、event store に実 private data が混ざっていないか確認してください。
- この package は送信 tool を公開しません。
- Discord token、cookie、webhook URL は不要です。

## データ契約

event store は newline-delimited JSON です。local-only で扱い、実会話データを含む場合は
commit しません。

各 event は意図的に小さく保ちます。

- `observed_at`
- `source`
- `guild_label`
- `channel_label`
- `author_label`
- `text_snippet`
- `actions_allowed`
- `private_surface`

label は human-safe な alias にしてください。その境界を明示的に選んだ場合を除き、
実 private identifier は保存しません。

## 公開安全境界

この repository は、公開可能な nucleus package として設計しています。
含める fixture は合成データだけで、credential は含めません。browser、desktop app、
automation profile から読み取る adapter は、個別の security review が終わるまで
この package の外に置きます。
