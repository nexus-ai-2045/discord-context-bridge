from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from .core import (
    DEFAULT_STORE,
    audit_event_store,
    fast_briefing,
    import_visible_text,
    load_events,
    review_reply_intent,
)

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


def build_server(
    *,
    store: Path = DEFAULT_STORE,
    host: str = "127.0.0.1",
    port: int = 8000,
    http_path: str = "/mcp",
) -> Any:
    FastMCP = _load_fastmcp()
    server = FastMCP(
        "discord-context-bridge",
        host=host,
        port=port,
        streamable_http_path=http_path,
    )

    @server.tool()
    def import_visible_discord_text(
        text: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Discord の可視テキストを local event store に取り込みます。"""
        return import_visible_text(
            text,
            path=store,
            guild_label=guild_label,
            channel_label=channel_label,
            dry_run=dry_run,
        )

    @server.tool()
    def get_fast_briefing(limit: int = 3) -> dict[str, Any]:
        """直近文脈の短い briefing を返します。"""
        return fast_briefing(load_events(store), limit=limit)

    @server.tool()
    def audit_event_store_before_tunnel() -> dict[str, Any]:
        """tunnel 公開前に event store の安全性を確認します。"""
        return audit_event_store(store)

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


def build_http_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discord Context Bridge を streamable HTTP MCP server として起動する"
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--path", default="/mcp")
    return parser


def main_http(argv: list[str] | None = None, *, run: Callable[[Any], None] | None = None) -> int:
    parser = build_http_parser()
    args = parser.parse_args(argv)
    server = build_server(
        store=args.store,
        host=args.host,
        port=args.port,
        http_path=args.path,
    )
    if run is not None:
        run(server)
    else:
        server.run(transport="streamable-http", mount_path=args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
