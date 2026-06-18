from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .core import DEFAULT_STORE, fast_briefing, import_visible_text, load_events, review_reply_intent


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord の可視会話テキストを扱う local context bridge")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    sub = parser.add_subparsers(dest="command", required=True)

    visible = sub.add_parser("import-visible-text", help="可視テキストを local event store に取り込む")
    visible.add_argument("--input", type=Path)
    visible.add_argument("--guild", default="example-community")
    visible.add_argument("--channel", default="general")
    visible.set_defaults(handler=_cmd_import_visible_text)

    briefing = sub.add_parser("fast-briefing", help="直近文脈の短い briefing を表示する")
    briefing.set_defaults(handler=_cmd_fast_briefing)

    review = sub.add_parser("review-intent", help="返信意図を直近文脈と照合する")
    review.add_argument("--draft", required=True, help="送信前に確認したい返信 draft")
    review.set_defaults(handler=_cmd_review_intent)
    return parser


def _cmd_import_visible_text(args: argparse.Namespace) -> int:
    text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    print(
        _json(
            import_visible_text(
                text,
                path=args.store,
                guild_label=args.guild,
                channel_label=args.channel,
            )
        )
    )
    return 0


def _cmd_fast_briefing(args: argparse.Namespace) -> int:
    print(_json(fast_briefing(load_events(args.store))))
    return 0


def _cmd_review_intent(args: argparse.Namespace) -> int:
    print(_json(review_reply_intent(args.draft, load_events(args.store))))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
