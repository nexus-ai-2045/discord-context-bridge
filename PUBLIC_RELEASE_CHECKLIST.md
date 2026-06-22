# Public Release Checklist

Discord Context Bridge を public repository に出す前に、この checklist を使います。

## Repository Target

- remote が意図した public Nexus repository を指している。
- `python3 scripts/gh_guard.py --switch` で GitHub account と remote owner の一致を確認している。
- git author name / email が public repository に適した名義になっている。
- release commit が、この public package と必要 metadata だけを含んでいる。

## Privacy And Secrets

- Discord user token、bot token、webhook URL、cookie、authorization header がない。
- 実 guild ID、channel ID、user ID、handle、private message、DM 本文がない。
- maintainer machine の local absolute path が public docs / tests にない。
- private `.env`、browser profile、database、cache、event store がない。
- local context library は `.local/discord-context-bridge/context-library.json` などの非公開領域に置き、実データを commit しない。

## Language Boundary

- 利用者向け文書の基本言語が日本語になっている。
- CLI / API のユーザー向けメッセージが日本語を既定にしている。
- JSON key、Python identifier、package name、command name は英語のままでよい。
- 英語だけの説明が増えた場合、対応する日本語説明がある。

## Package Boundary

- pyproject の version と CHANGELOG が今回の public capability を表している。
- release version は `python3 scripts/bump_version.py --part patch|minor|major --write` で採番し、`pyproject.toml` と `CHANGELOG.md` を同時更新している。
- `python3 scripts/bump_version.py --check` が成功している。
- Browser login と account automation は、この public nucleus の scope 外。
- outbound sending は既定で disabled で、この package には実装経路がない。
- MCP server は local event store / local context library を読むだけで、Discord への送信 tool を公開しない。
- 文脈パスポートはスレッド目的 / 流れ / 前提 / ルール注意 / 温度感の確認だけを行い、Discord 送信はしない。
- 返信ガイドは context / draft の照合だけを行い、Discord 送信はしない。
- 自動取得は `--source-command` で local observer の stdout を読むだけにし、browser profile / Discord cookie / token を public package に入れない。
- ChatGPT connector 用の HTTP MCP は `/mcp` だけを公開し、tunnel 前に event store の private data を確認する。
- HTTP MCP を tunnel へ出す場合は `--require-safe-store` で起動前監査を必須にできる。
- import は `dry_run` で保存前 preview できる。
- clipboard import は local clipboard だけを読み、Discord token / cookie / webhook を要求しない。
- tunnel 公開前に `audit-store` または MCP `audit_event_store_before_tunnel` を実行できる。
- tunnel 公開前に `audit-context-store` または MCP `audit_context_library_before_tunnel` を実行できる。
- fixture は合成データで、公開してよい。
- README が、この package の「できること / しないこと」を説明している。
- SECURITY.md が sensitive data exposure の扱いを説明している。

## Verification

```bash
python3 scripts/ops_check.py
python3 scripts/ops_check.py --gh --gh-switch
python3 -m pytest tests -q
python3 -m compileall src tests
python3 -m pip install ".[mcp]"
python3 - <<'PY'
from discord_context_bridge.mcp_server import build_server
server = build_server()
assert getattr(server, "name", "") == "discord-context-bridge"
PY
discord-context-bridge-mcp-http --help
discord-context-bridge-mcp-http --store /tmp/discord-context-events.ndjson --require-safe-store --help
discord-context-bridge --store /tmp/discord-context-events.ndjson audit-store
discord-context-bridge --context-store /tmp/discord-context-library.json audit-context-store
python3 scripts/bump_version.py --check
rg -n "(DISCORD_(BOT_TOKEN|WEBHOOK_URL)|discord(app)?\\.com/api/webhooks|Authorization|Bearer |mfa\\.|[A-Za-z0-9_-]{24}\\.[A-Za-z0-9_-]{6}\\.[A-Za-z0-9_-]{27})" .
```

最後の `rg` command は、この checklist または意図的な fake test string だけに
match すること。real credential に match してはいけません。

## Human Decision Before Push

public push には、target remote を明示した current-session GO が必要です。
