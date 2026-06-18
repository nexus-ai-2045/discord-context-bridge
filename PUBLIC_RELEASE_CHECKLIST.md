# Public Release Checklist

Discord Context Bridge を public repository に出す前に、この checklist を使います。

## Repository Target

- remote が意図した public Nexus repository を指している。
- git author name / email が public repository に適した名義になっている。
- release commit が、この public package と必要 metadata だけを含んでいる。

## Privacy And Secrets

- Discord user token、bot token、webhook URL、cookie、authorization header がない。
- 実 guild ID、channel ID、user ID、handle、private message、DM 本文がない。
- maintainer machine の local absolute path が public docs / tests にない。
- private `.env`、browser profile、database、cache、event store がない。

## Language Boundary

- 利用者向け文書の基本言語が日本語になっている。
- CLI / API のユーザー向けメッセージが日本語を既定にしている。
- JSON key、Python identifier、package name、command name は英語のままでよい。
- 英語だけの説明が増えた場合、対応する日本語説明がある。

## Package Boundary

- pyproject の version と CHANGELOG が今回の public capability を表している。
- Browser login と account automation は、この public nucleus の scope 外。
- outbound sending は既定で disabled で、この package には実装経路がない。
- MCP server は local event store を読むだけで、Discord への送信 tool を公開しない。
- ChatGPT connector 用の HTTP MCP は `/mcp` だけを公開し、tunnel 前に event store の private data を確認する。
- fixture は合成データで、公開してよい。
- README が、この package の「できること / しないこと」を説明している。
- SECURITY.md が sensitive data exposure の扱いを説明している。

## Verification

```bash
python3 -m pytest tests -q
python3 -m compileall src tests
python3 -m pip install ".[mcp]"
python3 - <<'PY'
from discord_context_bridge.mcp_server import build_server
server = build_server()
assert getattr(server, "name", "") == "discord-context-bridge"
PY
discord-context-bridge-mcp-http --help
python3 - <<'PY'
import tomllib
from pathlib import Path
version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
assert version
PY
rg -n "(DISCORD_(BOT_TOKEN|WEBHOOK_URL)|discord(app)?\\.com/api/webhooks|Authorization|Bearer |mfa\\.|[A-Za-z0-9_-]{24}\\.[A-Za-z0-9_-]{6}\\.[A-Za-z0-9_-]{27})" .
```

最後の `rg` command は、この checklist または意図的な fake test string だけに
match すること。real credential に match してはいけません。

## Human Decision Before Push

public push には、target remote を明示した current-session GO が必要です。
