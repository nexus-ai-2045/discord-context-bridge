from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from .core import (
    DEFAULT_CONTEXT_STORE,
    DEFAULT_STORE,
    audit_context_store,
    audit_event_store,
    build_discord_post_send_closeout_packet,
    build_discord_send_staging_packet,
    build_reply_context_gate,
    build_reply_context_gate_from_messages,
    context_operating_mode_from_text,
    context_passport_from_text,
    digest_context_from_text,
    fast_briefing,
    get_context_document,
    guide_reply_from_text,
    import_visible_text,
    list_context_documents,
    load_events,
    plan_discord_url_read as plan_discord_url_read_core,
    review_reply_intent,
    snapshot_visible_text,
    upsert_context_document,
    verify_chrome_extension_fill_only_dry_run,
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
    snapshot_store: Path | None = None,
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
    def review_reply_before_send(
        draft: str,
        understanding_confirmed: bool = False,
        thread_root_present: bool = False,
        reply_target_present: bool = False,
        prior_message_count: int = 0,
        history_exhausted: bool = False,
        unresolved_reference_count: int = 0,
    ) -> dict[str, Any]:
        """送信前の返信 draft を直近文脈と照合します。"""
        gate = build_reply_context_gate(
            thread_root_present=thread_root_present,
            reply_target_present=reply_target_present,
            prior_message_count=prior_message_count,
            history_exhausted=history_exhausted,
            unresolved_reference_count=unresolved_reference_count,
        )
        return review_reply_intent(
            draft,
            load_events(store),
            understanding_confirmed=understanding_confirmed,
            reply_context_gate=gate,
        )

    @server.tool()
    def plan_reply_context_before_draft(
        thread_root_present: bool = False,
        reply_target_present: bool = False,
        prior_message_count: int = 0,
        history_exhausted: bool = False,
        unresolved_reference_count: int = 0,
        fetched_message_count: int = 0,
        max_context_messages: int = 100,
    ) -> dict[str, Any]:
        """返信前の起点・対象・直前10件と追加取得要否を本文なしで判定します。"""
        return build_reply_context_gate(
            thread_root_present=thread_root_present,
            reply_target_present=reply_target_present,
            prior_message_count=prior_message_count,
            history_exhausted=history_exhausted,
            unresolved_reference_count=unresolved_reference_count,
            fetched_message_count=fetched_message_count,
            max_context_messages=max_context_messages,
        )

    @server.tool()
    def stage_discord_send_before_human_action(
        draft: str,
        mode: str = "reply",
        target_url: str = "",
        mention_label: str = "",
        understanding_confirmed: bool = False,
        thread_root_present: bool = False,
        reply_target_present: bool = False,
        prior_message_count: int = 0,
        history_exhausted: bool = False,
        unresolved_reference_count: int = 0,
    ) -> dict[str, Any]:
        """Discord 返信/メンションの下書き入力準備 packet を返します。実送信はしません。"""
        gate = build_reply_context_gate(
            thread_root_present=thread_root_present,
            reply_target_present=reply_target_present,
            prior_message_count=prior_message_count,
            history_exhausted=history_exhausted,
            unresolved_reference_count=unresolved_reference_count,
        )
        return build_discord_send_staging_packet(
            draft,
            load_events(store),
            mode=mode,
            target_url=target_url,
            mention_label=mention_label,
            understanding_confirmed=understanding_confirmed,
            reply_context_gate=gate,
        )

    @server.tool()
    def verify_chrome_extension_fill_only_before_action(
        staging_packet: dict[str, Any],
        socket_preflight: bool = False,
        target_url_verified: bool = False,
        socket_after_navigation: bool = False,
        latest_target_snapshot_confirmed: bool = False,
        reply_ui_candidates: int = 0,
        message_box_candidates: int = 0,
        draft_matches_copy_block: bool = False,
        socket_pre_send: bool = False,
    ) -> dict[str, Any]:
        """Chrome 拡張が下書き入力してよいかを dry-run 観測だけで判定します。"""
        return verify_chrome_extension_fill_only_dry_run(
            staging_packet,
            socket_preflight=socket_preflight,
            target_url_verified=target_url_verified,
            socket_after_navigation=socket_after_navigation,
            latest_target_snapshot_confirmed=latest_target_snapshot_confirmed,
            reply_ui_candidates=reply_ui_candidates,
            message_box_candidates=message_box_candidates,
            draft_matches_copy_block=draft_matches_copy_block,
            socket_pre_send=socket_pre_send,
        )

    @server.tool()
    def closeout_discord_send_after_human_action(
        staging_packet: dict[str, Any] | None = None,
        dry_run_report: dict[str, Any] | None = None,
        human_sent_observed: bool = False,
        human_reviewed: bool = False,
        observed_text_status: str = "not_checked",
        unread_check_status: str = "not_checked",
        unread_signal_count: int = 0,
        observed_message_id: str = "",
        observed_url: str = "",
        note_label: str = "",
    ) -> dict[str, Any]:
        """人間送信後の状態を metadata-only で閉じます。本文、URL、snowflake は返しません。"""
        return build_discord_post_send_closeout_packet(
            staging_packet=staging_packet,
            dry_run_report=dry_run_report,
            human_sent_observed=human_sent_observed,
            human_reviewed=human_reviewed,
            observed_text_status=observed_text_status,
            unread_check_status=unread_check_status,
            unread_signal_count=unread_signal_count,
            observed_message_id=observed_message_id,
            observed_url=observed_url,
            note_label=note_label,
        )

    @server.tool()
    def guide_reply_from_visible_text(
        text: str,
        draft: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
        understanding_confirmed: bool = False,
        thread_root_present: bool = False,
        reply_target_present: bool = False,
        prior_message_count: int = 0,
        history_exhausted: bool = False,
        unresolved_reference_count: int = 0,
    ) -> dict[str, Any]:
        """Discord の可視テキストと返信 draft から会話ガイドを返します。"""
        gate = build_reply_context_gate(
            thread_root_present=thread_root_present,
            reply_target_present=reply_target_present,
            prior_message_count=prior_message_count,
            history_exhausted=history_exhausted,
            unresolved_reference_count=unresolved_reference_count,
        )
        return guide_reply_from_text(
            text,
            draft,
            guild_label=guild_label,
            channel_label=channel_label,
            understanding_confirmed=understanding_confirmed,
            reply_context_gate=gate,
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

    @server.tool()
    def chew_discord_context_from_visible_text(
        text: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
        server_context: str = "",
        channel_context: str = "",
        thread_context: str = "",
        focus: str = "",
    ) -> dict[str, Any]:
        """Discord の可視テキストを咀嚼し、raw 本文なしの理解メモを返します。"""
        return digest_context_from_text(
            text,
            guild_label=guild_label,
            channel_label=channel_label,
            server_context=server_context,
            channel_context=channel_context,
            thread_context=thread_context,
            focus=focus,
        )

    @server.tool()
    def get_context_operating_mode_from_visible_text(
        text: str,
        mode: str,
        guild_label: str = "example-community",
        channel_label: str = "general",
        server_context: str = "",
        channel_context: str = "",
        thread_context: str = "",
        focus: str = "",
    ) -> dict[str, Any]:
        """triage/catchup/join-thread/boundary の運用モード packet を返します。"""
        return context_operating_mode_from_text(
            text,
            mode=mode,
            guild_label=guild_label,
            channel_label=channel_label,
            server_context=server_context,
            channel_context=channel_context,
            thread_context=thread_context,
            focus=focus,
        )

    @server.tool()
    def plan_discord_url_read(url: str) -> dict[str, Any]:
        """Discord URL をブラウザ可視テキストで読むための local-first 計画を返します。"""
        return plan_discord_url_read_core(url)

    @server.tool()
    def snapshot_discord_url_text(
        url: str,
        text: str = "",
        title: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Discord URL で開いた画面の可視テキストをローカル snapshot として保存します。"""
        return snapshot_visible_text(
            text=text,
            messages=messages or [],
            url=url,
            title=title,
            source="discord_url_visible_text",
            path=snapshot_store or Path(".local/discord-context-bridge/text-snapshots.ndjson"),
        )

    @server.tool()
    def snapshot_chrome_extension_visible_text(
        text: str = "",
        title: str = "",
        url: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Chrome 拡張や DOM 抽出で見えている Discord テキストをローカル snapshot として保存します。"""
        message_list = messages or []
        snapshot = snapshot_visible_text(
            text=text,
            messages=message_list,
            url=url,
            title=title,
            source="chrome_extension_dom",
            path=snapshot_store or Path(".local/discord-context-bridge/text-snapshots.ndjson"),
        )
        return {
            **snapshot,
            "reply_context_gate": build_reply_context_gate_from_messages(message_list),
        }

    @server.tool()
    def get_context_passport_from_discord_url(
        url: str,
        text: str = "",
        title: str = "",
        messages: list[dict[str, Any]] | None = None,
        guild_label: str = "example-community",
        channel_label: str = "general",
        server_context: str = "",
        channel_context: str = "",
        thread_context: str = "",
    ) -> dict[str, Any]:
        """Discord URL の可視テキストから、保存と文脈カード作成をまとめて行います。"""
        message_list = messages or []
        content = text or "\n".join(
            str(message.get("text") or message.get("content") or message.get("text_snippet") or "")
            for message in message_list
        )
        snapshot = snapshot_visible_text(
            text=content,
            messages=message_list,
            url=url,
            title=title,
            source="discord_url_visible_text",
            path=snapshot_store or Path(".local/discord-context-bridge/text-snapshots.ndjson"),
        )
        passport = context_passport_from_text(
            content,
            guild_label=guild_label,
            channel_label=channel_label,
            server_context=server_context,
            channel_context=channel_context,
            thread_context=thread_context,
        )
        return {
            **passport,
            "snapshot": snapshot,
            "reply_context_gate": build_reply_context_gate_from_messages(message_list),
            "url_read_plan": plan_discord_url_read_core(url),
        }

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
