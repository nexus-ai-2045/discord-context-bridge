from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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
    ops_view_summary,
    review_reply_intent,
    resolve_context_bindings,
    upsert_context_document,
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


def build_parser() -> argparse.ArgumentParser:
    parser = JapaneseArgumentParser(description="Discord の可視会話テキストを扱うローカル文脈ブリッジ")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE, help="取り込んだ可視テキストを保存するローカルファイル")
    parser.add_argument("--context-store", type=Path, default=DEFAULT_CONTEXT_STORE, help="サーバールールやスレッド目的を保存するローカル文脈庫")
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
    review.set_defaults(handler=_cmd_review_intent)

    draft_review = sub.add_parser("review-draft", help="返信下書きを保存済みの直近文脈と照合する")
    draft_review.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    draft_review.set_defaults(handler=_cmd_review_intent)

    guide = sub.add_parser("guide-reply", help="Discord 可視テキストと返信下書きから会話ガイドを作る")
    guide.add_argument("--input", type=Path, help="読み込むテキストファイル")
    guide.add_argument("--from-clipboard", action="store_true", help="--input ではなくローカルのクリップボードから可視テキストを読む")
    guide.add_argument("--clipboard-command", default="pbpaste", help="クリップボード取得に使うローカルコマンド")
    guide.add_argument("--source-command", help="Discord 可視テキストを出力するローカルコマンド")
    guide.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    guide.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    guide.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
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
    watch_passport.add_argument("--json", action="store_true", help="機械処理用に JSON lines で出力する")
    watch_passport.set_defaults(handler=_cmd_watch_passport)

    watch_guide = sub.add_parser("watch-guide", help="ローカルコマンドを監視し、変化した Discord 可視テキストから会話ガイドを作る")
    watch_guide.add_argument("--source-command", required=True, help="Discord 可視テキストを出力するローカルコマンド")
    watch_guide.add_argument("--draft", required=True, help="送信前に確認したい返信下書き")
    watch_guide.add_argument("--guild", default="example-community", help="サーバー名または仮ラベル")
    watch_guide.add_argument("--channel", default="general", help="チャンネル名または仮ラベル")
    watch_guide.add_argument("--interval", type=float, default=1.0, help="入力元コマンドを確認する間隔秒")
    watch_guide.add_argument("--max-polls", type=int, default=0, help="確認回数。0 の場合は停止されるまで継続する")
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


def read_command_text(
    command: str,
    *,
    empty_message: str = "source command の出力が空です。",
    timeout: float | None = None,
) -> str:
    tokens = shlex.split(command)
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
        raise SystemExit(
            "local command の実行に失敗しました: "
            f"command={command!r} stderr={completed.stderr.strip()}"
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
    print(_json(review_reply_intent(args.draft, load_events(args.store))))
    return 0


def _cmd_guide_reply(args: argparse.Namespace) -> int:
    text = read_visible_text_arg(args)
    guide = guide_reply_from_text(
        text,
        args.draft,
        guild_label=args.guild,
        channel_label=args.channel,
    )
    if args.json:
        print(_json(guide))
        return 0
    print(guide["message"])
    print(f"解析件数: {guide['parsed']}")
    print(f"相手側の文脈: {guide['counterparty_context']}")
    review = guide["reply_review"]
    print(review["ok_to_reply_label"])
    print(review["alignment_label"])
    print(review["missing_knowledge_label"])
    for action in guide["next_actions"]:
        print(f"- {action}")
    print(guide["send_capability_label"])
    return 0


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
        text = read_command_text(args.source_command, empty_message="source command の出力が空です。")
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
        text = read_command_text(args.source_command, empty_message="source command の出力が空です。")
        if text != last_text:
            last_text = text
            changed += 1
            guide = guide_reply_from_text(
                text,
                args.draft,
                guild_label=args.guild,
                channel_label=args.channel,
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
