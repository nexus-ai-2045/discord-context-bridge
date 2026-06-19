from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.cli import read_command_text
from discord_context_bridge.core import audit_event_store, import_visible_text, ops_view_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="実Discord可視本文を出力せずに adapter -> store -> ops-view を確認する"
    )
    parser.add_argument("--source-command", required=True, help="Discord 可視テキストを stdout に出す local command")
    parser.add_argument("--store", type=Path, default=Path("/tmp/dcb-live-ops-smoke.ndjson"))
    parser.add_argument("--guild", default="live-smoke", help="実IDではなく安全な仮ラベル")
    parser.add_argument("--channel", default="visible-thread", help="実IDではなく安全な仮ラベル")
    parser.add_argument("--reset", action="store_true", help="既存の smoke store を消してから実行する")
    parser.add_argument("--json", action="store_true", help="本文なしの JSON summary を出力する")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_summary(imported: dict, ops: dict, audit: dict) -> dict:
    return {
        "language": "ja",
        "message": "live ops smoke が完了しました。",
        "store": ops["store"],
        "parsed": imported["parsed"],
        "appended": imported["appended"],
        "duplicate": imported["duplicate"],
        "event_count": ops["event_count"],
        "safe_labels": ops["safe_labels"],
        "last_seen": ops["last_seen"],
        "delta_count": imported["appended"],
        "gate_verdict": ops["gate_verdict"],
        "gate_verdict_label": ops["gate_verdict_label"],
        "issue_count": audit["issue_count"],
        "outbound": ops["outbound"],
        "outbound_label": ops["outbound_label"],
        "text_output": "omitted",
    }


def print_human(summary: dict) -> None:
    print(summary["message"])
    print(f"safe label: {', '.join(summary['safe_labels']) if summary['safe_labels'] else 'なし'}")
    print(f"parsed: {summary['parsed']}")
    print(f"appended: {summary['appended']}")
    print(f"duplicate: {summary['duplicate']}")
    print(f"event count: {summary['event_count']}")
    print(f"last_seen: {summary['last_seen'] or 'なし'}")
    print(f"delta count: {summary['delta_count']}")
    print(f"gate verdict: {summary['gate_verdict']}")
    print(summary["gate_verdict_label"])
    print(summary["outbound_label"])
    print("message text: omitted")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.reset and args.store.exists():
        args.store.unlink()

    text = read_command_text(args.source_command, empty_message="source command の出力が空です。")
    imported = import_visible_text(
        text,
        path=args.store,
        guild_label=args.guild,
        channel_label=args.channel,
    )
    audit = audit_event_store(args.store)
    ops = ops_view_summary(args.store)
    summary = build_summary(imported, ops, audit)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(summary)
    return 0 if summary["gate_verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
