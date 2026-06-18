from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .core import DEFAULT_STORE, fast_briefing, import_visible_text, load_events, review_reply_intent

MCP_DEPENDENCY_HELP = (
    "MCP server を使うには optional dependency が必要です: "
    "python3 -m pip install 'discord-context-bridge[mcp]'"
)


def _load_fastmcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise RuntimeError(MCP_DEPENDENCY_HELP) from exc
    return FastMCP


def build_server(*, store: Path = DEFAULT_STORE) -> Any:
    FastMCP = _load_fastmcp()
    server = FastMCP("discord-context-bridge")

    @server.tool()
    def import_visible_discord_text(
        text: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
    ) -> dict[str, Any]:
        """Discord の可視テキストを local event store に取り込みます。"""
        return import_visible_text(
            text,
            path=store,
            guild_label=guild_label,
            channel_label=channel_label,
        )

    @server.tool()
    def get_fast_briefing(limit: int = 3) -> dict[str, Any]:
        """直近文脈の短い briefing を返します。"""
        return fast_briefing(load_events(store), limit=limit)

    @server.tool()
    def review_reply_before_send(draft: str) -> dict[str, Any]:
        """送信前の返信 draft を直近文脈と照合します。"""
        return review_reply_intent(draft, load_events(store))

    return server


def main(argv: list[str] | None = None, *, run: Callable[[Any], None] | None = None) -> int:
    if argv is None:
        argv = []
    if argv:
        raise SystemExit("discord-context-bridge-mcp は引数なしで stdio MCP server として起動します。")
    server = build_server()
    if run is not None:
        run(server)
    else:
        server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
