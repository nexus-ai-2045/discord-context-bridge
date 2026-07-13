from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .core import (
    DEFAULT_CONTEXT_STORE,
    DEFAULT_REVIEW_STORE,
    DEFAULT_ATTACHMENT_LEDGER,
    DEFAULT_SHARED_RAW_SNAPSHOT_ROOT,
    DEFAULT_STORE,
    DEFAULT_TEXT_SNAPSHOT_STORE,
    audit_context_store,
    audit_event_store,
    build_attachment_ledger,
    load_jsonl_records,
    build_coverage_report,
    build_discord_post_send_closeout_packet,
    build_discord_send_staging_packet,
    build_discord_send_operation_status,
    build_cache_first_intake,
    build_handoff_packet,
    build_latest_snapshot_report,
    plan_full_thread_capture,
    build_review_artifact_markdown,
    build_reply_context_gate,
    build_bridge_intake,
    build_url_intake_fast_path,
    context_passport_from_text,
    context_operating_mode_from_text,
    digest_context_from_text,
    fast_briefing,
    get_review_state,
    get_context_document,
    guide_reply_from_text,
    import_visible_text,
    list_context_documents,
    load_events,
    matching_snapshot_records,
    ops_view_summary,
    review_reply_intent,
    resolve_context_bindings,
    snapshot_visible_text,
    status_dashboard,
    upsert_review_state,
    upsert_context_document,
    build_url_intake_gate,
    verify_chrome_extension_fill_only_dry_run,
    write_attachment_ocr_log,
)


SENSITIVE_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+", re.IGNORECASE),
    re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,})\b"),
    re.compile(r"(?:Library/Application Support/Discord|\\AppData\\Roaming\\discord)", re.IGNORECASE),
    re.compile(r"(?<!\d)\d{17,20}(?!\d)"),
    re.compile(r"(?:/(?:Users|home|private|tmp|var/folders|Volumes)/[^ \n]+|[A-Za-z]:\\[^ \n]+)"),
    re.compile(r"\b" + "Author" + r"ization\s*:\s*(?:" + "Bear" + r"er\s+)?[^ \n]+", re.IGNORECASE),
    re.compile(r"\b" + "Bear" + r"er\s+[A-Za-z0-9._~+/=-]{10,}\b", re.IGNORECASE),
)


class JapaneseArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)
        self._positionals.title = "コマンド"
        self._optionals.title = "オプション"
        self.add_argument("-h", "--help", action="help", help="このヘルプを表示して終了する")

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "使い方:")

    def format_help(self) -> str:
        help_text = super().format_help()
        replacements = {
            "usage:": "使い方:",
            "positional arguments:": "コマンド:",
            "optional arguments:": "オプション:",
            "options:": "オプション:",
            "show this help message and exit": "このヘルプを表示して終了する",
        }
        for source, target in replacements.items():
            help_text = help_text.replace(source, target)
        return help_text


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def split_local_command(command: str) -> list[str]:
    if os.name == "nt":
        ctypes.windll.shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
        argc = ctypes.c_int()
        argv = ctypes.windll.shell32.CommandLineToArgvW(command, ctypes.byref(argc))
        if not argv:
            raise SystemExit("local command の解析に失敗しました。")
        try:
            return [argv[index] for index in range(argc.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
    return shlex.split(command, posix=os.name != "nt")


def _add_reply_context_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--thread-root-present", action="store_true", help="スレッド起点を取得済み")
    parser.add_argument("--reply-target-present", action="store_true", help="返信対象を取得済み")
    parser.add_argument("--prior-message-count", type=int, default=0, help="返信対象より前に取得した会話件数")
    parser.add_argument("--history-exhausted", action="store_true", help="これ以前の履歴がないことを確認済み")
    parser.add_argument("--unresolved-reference-count", type=int, default=0, help="追加取得が必要な未解決参照数")
    parser.add_argument("--max-context-messages", type=int, default=100, help="自動追加取得の安全上限")


def _reply_context_gate_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return build_reply_context_gate(
        thread_root_present=args.thread_root_present,
        reply_target_present=args.reply_target_present,
        prior_message_count=args.prior_message_count,
        history_exhausted=args.history_exhausted,
        unresolved_reference_count=args.unresolved_reference_count,
        fetched_message_count=args.prior_message_count + int(args.reply_target_present),
        max_context_messages=args.max_context_messages,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = JapaneseArgumentParser(description="Discord の可視会話テキストを扱うローカル文脈ブリッジ")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE, help="取り込んだ可視テキストを保存するローカルファイル")
    parser.add_argument("--context-store", type=Path, default=DEFAULT_CONTEXT_STORE, help="サーバールールやスレッド目的を保存するローカル文脈庫")
    parser.add_argument("--review-store", type=Path, default=DEFAULT_REVIEW_STORE, help="返信前レビューの安全な状態メタデータを保存する registry")
    sub = parser.add_subparsers(dest="command", required=True, title="コマンド", parser_class=JapaneseArgumentParser)

    visible = sub.add_parser("import-visible-text", help="可視テキストをローカル保存へ取り込む")
    visible.add_argument("--input", type=Path, help="読み込むテキストファイル")
    visible.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    visible.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    visible.add_argument("--dry-run", action="store_true", help="保存せずに解析結果だけ確認する")
    visible.set_defaults(handler=_cmd_import_visible_text)

    clipboard = sub.add_parser(
        "import-clipboard",
        help="ローカルのクリップボードから Discord の可視テキストを取り込む",
    )
    clipboard.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    clipboard.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    clipboard.add_argument("--dry-run", action="store_true", help="保存せずに解析結果だけ確認する")
    clipboard.add_argument(
        "--clipboard-command",
        default="pbpaste",
        help="クリップボード取得に使うローカルコマンド。既定は macOS の pbpaste",
    )
    clipboard.set_defaults(handler=_cmd_import_clipboard)

    watch = sub.add_parser(
        "watch-clipboard",
        help="ローカルのクリップボードを監視し、変化した Discord 可視テキストを取り込む",
    )
    watch.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    watch.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    watch.add_argument(
        "--clipboard-command",
        default="pbpaste",
        help="クリップボード取得に使うローカルコマンド。既定は macOS の pbpaste",
    )
    watch.add_argument("--interval", type=float, default=1.0, help="クリップボードを確認する間隔秒")
    watch.add_argument("--max-polls", type=int, default=0, help="確認回数。0 の場合は停止されるまで継続する")
    watch.set_defaults(handler=_cmd_watch_clipboard)

    briefing = sub.add_parser("fast-briefing", help="直近文脈の短い要約を表示する")
    briefing.set_defaults(handler=_cmd_fast_briefing)

    audit = sub.add_parser("audit-store", help="トンネル公開前に保存データの安全性を確認する")
    audit.set_defaults(handler=_cmd_audit_store)

    ops_view = sub.add_parser("ops-view", help="本文なしで運用ログの状態を表示する")
    ops_view.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    ops_view.set_defaults(handler=_cmd_ops_view)

    dashboard = sub.add_parser("status-dashboard", help="本文なしで現在の運用状態をまとめる")
    dashboard.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    dashboard.add_argument("--github-state", default="not_checked", help="GitHub 状態の短い安全ラベル")
    dashboard.set_defaults(handler=_cmd_status_dashboard)

    report_latest = sub.add_parser("report-latest", help="保存済み snapshot から metadata-only の最新 report を作る")
    report_latest.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="保存済み可視テキスト snapshot のローカルファイル")
    report_latest.add_argument("--target-key", default="", help="特定 target_key の最新 snapshot だけを見る")
    report_latest.add_argument("--url", default="", help="特定 URL に一致する保存済み snapshot だけを見る。出力には URL を含めません")
    report_latest.add_argument("--include-preview", action="store_true", help="本文全体ではなく短い preview だけを明示的に含める")
    report_latest.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    report_latest.set_defaults(handler=_cmd_report_latest)

    fast_path = sub.add_parser(
        "url-intake-fast-path",
        help="Discord URL intake の最短 read-only 判定を metadata-only で返す",
    )
    fast_path.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    fast_path.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="保存済み可視テキスト snapshot のローカルファイル")
    fast_path.add_argument("--raw-cache", type=Path, help="突合する raw cache / local snapshot ndjson")
    fast_path.add_argument("--target-key", default="", help="任意: URL 由来ではない target_key を指定する")
    fast_path.add_argument("--hook-snapshot-status", default="", help="hook が既に出した snapshot status")
    fast_path.add_argument("--input", type=Path, help="判定前に取り込む可視テキストファイル")
    fast_path.add_argument("--from-clipboard", action="store_true", help="判定前に clipboard の可視テキストを取り込む")
    fast_path.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    fast_path.add_argument("--source-command", help="判定前に実行する Discord 可視テキスト取得コマンド")
    fast_path.add_argument(
        "--refresh-source",
        default="discord_url_visible_text_refresh",
        help="判定前 refresh snapshot の取得元 label",
    )
    fast_path.add_argument("--title", default="", help="任意の安全な短い label。実 channel 名や private 名は避ける")
    fast_path.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    fast_path.set_defaults(handler=_cmd_url_intake_fast_path)

    bridge_intake = sub.add_parser(
        "bridge-intake",
        help="message found -> snapshot / coverage / passport / guide までを一本で進める",
    )
    bridge_intake.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    bridge_intake.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="保存済み可視テキスト snapshot のローカルファイル")
    bridge_intake.add_argument("--raw-cache", type=Path, help="突合する raw cache / local snapshot ndjson")
    bridge_intake.add_argument("--target-key", default="", help="任意: URL 由来ではない target_key を指定する")
    bridge_intake.add_argument("--input", type=Path, help="取り込む可視テキストファイル")
    bridge_intake.add_argument("--from-clipboard", action="store_true", help="clipboard の可視テキストを取り込む")
    bridge_intake.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    bridge_intake.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
    bridge_intake.add_argument("--title", default="", help="任意の安全な短い label。実 channel 名や private 名は避ける")
    bridge_intake.add_argument("--source", default="bridge_intake", help="snapshot の取得元 label")
    bridge_intake.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    bridge_intake.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    bridge_intake.add_argument("--draft", default="", help="任意: 返信下書き。指定時だけ guide-reply まで進める")
    bridge_intake.add_argument(
        "--understanding-confirmed",
        action="store_true",
        help="文脈理解サマリを人間が確認済みの場合だけ guide を本審査する",
    )
    _add_reply_context_args(bridge_intake)
    bridge_intake.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    bridge_intake.set_defaults(handler=_cmd_bridge_intake)

    snapshot_url = sub.add_parser(
        "snapshot-discord-url-text",
        help="Discord URL の可視テキストを local snapshot として保存する",
    )
    snapshot_url.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    snapshot_url.add_argument("--input", type=Path, help="読み込む可視テキストファイル。省略時は stdin")
    snapshot_url.add_argument("--from-clipboard", action="store_true", help="--input ではなくローカルのクリップボードから可視テキストを読む")
    snapshot_url.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    snapshot_url.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
    snapshot_url.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="保存先 snapshot store")
    snapshot_url.add_argument("--title", default="", help="任意の安全な短い label。実 channel 名や private 名は避ける")
    snapshot_url.add_argument(
        "--source",
        default="discord_url_visible_text",
        help="snapshot の取得元 label。例: discord_url_visible_text / computer_use_accessibility_tree",
    )
    snapshot_url.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    snapshot_url.set_defaults(handler=_cmd_snapshot_discord_url_text)

    attachment_ledger = sub.add_parser("attachment-ledger", help="raw cache から添付の safe metadata ledger を作る")
    attachment_ledger.add_argument("--input", type=Path, required=True, help="Discord raw cache / snapshot ndjson")
    attachment_ledger.add_argument("--output", type=Path, default=DEFAULT_ATTACHMENT_LEDGER, help="保存する metadata-only ledger")
    attachment_ledger.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    attachment_ledger.set_defaults(handler=_cmd_attachment_ledger)

    attachment_ocr = sub.add_parser("attachment-ocr-log", help="添付画像 OCR 結果を private local log として保存する")
    attachment_ocr.add_argument("--source-key", required=True, help="attachment-ledger が出した source_key")
    attachment_ocr.add_argument("--output", type=Path, required=True, help="保存する OCR log Markdown")
    attachment_ocr.add_argument("--ocr-text-file", type=Path, help="OCR 済み text file。stdout には本文を返しません")
    attachment_ocr.add_argument("--ocr-command", help="OCR text を stdout に出す local command。stdout には本文を返しません")
    attachment_ocr.add_argument("--note-label", default="", help="任意の安全な短い label")
    attachment_ocr.add_argument("--source-timeout", type=float, default=20.0, help="local command の最大秒数")
    attachment_ocr.set_defaults(handler=_cmd_attachment_ocr_log)

    intake = sub.add_parser("verify-url-intake", help="Discord URL の raw-cache-first intake gate を確認する")
    intake.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    intake.add_argument("--target-key", default="", help="任意: URL 由来ではない target_key を指定する")
    intake.add_argument("--raw-cache", type=Path, help="Discord raw cache / local snapshot ndjson")
    intake.add_argument("--ai-log", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="AI-facing snapshot log")
    intake.add_argument("--sync", action="store_true", help="raw cache exact match を AI log へ同期する")
    intake.set_defaults(handler=_cmd_verify_url_intake)

    coverage = sub.add_parser("coverage-report", help="Discord URL / target_key の coverage と freshness を本文なしで確認する")
    coverage.add_argument("--url", default="", help="対象 Discord URL。出力には表示しません")
    coverage.add_argument("--target-key", default="", help="任意: URL 由来ではない target_key を指定する")
    coverage.add_argument("--raw-cache", type=Path, help="Discord raw cache / local snapshot ndjson")
    coverage.add_argument("--ai-log", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="AI-facing snapshot log")
    coverage.add_argument(
        "--source-kind",
        choices=["saved_log", "visible_dom", "scroll_dom", "api_or_export"],
        default="saved_log",
        help="coverage の保証レベルを表す source kind",
    )
    coverage.add_argument(
        "--dedupe-policy",
        choices=["none", "by_hash", "by_author_time_text", "semantic_near"],
        default="by_hash",
        help="重複判定方針",
    )
    coverage.set_defaults(handler=_cmd_coverage_report)

    full_thread = sub.add_parser(
        "thread-capture-plan",
        help="Discord スレッド全文取得に必要な route 配線状態を本文なしで確認する",
    )
    full_thread.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    full_thread.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="保存済み可視テキスト snapshot のローカルファイル")
    full_thread.add_argument("--raw-cache", type=Path, help="Discord raw export / cache ndjson")
    full_thread.add_argument("--gateway-configured", action="store_true", help="gateway live event route が設定済み")
    full_thread.add_argument("--rest-configured", action="store_true", help="REST/backfill route が設定済み")
    full_thread.add_argument("--bot-inbox-ready", action="store_true", help="bot text event inbox が利用可能")
    full_thread.add_argument("--private-adapter-configured", action="store_true", help="private adapter が利用可能")
    full_thread.add_argument("--visible-dom-available", action="store_true", help="Chrome visible DOM snapshot が利用可能")
    full_thread.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    full_thread.set_defaults(handler=_cmd_thread_capture_plan)

    reply_context = sub.add_parser("reply-context-plan", help="返信前の起点・対象・直前10件を本文なしで判定する")
    _add_reply_context_args(reply_context)
    reply_context.add_argument("--json", action="store_true", help="互換用。常にJSONを出力します")
    reply_context.set_defaults(handler=_cmd_reply_context_plan)

    cache_first = sub.add_parser(
        "cache-first-intake",
        help="ローカル cache / snapshot を先に見て private MD book を作る",
    )
    cache_first.add_argument("--url", required=True, help="対象 Discord URL。出力には表示しません")
    cache_first.add_argument("--snapshot-store", type=Path, default=DEFAULT_TEXT_SNAPSHOT_STORE, help="保存済み可視テキスト snapshot のローカルファイル")
    cache_first.add_argument("--cache-root", type=Path, default=DEFAULT_SHARED_RAW_SNAPSHOT_ROOT, help="shared raw snapshot root。出力には表示しません")
    cache_first.add_argument("--book-output", type=Path, default=None, help="生成する private Markdown book。出力には表示しません")
    cache_first.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    cache_first.set_defaults(handler=_cmd_cache_first_intake)

    handoff = sub.add_parser("handoff-packet", help="本文なしで次担当へ渡す handoff packet を作る")
    handoff.add_argument("--thread-key", default="manual-thread", help="review registry から参照する安全な thread key")
    handoff.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    handoff.add_argument("--github-state", default="not_checked", help="GitHub 状態の短い安全ラベル")
    handoff.set_defaults(handler=_cmd_handoff_packet)

    context_upsert = sub.add_parser("context-upsert", help="サーバー/チャンネル/スレッドの文脈をローカル文脈庫へ保存する")
    context_upsert.add_argument("--kind", required=True, choices=["server", "channel", "thread"], help="保存する文脈の種類")
    context_upsert.add_argument("--key", required=True, help="文脈を再利用するためのローカルキー")
    context_upsert.add_argument("--input", type=Path, required=True, help="保存する文脈テキスト")
    context_upsert.add_argument("--source", default="manual", help="文脈の取得元メモ")
    context_upsert.add_argument(
        "--label",
        action="append",
        default=[],
        help="自動紐付けに使う安全な別名。複数指定できます",
    )
    context_upsert.set_defaults(handler=_cmd_context_upsert)

    context_list = sub.add_parser("context-list", help="ローカル文脈庫の一覧を表示する")
    context_list.set_defaults(handler=_cmd_context_list)

    audit_context = sub.add_parser("audit-context-store", help="トンネル公開前に文脈庫の安全性を確認する")
    audit_context.set_defaults(handler=_cmd_audit_context_store)

    review = sub.add_parser("review-intent", help="返信意図を直近文脈と照合する")
    review.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    review.add_argument("--artifact-path", type=Path, help="送信前レビュー用の Markdown artifact を保存する")
    review.add_argument("--thread-key", default="manual-thread", help="review registry に保存する時の安全な thread key")
    review.add_argument("--save-review-state", action="store_true", help="safe metadata だけを review registry に保存する")
    review.add_argument("--understanding-confirmed", action="store_true", help="文脈理解サマリを人間が確認済みの場合だけ下書き review を進める")
    _add_reply_context_args(review)
    review.set_defaults(handler=_cmd_review_intent)

    draft_review = sub.add_parser("review-draft", help="返信下書きを保存済みの直近文脈と照合する")
    draft_review.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    draft_review.add_argument("--artifact-path", type=Path, help="送信前レビュー用の Markdown artifact を保存する")
    draft_review.add_argument("--thread-key", default="manual-thread", help="review registry に保存する時の安全な thread key")
    draft_review.add_argument("--save-review-state", action="store_true", help="safe metadata だけを review registry に保存する")
    draft_review.add_argument("--understanding-confirmed", action="store_true", help="文脈理解サマリを人間が確認済みの場合だけ下書き review を進める")
    _add_reply_context_args(draft_review)
    draft_review.set_defaults(handler=_cmd_review_intent)

    stage_send = sub.add_parser("stage-discord-send", help="Discord 返信/メンションの下書き入力準備だけを作る")
    stage_send.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    stage_send.add_argument("--mode", choices=["reply", "mention"], default="reply", help="返信として入れるか、メンション付き通常投稿として入れるか")
    stage_send.add_argument("--target-url", required=True, help="対象 Discord URL。出力には表示しません")
    stage_send.add_argument("--mention-label", default="", help="メンション運用時に人間が確認する表示名。例: @safe-label")
    stage_send.add_argument("--understanding-confirmed", action="store_true", help="文脈理解サマリを人間が確認済みの場合だけ準備を進める")
    _add_reply_context_args(stage_send)
    stage_send.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    stage_send.set_defaults(handler=_cmd_stage_discord_send)

    chrome_fill = sub.add_parser("verify-chrome-fill-dry-run", help="Chrome 拡張 fill-only 実行前の観測結果を検査する")
    chrome_fill.add_argument("--staging-packet", type=Path, required=True, help="stage-discord-send が出した JSON packet")
    chrome_fill.add_argument("--socket-preflight", action="store_true", help="Chrome 拡張 socket preflight が通った")
    chrome_fill.add_argument("--target-url-verified", action="store_true", help="対象 URL が一致した")
    chrome_fill.add_argument("--socket-after-navigation", action="store_true", help="遷移後 socket / DOM 確認が通った")
    chrome_fill.add_argument("--latest-target-snapshot-confirmed", action="store_true", help="ユーザー操作直前に最新対象 snapshot を取得・確認した")
    chrome_fill.add_argument("--reply-ui-candidates", type=int, default=0, help="検出した reply UI 候補数")
    chrome_fill.add_argument("--message-box-candidates", type=int, default=0, help="検出した通常 message box 候補数")
    chrome_fill.add_argument("--draft-matches-copy-block", action="store_true", help="入力予定 draft が copy block と一致した")
    chrome_fill.add_argument("--socket-pre-send", action="store_true", help="送信前停止地点で socket ping が通った")
    chrome_fill.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    chrome_fill.set_defaults(handler=_cmd_verify_chrome_fill_dry_run)

    closeout_send = sub.add_parser("closeout-discord-send", help="人間送信後の metadata-only closeout を確認する")
    closeout_send.add_argument("--staging-packet", type=Path, help="任意: stage-discord-send が出した JSON packet")
    closeout_send.add_argument("--dry-run-report", type=Path, help="任意: verify-chrome-fill-dry-run が出した JSON report")
    closeout_send.add_argument("--human-sent-observed", action="store_true", help="Discord 上で人間送信済み message を確認した")
    closeout_send.add_argument("--human-reviewed", action="store_true", help="送信後の見え方を人間が確認済み")
    closeout_send.add_argument(
        "--observed-text-status",
        choices=["matches-copy-block", "human-edited-and-reviewed", "not-checked"],
        default="not-checked",
        help="送信後本文の安全な状態ラベル。本文は出力しません",
    )
    closeout_send.add_argument(
        "--unread-check-status",
        choices=["none-unread", "has-unread", "not-checked"],
        default="not-checked",
        help="送信後に未読が残っていないかの安全な状態ラベル。本文は出力しません",
    )
    closeout_send.add_argument("--unread-signal-count", type=int, default=0, help="任意: 未読シグナル件数。本文は出力しません")
    closeout_send.add_argument("--observed-message-id", default="", help="任意: 確認した message id。出力には表示しません")
    closeout_send.add_argument("--observed-url", default="", help="任意: 確認した Discord URL。出力には表示しません")
    closeout_send.add_argument("--note-label", default="", help="任意: closeout 用の短い安全ラベル")
    closeout_send.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    closeout_send.set_defaults(handler=_cmd_closeout_discord_send)

    send_status = sub.add_parser("send-operation-status", help="既存ログからDiscord送信テスト運転表を作る")
    send_status.add_argument("--staging-packet", type=Path, help="任意: stage-discord-send が出した JSON packet")
    send_status.add_argument("--dry-run-report", type=Path, help="任意: verify-chrome-fill-dry-run が出した JSON report")
    send_status.add_argument("--closeout-report", type=Path, help="任意: closeout-discord-send が出した JSON report")
    send_status.add_argument("--target-label", default="", help="対象チャンネル/投稿先の安全な表示名。実IDやURLは避ける")
    send_status.add_argument(
        "--target-environment",
        choices=["test", "production"],
        default="test",
        help="対象がテスト用か本番用か",
    )
    send_status.add_argument("--rollback-plan-reviewed", action="store_true", help="失敗時の修正投稿/停止/人間確認手順を確認済み")
    send_status.add_argument("--production-runbook-fixed", action="store_true", help="本番送信手順を固定・レビュー済み")
    send_status.add_argument(
        "--post-send-retrospective",
        action="store_true",
        help="送信済みログを事後closeoutとして扱う。過去の事前gate通過は主張しない",
    )
    send_status.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    send_status.set_defaults(handler=_cmd_send_operation_status)

    guide = sub.add_parser("guide-reply", help="Discord 可視テキストと返信下書きから会話ガイドを作る")
    guide.add_argument("--input", type=Path, help="読み込むテキストファイル")
    guide.add_argument("--from-clipboard", action="store_true", help="--input ではなくローカルのクリップボードから可視テキストを読む")
    guide.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    guide.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
    guide.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    guide.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    guide.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    guide.add_argument("--understanding-confirmed", action="store_true", help="文脈理解サマリを人間が確認済みの場合だけ下書き review を進める")
    _add_reply_context_args(guide)
    guide.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    guide.set_defaults(handler=_cmd_guide_reply)

    passport = sub.add_parser("context-passport", help="Discord 可視テキストからスレッド文脈カードを作る")
    passport.add_argument("--input", type=Path, help="読み込むテキストファイル")
    passport.add_argument("--from-clipboard", action="store_true", help="--input ではなくローカルのクリップボードから可視テキストを読む")
    passport.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    passport.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
    passport.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    passport.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    passport.add_argument("--server-context", type=Path, help="サーバールールや全体方針を書いたローカルテキスト")
    passport.add_argument("--channel-context", type=Path, help="チャンネル目的や掲示板ルールを書いたローカルテキスト")
    passport.add_argument("--thread-context", type=Path, help="スレッド固定文・目的・注意事項を書いたローカルテキスト")
    passport.add_argument("--server-context-key", help="文脈庫から読むサーバー文脈キー")
    passport.add_argument("--channel-context-key", help="文脈庫から読むチャンネル文脈キー")
    passport.add_argument("--thread-context-key", help="文脈庫から読むスレッド文脈キー")
    passport.add_argument(
        "--auto-context-bindings",
        action="store_true",
        help="安全な guild/channel label から文脈庫キーを補助的に解決する",
    )
    passport.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    passport.set_defaults(handler=_cmd_context_passport)

    chew = sub.add_parser("chew-context", help="Discord 可視テキストを咀嚼し、返信前の理解メモを作る")
    chew.add_argument("--input", type=Path, help="読み込むテキストファイル")
    chew.add_argument("--from-clipboard", action="store_true", help="--input ではなくローカルのクリップボードから可視テキストを読む")
    chew.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    chew.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
    chew.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    chew.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    chew.add_argument("--server-context", type=Path, help="サーバールールや全体方針を書いたローカルテキスト")
    chew.add_argument("--channel-context", type=Path, help="チャンネル目的や掲示板ルールを書いたローカルテキスト")
    chew.add_argument("--thread-context", type=Path, help="スレッド固定文・目的・注意事項を書いたローカルテキスト")
    chew.add_argument("--server-context-key", help="文脈庫から読むサーバー文脈キー")
    chew.add_argument("--channel-context-key", help="文脈庫から読むチャンネル文脈キー")
    chew.add_argument("--thread-context-key", help="文脈庫から読むスレッド文脈キー")
    chew.add_argument(
        "--auto-context-bindings",
        action="store_true",
        help="安全な guild/channel label から文脈庫キーを補助的に解決する",
    )
    chew.add_argument("--focus", default="", help="任意: 咀嚼したい焦点。出力前に安全化します")
    chew.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    chew.set_defaults(handler=_cmd_chew_context)

    for command, mode, help_text in [
        ("triage-mode", "triage", "読む・咀嚼・返信・保存・停止の入口を切る"),
        ("catchup-mode", "catchup", "初参加/再訪時にどこまで読めばよいかを切る"),
        ("join-thread-mode", "join-thread", "スレッド初参加としてどう入るかを切る"),
        ("boundary-mode", "boundary", "raw/private/外部送信/公開境界を切る"),
    ]:
        mode_parser = sub.add_parser(command, help=help_text)
        mode_parser.add_argument("--input", type=Path, help="読み込むテキストファイル")
        mode_parser.add_argument("--from-clipboard", action="store_true", help="--input ではなくローカルのクリップボードから可視テキストを読む")
        mode_parser.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
        mode_parser.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
        mode_parser.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
        mode_parser.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
        mode_parser.add_argument("--server-context", type=Path, help="サーバールールや全体方針を書いたローカルテキスト")
        mode_parser.add_argument("--channel-context", type=Path, help="チャンネル目的や掲示板ルールを書いたローカルテキスト")
        mode_parser.add_argument("--thread-context", type=Path, help="スレッド固定文・目的・注意事項を書いたローカルテキスト")
        mode_parser.add_argument("--server-context-key", help="文脈庫から読むサーバー文脈キー")
        mode_parser.add_argument("--channel-context-key", help="文脈庫から読むチャンネル文脈キー")
        mode_parser.add_argument("--thread-context-key", help="文脈庫から読むスレッド文脈キー")
        mode_parser.add_argument(
            "--auto-context-bindings",
            action="store_true",
            help="安全な guild/channel label から文脈庫キーを補助的に解決する",
        )
        mode_parser.add_argument("--focus", default="", help="任意: 見たい焦点。出力前に安全化します")
        mode_parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
        mode_parser.set_defaults(handler=_cmd_context_mode, context_mode=mode)

    watch_passport = sub.add_parser(
        "watch-passport",
        help="ローカルコマンドを監視し、変化した Discord 可視テキストから文脈カードを作る",
    )
    watch_passport.add_argument("--source-command", required=True, help="Discord 可視テキストを出力するローカルコマンド")
    watch_passport.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    watch_passport.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    watch_passport.add_argument("--server-context", type=Path, help="サーバールールや全体方針を書いたローカルテキスト")
    watch_passport.add_argument("--channel-context", type=Path, help="チャンネル目的や掲示板ルールを書いたローカルテキスト")
    watch_passport.add_argument("--thread-context", type=Path, help="スレッド固定文・目的・注意事項を書いたローカルテキスト")
    watch_passport.add_argument("--server-context-key", help="文脈庫から読むサーバー文脈キー")
    watch_passport.add_argument("--channel-context-key", help="文脈庫から読むチャンネル文脈キー")
    watch_passport.add_argument("--thread-context-key", help="文脈庫から読むスレッド文脈キー")
    watch_passport.add_argument(
        "--auto-context-bindings",
        action="store_true",
        help="安全な guild/channel label から文脈庫キーを補助的に解決する",
    )
    watch_passport.add_argument("--interval", type=float, default=1.0, help="入力元コマンドを確認する間隔秒")
    watch_passport.add_argument("--max-polls", type=int, default=0, help="確認回数。0 の場合は停止されるまで継続する")
    watch_passport.add_argument("--source-timeout", type=float, default=20.0, help="入力元コマンドの最大秒数")
    watch_passport.add_argument("--json", action="store_true", help="機械処理用に JSON lines で出力する")
    watch_passport.set_defaults(handler=_cmd_watch_passport)

    watch_guide = sub.add_parser("watch-guide", help="ローカルコマンドを監視し、変化した Discord 可視テキストから会話ガイドを作る")
    watch_guide.add_argument("--source-command", required=True, help="Discord 可視テキストを出力するローカルコマンド")
    watch_guide.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    watch_guide.add_argument("--understanding-confirmed", action="store_true", help="文脈理解サマリを人間が確認済みの場合だけ下書き review を進める")
    watch_guide.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    watch_guide.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    watch_guide.add_argument("--interval", type=float, default=1.0, help="入力元コマンドを確認する間隔秒")
    watch_guide.add_argument("--max-polls", type=int, default=0, help="確認回数。0 の場合は停止されるまで継続する")
    watch_guide.add_argument("--source-timeout", type=float, default=20.0, help="入力元コマンドの最大秒数")
    watch_guide.add_argument("--json", action="store_true", help="機械処理用に JSON lines で出力する")
    watch_guide.set_defaults(handler=_cmd_watch_guide)
    return parser


def _cmd_import_visible_text(args: argparse.Namespace) -> int:
    text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    return _import_text_from_args(text, args)


def _cmd_import_clipboard(args: argparse.Namespace) -> int:
    text = read_clipboard_text(args.clipboard_command)
    return _import_text_from_args(text, args)


def _cmd_watch_clipboard(args: argparse.Namespace) -> int:
    last_text = ""
    polls = 0
    changed = 0
    appended = 0
    duplicate = 0
    while args.max_polls <= 0 or polls < args.max_polls:
        polls += 1
        text = read_clipboard_text(args.clipboard_command)
        if text != last_text:
            last_text = text
            changed += 1
            result = import_visible_text(
                text,
                path=args.store,
                guild_label=args.guild,
                channel_label=args.channel,
            )
            appended += int(result["appended"])
            duplicate += int(result["duplicate"])
            print(
                _json(
                    {
                        **result,
                        "event": "clipboard_import",
                        "event_label": "clipboard 取り込み",
                        "message": "clipboard の変化を取り込みました。",
                        "poll": polls,
                    }
                )
            )
        if args.max_polls <= 0 or polls < args.max_polls:
            time.sleep(args.interval)
    print(
        _json(
            {
                "language": "ja",
                "event": "watch_complete",
                "event_label": "clipboard 監視完了",
                "message": "clipboard 監視を終了しました。",
                "polls": polls,
                "changed": changed,
                "appended": appended,
                "duplicate": duplicate,
                "store": str(args.store),
            }
        )
    )
    return 0


def _import_text_from_args(text: str, args: argparse.Namespace) -> int:
    print(
        _json(
            import_visible_text(
                text,
                path=args.store,
                guild_label=args.guild,
                channel_label=args.channel,
                dry_run=args.dry_run,
            )
        )
    )
    return 0


def read_clipboard_text(command: str) -> str:
    return read_command_text(command, empty_message="clipboard が空です。Discord の可視テキストをコピーしてから再実行してください。")


def safe_command_failure_reason(stderr: str) -> str:
    if any(pattern.search(stderr) for pattern in SENSITIVE_ERROR_PATTERNS):
        return "安全監査により詳細を省略しました。"
    lowered = stderr.casefold()
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "permission" in lowered or "not authorized" in lowered:
        return "permission"
    if "ocr" in lowered and ("empty" in lowered or "空" in stderr):
        return "ocr_empty"
    if "empty" in lowered:
        return "empty"
    if "not_found" in lowered or "not found" in lowered:
        return "not_found"
    return "adapter_failed"


def read_command_text(
    command: str,
    *,
    empty_message: str = "source command の出力が空です。",
    timeout: float | None = None,
) -> str:
    tokens = split_local_command(command)
    env = os.environ.copy()
    while tokens:
        key_value = tokens[0]
        if "=" not in key_value or key_value.startswith("-"):
            break
        key, value = key_value.split("=", 1)
        if not key.replace("_", "").isalnum():
            break
        tokens.pop(0)
        env[key] = value
    if not tokens:
        raise SystemExit("local command が空です。")
    try:
        completed = subprocess.run(
            tokens,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"local command が {timeout:g} 秒で完了しませんでした。") from exc
    if completed.returncode != 0:
        failure_text = "\n".join(part for part in [completed.stderr.strip(), completed.stdout.strip()] if part)
        raise SystemExit(
            "local command の実行に失敗しました: "
            f"exit_code={completed.returncode} reason={safe_command_failure_reason(failure_text)}"
        )
    if not completed.stdout.strip():
        raise SystemExit(empty_message)
    return completed.stdout


def _cmd_fast_briefing(args: argparse.Namespace) -> int:
    print(_json(fast_briefing(load_events(args.store))))
    return 0


def _cmd_audit_store(args: argparse.Namespace) -> int:
    report = audit_event_store(args.store)
    print(_json(report))
    return 0 if report["safe_for_tunnel"] else 2


def _cmd_ops_view(args: argparse.Namespace) -> int:
    summary = ops_view_summary(args.store)
    if args.json:
        print(_json(summary))
        return 0 if summary["gate_verdict"] == "pass" else 2
    print(summary["message"])
    print(f"safe label: {', '.join(summary['safe_labels']) if summary['safe_labels'] else 'なし'}")
    print(f"件数: {summary['event_count']}")
    print(f"last_seen: {summary['last_seen'] or 'なし'}")
    print(f"delta count: {summary['delta_count']}")
    print(f"gate verdict: {summary['gate_verdict']}")
    print(summary["gate_verdict_label"])
    print(summary["outbound_label"])
    return 0 if summary["gate_verdict"] == "pass" else 2


def _cmd_status_dashboard(args: argparse.Namespace) -> int:
    dashboard = status_dashboard(args.store, github_state=args.github_state)
    if args.json:
        print(_json(dashboard))
        return 0 if not dashboard["broken"] else 2
    print(dashboard["message"])
    print(f"now: event_count={dashboard['now']['event_count']} context_available={str(dashboard['now']['context_available']).lower()}")
    print(f"done: {', '.join(dashboard['done'])}")
    print(f"broken: {', '.join(dashboard['broken']) if dashboard['broken'] else 'なし'}")
    print(f"blocked: {', '.join(dashboard['blocked']) if dashboard['blocked'] else 'なし'}")
    print(f"next: {', '.join(dashboard['next']) if dashboard['next'] else 'なし'}")
    print(f"github: {dashboard['github']['state']}")
    print("outbound: disabled")
    return 0 if not dashboard["broken"] else 2


def _cmd_report_latest(args: argparse.Namespace) -> int:
    report = build_latest_snapshot_report(
        path=args.snapshot_store,
        target_key=args.target_key,
        url=args.url,
        include_preview=args.include_preview,
    )
    if args.json:
        print(_json(report))
        return 0 if report["ok"] else 2
    print(report["message"])
    print(f"ok: {str(report['ok']).lower()}")
    print(f"raw_text_returned: {str(report['raw_text_returned']).lower()}")
    print("path_output: omitted")
    if report["ok"]:
        source = report["source_summary"]
        report_context = source["report_acquisition_context"]
        print(f"target_key: {report['target']['target_key']}")
        print(f"source_kind: {source['source_kind']}")
        print(f"report_mode: {report_context['mode']}")
        print(f"live_browser_access: {str(report_context['live_browser_access']).lower()}")
        print(f"new_capture: {str(report_context['new_capture']).lower()}")
        print(f"uncaptured_after_latest: {report['uncaptured_range']['after_latest_saved_snapshot']}")
    else:
        print(f"reason: {report['reason']}")
    print("outbound: disabled")
    return 0 if report["ok"] else 2


def latest_match_metadata(path: Path, *, url: str, target_key: str) -> dict[str, Any]:
    matches = matching_snapshot_records(path, url=url, target_key=target_key)
    latest = matches[-1] if matches else {}
    return {
        "exists": path.exists(),
        "match_count": len(matches),
        "latest_captured_at": str(latest.get("captured_at") or ""),
        "latest_content_hash": str(latest.get("content_hash") or ""),
        "raw_text_returned": False,
        "path_output": "omitted",
    }


def _cmd_bridge_intake(args: argparse.Namespace) -> int:
    text = ""
    if args.input or args.from_clipboard or args.source_command:
        text = read_visible_text_arg(args)
    payload = build_bridge_intake(
        url=args.url,
        snapshot_store=args.snapshot_store,
        raw_cache_path=args.raw_cache,
        text=text,
        draft=args.draft,
        understanding_confirmed=args.understanding_confirmed,
        title=args.title,
        source=args.source,
        target_key=args.target_key,
        guild_label=args.guild,
        channel_label=args.channel,
        reply_context_gate=_reply_context_gate_from_args(args),
    )
    if args.json:
        print(_json(payload))
    else:
        print(payload["message"])
        print(f"decision: {payload['decision']}")
        print(f"next_step: {payload['next_step']}")
        print(f"recommended_command: {payload['recommended_command']}")
        print(f"pipeline_completed: {','.join(payload['pipeline']['completed'])}")
        print(f"blocked_reason: {payload.get('blocked_reason') or 'none'}")
        print(f"raw_text_returned: {str(payload['raw_text_returned']).lower()}")
        print("path_output: omitted")
        print("outbound: disabled")
    if payload["decision"] in {"passport_ready", "guide_ready"}:
        return 0
    if payload["decision"] == "understanding_blocked":
        return 2
    return 2


def _cmd_url_intake_fast_path(args: argparse.Namespace) -> int:
    target_key = args.target_key or ""
    initial_coverage = build_coverage_report(
        url=args.url,
        target_key=target_key,
        raw_cache_path=args.raw_cache,
        ai_log_path=args.snapshot_store,
    )
    source_available = bool(args.input or args.from_clipboard or args.source_command)
    refresh_needed = initial_coverage["recency"]["status"] in {"stale", "missing", "unknown"}
    refresh_requested = source_available and refresh_needed
    refresh_payload: dict[str, Any] | None = None
    if refresh_requested:
        refresh_payload = snapshot_visible_text(
            text=read_visible_text_arg(args),
            url=args.url,
            title=args.title,
            source=args.refresh_source,
            path=args.snapshot_store,
        )
    payload = build_url_intake_fast_path(
        url=args.url,
        snapshot_store=args.snapshot_store,
        target_key=args.target_key,
        hook_snapshot_status=args.hook_snapshot_status,
    )
    key = payload["target"]["target_key"]
    final_coverage = build_coverage_report(
        url=args.url,
        target_key=key,
        raw_cache_path=args.raw_cache,
        ai_log_path=args.snapshot_store,
        source_kind="saved_log",
    )
    snapshot_meta = latest_match_metadata(args.snapshot_store, url=args.url, target_key=key)
    raw_cache_meta = (
        latest_match_metadata(args.raw_cache, url=args.url, target_key=key)
        if args.raw_cache
        else {
            "exists": False,
            "match_count": 0,
            "latest_captured_at": "",
            "latest_content_hash": "",
            "raw_text_returned": False,
            "path_output": "omitted",
        }
    )
    cache_hash_match = bool(
        snapshot_meta["latest_content_hash"]
        and raw_cache_meta["latest_content_hash"]
        and snapshot_meta["latest_content_hash"] == raw_cache_meta["latest_content_hash"]
    )
    payload["refresh_attempted_before_decision"] = refresh_requested
    payload["refresh_needed_before_decision"] = refresh_needed
    payload["refresh_source_available"] = source_available
    payload["initial_recency"] = initial_coverage["recency"]
    payload["final_recency"] = final_coverage["recency"]
    payload["operations"]["new_capture"] = refresh_requested
    if refresh_requested:
        payload["operations"]["browser_access"] = "refresh_source_command_or_input"
    payload["cache_comparison"] = {
        "raw_cache_checked": args.raw_cache is not None,
        "snapshot_match_count": snapshot_meta["match_count"],
        "raw_cache_match_count": raw_cache_meta["match_count"],
        "snapshot_latest_captured_at": snapshot_meta["latest_captured_at"],
        "raw_cache_latest_captured_at": raw_cache_meta["latest_captured_at"],
        "content_hash_match": cache_hash_match,
        "raw_text_returned": False,
        "path_output": "omitted",
    }
    payload["refresh_snapshot"] = (
        {
            "saved": bool(refresh_payload.get("saved")),
            "changed": bool(refresh_payload.get("changed")),
            "target_key": str(refresh_payload.get("target_key") or ""),
            "content_hash": str(refresh_payload.get("content_hash") or ""),
            "raw_text_returned": False,
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }
        if refresh_payload
        else {
            "saved": False,
            "changed": False,
            "target_key": "",
            "content_hash": "",
            "raw_text_returned": False,
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }
    )
    if args.json:
        print(_json(payload))
        return 0 if payload["decision"] == "snapshot_metadata_ready" else 2
    print(payload["message"])
    print(f"refresh_attempted_before_decision: {str(refresh_requested).lower()}")
    if refresh_payload:
        print(f"refresh_saved: {str(refresh_payload['saved']).lower()}")
        print(f"refresh_changed: {str(refresh_payload['changed']).lower()}")
    print(f"decision: {payload['decision']}")
    print(f"next_step: {payload['next_step']}")
    print(f"recommended_command: {payload['recommended_command']}")
    print(f"text_required_tools_allowed: {str(payload['text_required_tools_allowed']).lower()}")
    print(f"raw_text_returned: {str(payload['raw_text_returned']).lower()}")
    print("path_output: omitted")
    print("outbound: disabled")
    return 0 if payload["decision"] == "snapshot_metadata_ready" else 2


def _cmd_snapshot_discord_url_text(args: argparse.Namespace) -> int:
    text = read_visible_text_arg(args)
    payload = snapshot_visible_text(
        text=text,
        url=args.url,
        title=args.title,
        source=args.source,
        path=args.snapshot_store,
    )
    if args.json:
        print(_json(payload))
        return 0
    print(payload["message"])
    print(f"saved: {str(payload['saved']).lower()}")
    print(f"changed: {str(payload['changed']).lower()}")
    print(f"target_key: {payload['target_key']}")
    print(f"content_hash: {payload['content_hash']}")
    print(f"snapshot_count_for_target: {payload['snapshot_count_for_target']}")
    print("raw_text_returned: false")
    print("path_output: omitted")
    print("outbound: disabled")
    return 0


def _cmd_attachment_ledger(args: argparse.Namespace) -> int:
    payload = build_attachment_ledger(records=load_jsonl_records(args.input), output=args.output)
    if args.json:
        print(_json(payload))
        return 0
    print(payload["message"])
    print(f"attachment_count: {payload['attachment_count']}")
    print("raw_url_returned: false")
    print("raw_text_returned: false")
    print("path_output: omitted")
    print("outbound: disabled")
    return 0


def _cmd_attachment_ocr_log(args: argparse.Namespace) -> int:
    if bool(args.ocr_text_file) == bool(args.ocr_command):
        raise SystemExit("--ocr-text-file または --ocr-command のどちらか一方を指定してください。")
    ocr_text = (
        args.ocr_text_file.read_text(encoding="utf-8")
        if args.ocr_text_file
        else read_command_text(args.ocr_command, empty_message="OCR command の出力が空です。", timeout=args.source_timeout)
    )
    payload = write_attachment_ocr_log(
        source_key=args.source_key,
        ocr_text=ocr_text,
        output=args.output,
        note_label=args.note_label,
    )
    print(_json(payload))
    return 0


def _cmd_verify_url_intake(args: argparse.Namespace) -> int:
    payload = build_url_intake_gate(
        args.url,
        raw_cache_path=args.raw_cache,
        ai_log_path=args.ai_log,
        target_key=args.target_key or None,
        sync=args.sync,
    )
    print(_json(payload))
    return 0 if payload["state"] == "ready" else 2


def _cmd_coverage_report(args: argparse.Namespace) -> int:
    payload = build_coverage_report(
        url=args.url,
        target_key=args.target_key,
        raw_cache_path=args.raw_cache,
        ai_log_path=args.ai_log,
        source_kind=args.source_kind,
        dedupe_policy=args.dedupe_policy,
    )
    print(_json(payload))
    return 0 if payload["coverage"]["exact_coverage"] else 2


def _cmd_thread_capture_plan(args: argparse.Namespace) -> int:
    rest_configured = bool(
        args.rest_configured
        or os.environ.get("DISCORD_" + "BOT_TOKEN", "").strip()
        or os.environ.get("DISCORD_CONTEXT_BRIDGE_REST_COMMAND", "").strip()
    )
    payload = plan_full_thread_capture(
        url=args.url,
        snapshot_store=args.snapshot_store,
        raw_cache_path=args.raw_cache,
        gateway_configured=args.gateway_configured,
        rest_configured=rest_configured,
        bot_inbox_ready=args.bot_inbox_ready,
        private_adapter_configured=args.private_adapter_configured,
        visible_dom_available=args.visible_dom_available,
    )
    if args.json:
        print(_json(payload))
        return 0 if payload["ok"] else 2
    print(payload["message"])
    print(f"state: {payload['state']}")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"full_thread_confirmed: {str(payload.get('coverage_now', {}).get('full_thread_confirmed', False)).lower()}")
    print(f"visible_dom_is_full_thread_proof: {str(payload.get('coverage_now', {}).get('visible_dom_is_full_thread_proof', False)).lower()}")
    print(f"blockers: {', '.join(payload.get('blockers') or []) or 'なし'}")
    print("raw_text_returned: false")
    print("path_output: omitted")
    print("outbound: disabled")
    return 0 if payload["ok"] else 2


def _cmd_reply_context_plan(args: argparse.Namespace) -> int:
    payload = _reply_context_gate_from_args(args)
    print(_json(payload))
    return 0 if payload["reply_generation_allowed"] else 2


def _cmd_cache_first_intake(args: argparse.Namespace) -> int:
    payload = build_cache_first_intake(
        url=args.url,
        snapshot_store=args.snapshot_store,
        cache_root=args.cache_root,
        book_output=args.book_output,
    )
    if args.json:
        print(_json(payload))
        return 0 if payload["ok"] else 2
    print(payload["message"])
    print(f"state: {payload['state']}")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"source_selected: {payload['snapshot']['source_selected']}")
    print(f"book_created: {str(payload['book']['created']).lower()}")
    print(f"book_status: {payload['book']['status']}")
    print(f"raw_text_returned: {str(payload['raw_text_returned']).lower()}")
    print("path_output: omitted")
    print("outbound: disabled")
    return 0 if payload["ok"] else 2


def _cmd_handoff_packet(args: argparse.Namespace) -> int:
    dashboard = status_dashboard(args.store, github_state=args.github_state)
    review_state = get_review_state(args.thread_key, path=args.review_store)
    packet = build_handoff_packet(thread_key=args.thread_key, review_state=review_state, status=dashboard)
    if args.json:
        print(_json(packet))
        return 0
    print(packet["message"])
    current = packet["current_state"]
    print(f"current: review_state_available={str(current['review_state_available']).lower()} context_available={str(current['context_available']).lower()}")
    print(f"gate: {current['gate_decision']} / copy_block: {current['copy_block_status']}")
    print(f"next: {packet['next_action']}")
    print("outbound: disabled")
    return 0


def _cmd_context_upsert(args: argparse.Namespace) -> int:
    print(
        _json(
            upsert_context_document(
                args.kind,
                args.key,
                args.input.read_text(encoding="utf-8"),
                path=args.context_store,
                source=args.source,
                labels=args.label,
            )
        )
    )
    return 0


def _cmd_context_list(args: argparse.Namespace) -> int:
    print(_json(list_context_documents(args.context_store)))
    return 0


def _cmd_audit_context_store(args: argparse.Namespace) -> int:
    report = audit_context_store(args.context_store)
    print(_json(report))
    return 0 if report["safe_for_tunnel"] else 2


def _cmd_review_intent(args: argparse.Namespace) -> int:
    review = review_reply_intent(
        args.draft,
        load_events(args.store),
        understanding_confirmed=args.understanding_confirmed,
        reply_context_gate=_reply_context_gate_from_args(args),
    )
    review = {
        **review,
        "likely_counterparty_meaning": "omitted",
        "suggested_correction": "omitted",
    }
    if args.artifact_path:
        args.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        args.artifact_path.write_text(build_review_artifact_markdown(args.draft, review), encoding="utf-8")
        review = {
            **review,
            "artifact": {
                "schema": "discord_review_artifact_markdown.v1",
                "created": True,
                "path_output": "omitted",
                "raw_discord_text_output": "omitted",
                "participant_names_output": "omitted",
                "outbound_actions": "disabled",
            },
        }
    if args.save_review_state:
        saved = upsert_review_state(args.thread_key, review, path=args.review_store)
        review = {
            **review,
            "review_state": {
                "schema": "discord_review_state.v1",
                "saved": True,
                "created": saved["created"],
                "path_output": "omitted",
                "thread_key": saved["entry"]["thread_key"],
                "outbound_actions": "disabled",
            },
        }
    print(_json(review))
    return 0 if review.get("copy_block", {}).get("status") in {"ready", "split"} else 2


def _cmd_guide_reply(args: argparse.Namespace) -> int:
    text = read_visible_text_arg(args)
    guide = guide_reply_from_text(
        text,
        args.draft,
        guild_label=args.guild,
        channel_label=args.channel,
        understanding_confirmed=args.understanding_confirmed,
        reply_context_gate=_reply_context_gate_from_args(args),
    )
    if args.json:
        print(_json(guide))
        return 0 if guide.get("reply_review", {}).get("copy_block", {}).get("status") in {"ready", "split"} else 2
    print(guide["message"])
    print(f"解析件数: {guide['parsed']}")
    print(f"相手側の文脈: {guide['counterparty_context']}")
    review = guide["reply_review"]
    print(review["quick_verdict_label"])
    print(review["ok_to_reply_label"])
    print(review["alignment_label"])
    print(review["missing_knowledge_label"])
    for action in guide["next_actions"]:
        print(f"- {action}")
    print(guide["send_capability_label"])
    return 0 if review.get("copy_block", {}).get("status") in {"ready", "split"} else 2


def _cmd_stage_discord_send(args: argparse.Namespace) -> int:
    packet = build_discord_send_staging_packet(
        args.draft,
        load_events(args.store),
        mode=args.mode,
        target_url=args.target_url,
        mention_label=args.mention_label,
        understanding_confirmed=args.understanding_confirmed,
        reply_context_gate=_reply_context_gate_from_args(args),
    )
    exit_code = 0 if packet["staging_status"] == "ready_to_fill" else 2
    if args.json:
        print(_json(packet))
        return exit_code
    print(packet["message"])
    print(f"mode: {packet['mode']}")
    print(f"staging_status: {packet['staging_status']}")
    if packet["blockers"]:
        print("blockers: " + " / ".join(packet["blockers"]))
    print(packet["send_capability_label"])
    return exit_code


def _cmd_verify_chrome_fill_dry_run(args: argparse.Namespace) -> int:
    packet = json.loads(args.staging_packet.read_text(encoding="utf-8"))
    report = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=args.socket_preflight,
        target_url_verified=args.target_url_verified,
        socket_after_navigation=args.socket_after_navigation,
        latest_target_snapshot_confirmed=args.latest_target_snapshot_confirmed,
        reply_ui_candidates=args.reply_ui_candidates,
        message_box_candidates=args.message_box_candidates,
        draft_matches_copy_block=args.draft_matches_copy_block,
        socket_pre_send=args.socket_pre_send,
    )
    exit_code = 0 if report["dry_run_status"] == "ready_to_fill" else 2
    if args.json:
        print(_json(report))
        return exit_code
    print(report["message"])
    print(f"dry_run_status: {report['dry_run_status']}")
    if report["blockers"]:
        print("blockers: " + " / ".join(report["blockers"]))
    print(report["send_capability_label"])
    return exit_code


def _cmd_closeout_discord_send(args: argparse.Namespace) -> int:
    staging_packet = json.loads(args.staging_packet.read_text(encoding="utf-8")) if args.staging_packet else None
    dry_run_report = json.loads(args.dry_run_report.read_text(encoding="utf-8")) if args.dry_run_report else None
    packet = build_discord_post_send_closeout_packet(
        staging_packet=staging_packet,
        dry_run_report=dry_run_report,
        human_sent_observed=args.human_sent_observed,
        human_reviewed=args.human_reviewed,
        observed_text_status=args.observed_text_status,
        unread_check_status=args.unread_check_status,
        unread_signal_count=args.unread_signal_count,
        observed_message_id=args.observed_message_id,
        observed_url=args.observed_url,
        note_label=args.note_label,
    )
    exit_code = 0 if packet["closeout_status"] == "closed" else 2
    if args.json:
        print(_json(packet))
        return exit_code
    print(packet["message"])
    print(f"closeout_status: {packet['closeout_status']}")
    if packet["blockers"]:
        print("blockers: " + " / ".join(packet["blockers"]))
    print(packet["send_capability_label"])
    return exit_code


def _read_json_file(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _cmd_send_operation_status(args: argparse.Namespace) -> int:
    packet = build_discord_send_operation_status(
        staging_packet=_read_json_file(args.staging_packet),
        dry_run_report=_read_json_file(args.dry_run_report),
        closeout_report=_read_json_file(args.closeout_report),
        target_label=args.target_label,
        target_environment=args.target_environment,
        rollback_plan_reviewed=args.rollback_plan_reviewed,
        production_runbook_fixed=args.production_runbook_fixed,
        post_send_retrospective=args.post_send_retrospective,
    )
    exit_code = 0 if packet["ok"] else 2
    if args.json:
        print(_json(packet))
        return exit_code
    print("Discord送信テスト運転表")
    print(f"state: {packet['state']}")
    print(f"target: {packet['target']['label']} ({packet['target']['environment']})")
    for check in packet["checks"]:
        line = f"- {check['status']}: {check['name']}"
        if check.get("blocker"):
            line += f" / blocker={check['blocker']}"
        print(line)
    for action in packet["summary"]["next"]:
        print(f"next: {action}")
    return exit_code


def read_visible_text_arg(args: argparse.Namespace) -> str:
    if args.from_clipboard:
        return read_clipboard_text(args.clipboard_command)
    if args.source_command:
        return read_command_text(args.source_command, empty_message="source command の出力が空です。")
    if args.input:
        return args.input.read_text(encoding="utf-8")
    return sys.stdin.read()


def _cmd_context_passport(args: argparse.Namespace) -> int:
    text = read_visible_text_arg(args)
    passport = build_context_passport_from_args(text, args)
    if args.json:
        print(_json(passport))
        return 0
    print(passport["message"])
    print(f"解析件数: {passport['parsed']}")
    print(passport["external_context_used_label"])
    if passport.get("auto_context_bindings_used"):
        print(passport["auto_context_bindings_label"])
    print(passport["context_sources_label"])
    print(passport["thread_purpose_label"])
    print(passport["conversation_flow_label"])
    print(passport["implicit_premises_label"])
    print(passport["rule_notes_label"])
    print(passport["people_temperature_label"])
    print(passport["context_ready_label"])
    for action in passport["natural_entry_angles"]:
        print(f"- {action}")
    print(passport["send_capability_label"])
    return 0


def _cmd_chew_context(args: argparse.Namespace) -> int:
    text = read_visible_text_arg(args)
    digestion = build_context_digestion_from_args(text, args)
    if args.json:
        print(_json(digestion))
        return 0 if digestion["parsed"] else 2
    print(digestion["message"])
    print(f"解析件数: {digestion['parsed']}")
    print(digestion["thread_purpose_label"])
    print(digestion["people_temperature_label"])
    print(digestion["confidence_label"])
    print("理解レイヤー:")
    for layer in digestion["understanding_layers"]:
        print(f"- {layer}")
    print("未確認:")
    if digestion["open_questions"]:
        for question in digestion["open_questions"]:
            print(f"- {question}")
    else:
        print("- なし")
    print("次アクション:")
    for action in digestion["next_actions"]:
        print(f"- {action}")
    print(digestion["send_capability_label"])
    return 0 if digestion["parsed"] else 2


def _cmd_context_mode(args: argparse.Namespace) -> int:
    text = read_visible_text_arg(args)
    packet = build_context_mode_from_args(text, args)
    if args.json:
        print(_json(packet))
        return 0 if packet["route"] not in {"read_context", "observe_only"} else 2
    print(packet["message"])
    print(f"mode: {packet['mode']}")
    print(f"route: {packet['route']}")
    print(f"解析件数: {packet['parsed']}")
    print(packet["thread_purpose_label"])
    print(packet["people_temperature_label"])
    print("checklist:")
    for item in packet["checklist"]:
        print(f"- {item}")
    print("next:")
    for action in packet["next_actions"]:
        print(f"- {action}")
    print(packet["send_capability_label"])
    return 0 if packet["route"] not in {"read_context", "observe_only"} else 2


def build_context_passport_from_args(text: str, args: argparse.Namespace) -> dict[str, Any]:
    contexts = read_passport_contexts(args)
    passport = context_passport_from_text(
        text,
        guild_label=args.guild,
        channel_label=args.channel,
        server_context=contexts["server_context"],
        channel_context=contexts["channel_context"],
        thread_context=contexts["thread_context"],
    )
    if contexts["auto_context_bindings_used"]:
        passport["auto_context_bindings_used"] = True
        passport["auto_context_bindings_label"] = "safe label から文脈庫を補助参照しました。"
    else:
        passport["auto_context_bindings_used"] = False
        passport["auto_context_bindings_label"] = "safe label による自動文脈参照は使っていません。"
    return passport


def build_context_digestion_from_args(text: str, args: argparse.Namespace) -> dict[str, Any]:
    contexts = read_passport_contexts(args)
    digestion = digest_context_from_text(
        text,
        guild_label=args.guild,
        channel_label=args.channel,
        server_context=contexts["server_context"],
        channel_context=contexts["channel_context"],
        thread_context=contexts["thread_context"],
        focus=args.focus,
    )
    digestion["auto_context_bindings_used"] = bool(contexts["auto_context_bindings_used"])
    digestion["auto_context_bindings_label"] = (
        "safe label から文脈庫を補助参照しました。"
        if contexts["auto_context_bindings_used"]
        else "safe label による自動文脈参照は使っていません。"
    )
    return digestion


def build_context_mode_from_args(text: str, args: argparse.Namespace) -> dict[str, Any]:
    contexts = read_passport_contexts(args)
    packet = context_operating_mode_from_text(
        text,
        mode=args.context_mode,
        guild_label=args.guild,
        channel_label=args.channel,
        server_context=contexts["server_context"],
        channel_context=contexts["channel_context"],
        thread_context=contexts["thread_context"],
        focus=args.focus,
    )
    packet["auto_context_bindings_used"] = bool(contexts["auto_context_bindings_used"])
    packet["auto_context_bindings_label"] = (
        "safe label から文脈庫を補助参照しました。"
        if contexts["auto_context_bindings_used"]
        else "safe label による自動文脈参照は使っていません。"
    )
    return packet


def read_optional_text_file(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if path else ""


def merge_context_text(*parts: str) -> str:
    return "\n".join(part for part in parts if part.strip())


def read_passport_contexts(args: argparse.Namespace) -> dict[str, Any]:
    bindings = (
        resolve_context_bindings(
            guild_label=args.guild,
            channel_label=args.channel,
            path=args.context_store,
        )
        if getattr(args, "auto_context_bindings", False)
        else {}
    )
    auto_used = False

    def context_from_key(kind: str, explicit_key: str | None) -> str:
        nonlocal auto_used
        if explicit_key:
            return get_context_document(kind, explicit_key, path=args.context_store)
        binding = bindings.get(kind)
        if binding:
            auto_used = True
            return get_context_document(kind, binding["key"], path=args.context_store)
        return ""

    return {
        "server_context": merge_context_text(
            context_from_key("server", args.server_context_key),
            read_optional_text_file(args.server_context),
        ),
        "channel_context": merge_context_text(
            context_from_key("channel", args.channel_context_key),
            read_optional_text_file(args.channel_context),
        ),
        "thread_context": merge_context_text(
            context_from_key("thread", args.thread_context_key),
            read_optional_text_file(args.thread_context),
        ),
        "auto_context_bindings_used": auto_used,
    }


def _cmd_watch_passport(args: argparse.Namespace) -> int:
    last_text = ""
    polls = 0
    changed = 0
    while args.max_polls <= 0 or polls < args.max_polls:
        polls += 1
        text = read_command_text(
            args.source_command,
            empty_message="source command の出力が空です。",
            timeout=args.source_timeout,
        )
        if text != last_text:
            last_text = text
            changed += 1
            passport = build_context_passport_from_args(text, args)
            payload = {
                **passport,
                "language": "ja",
                "event": "passport_update",
                "event_label": "文脈カード更新",
                "message": "スレッド文脈カードを更新しました。",
                "poll": polls,
                "changed": changed,
            }
            if args.json:
                print(_json(payload), flush=True)
            else:
                print(f"\n== 文脈カード更新 #{changed} ==")
                if passport.get("auto_context_bindings_used"):
                    print(passport["auto_context_bindings_label"])
                print(passport["thread_purpose_label"])
                print(passport["conversation_flow_label"])
                print(passport["implicit_premises_label"])
                print(passport["rule_notes_label"])
                print(passport["people_temperature_label"])
                print(passport["context_ready_label"])
                for action in passport["natural_entry_angles"]:
                    print(f"- {action}")
                print(passport["send_capability_label"], flush=True)
        if args.max_polls <= 0 or polls < args.max_polls:
            time.sleep(args.interval)
    if args.json:
        print(
            _json(
                {
                    "language": "ja",
                    "event": "watch_passport_complete",
                    "event_label": "文脈カード監視完了",
                    "message": "文脈カード監視を終了しました。",
                    "polls": polls,
                    "changed": changed,
                }
            )
        )
    else:
        print(f"\n完了: 文脈カード監視を終了しました。確認回数: {polls} / 更新: {changed}")
    return 0


def _cmd_watch_guide(args: argparse.Namespace) -> int:
    last_text = ""
    polls = 0
    changed = 0
    while args.max_polls <= 0 or polls < args.max_polls:
        polls += 1
        text = read_command_text(
            args.source_command,
            empty_message="source command の出力が空です。",
            timeout=args.source_timeout,
        )
        if text != last_text:
            last_text = text
            changed += 1
            guide = guide_reply_from_text(
                text,
                args.draft,
                guild_label=args.guild,
                channel_label=args.channel,
                understanding_confirmed=args.understanding_confirmed,
            )
            payload = {
                **guide,
                "language": "ja",
                "event": "guide_update",
                "event_label": "返信ガイド更新",
                "message": "Discord 返信ガイドを更新しました。",
                "poll": polls,
                "changed": changed,
            }
            if args.json:
                print(_json(payload), flush=True)
            else:
                print(f"\n== 返信ガイド更新 #{changed} ==")
                print(guide["counterparty_context"])
                print(guide["reply_review"]["ok_to_reply_label"])
                print(guide["reply_review"]["alignment_label"])
                for action in guide["next_actions"]:
                    print(f"- {action}")
                print(guide["send_capability_label"], flush=True)
        if args.max_polls <= 0 or polls < args.max_polls:
            time.sleep(args.interval)
    if args.json:
        print(
            _json(
                {
                    "language": "ja",
                    "event": "watch_guide_complete",
                    "event_label": "返信ガイド監視完了",
                    "message": "返信ガイド監視を終了しました。",
                    "polls": polls,
                    "changed": changed,
                }
            )
        )
    else:
        print(f"\n完了: 返信ガイド監視を終了しました。確認回数: {polls} / 更新: {changed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
