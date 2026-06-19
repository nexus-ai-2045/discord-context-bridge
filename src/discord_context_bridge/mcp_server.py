from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from .core import (
    DEFAULT_CONTEXT_STORE,
    DEFAULT_STORE,
    audit_context_store,
    audit_event_store,
    context_passport_from_text,
    fast_briefing,
    get_context_document,
    guide_reply_from_text,
    import_visible_text,
    list_context_documents,
    load_events,
    review_reply_intent,
    upsert_context_document,
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
    context_store: Path = DEFAULT_CONTEXT_STORE,
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
    def upsert_context_library_entry(
        kind: str,
        key: str,
        text: str,
        source: str = "manual",
    ) -> dict[str, Any]:
        """サーバー/チャンネル/スレッド文脈をローカル文脈庫へ保存します。"""
        return upsert_context_document(kind, key, text, path=context_store, source=source)

    @server.tool()
    def list_context_library_entries() -> dict[str, Any]:
        """ローカル文脈庫の一覧を返します。本文は返さず summary だけ返します。"""
        return list_context_documents(context_store)

    @server.tool()
    def audit_context_library_before_tunnel() -> dict[str, Any]:
        """tunnel 公開前にローカル文脈庫の安全性を確認します。"""
        return audit_context_store(context_store)

    @server.tool()
    def review_reply_before_send(draft: str) -> dict[str, Any]:
        """送信前の返信 draft を直近文脈と照合します。"""
        return review_reply_intent(draft, load_events(store))

    @server.tool()
    def guide_reply_from_visible_text(
        text: str,
        draft: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
    ) -> dict[str, Any]:
        """Discord の可視テキストと返信 draft から会話ガイドを返します。"""
        return guide_reply_from_text(
            text,
            draft,
            guild_label=guild_label,
            channel_label=channel_label,
        )

    @server.tool()
    def get_context_passport_from_visible_text(
        text: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
        server_context: str = "",
        channel_context: str = "",
        thread_context: str = "",
        server_context_key: str = "",
        channel_context_key: str = "",
        thread_context_key: str = "",
    ) -> dict[str, Any]:
        """Discord の可視テキストからスレッド文脈カードを返します。"""
        return context_passport_from_text(
            text,
            guild_label=guild_label,
            channel_label=channel_label,
            server_context="\n".join(
                part
                for part in [
                    get_context_document("server", server_context_key, path=context_store) if server_context_key else "",
                    server_context,
                ]
                if part.strip()
            ),
            channel_context="\n".join(
                part
                for part in [
                    get_context_document("channel", channel_context_key, path=context_store) if channel_context_key else "",
                    channel_context,
                ]
                if part.strip()
            ),
            thread_context="\n".join(
                part
                for part in [
                    get_context_document("thread", thread_context_key, path=context_store) if thread_context_key else "",
                    thread_context,
                ]
                if part.strip()
            ),
        )

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
    parser.add_argument("--context-store", type=Path, default=DEFAULT_CONTEXT_STORE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--path", default="/mcp")
    parser.add_argument(
        "--require-safe-store",
        action="store_true",
        help="起動前に event store を監査し、安全でない場合は HTTP MCP server を起動しない",
    )
    return parser


def main_http(argv: list[str] | None = None, *, run: Callable[[Any], None] | None = None) -> int:
    parser = build_http_parser()
    args = parser.parse_args(argv)
    if args.require_safe_store:
        audit = audit_event_store(args.store)
        if not audit["safe_for_tunnel"]:
            raise SystemExit(
                "保存データの監査に失敗しました: "
                f"外部公開前に確認が必要です issue_count={audit['issue_count']} store={audit['store']}"
            )
    server = build_server(
        store=args.store,
        context_store=args.context_store,
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
