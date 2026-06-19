from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.cli import read_command_text, safe_command_failure_reason
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
    parser.add_argument("--source-timeout", type=float, default=20.0, help="source command の最大秒数")
    parser.add_argument("--min-parsed", type=int, default=1, help="成功扱いに必要な最小解析件数")
    parser.add_argument("--json", action="store_true", help="本文なしの JSON summary を出力する")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_summary(imported: dict, ops: dict, audit: dict, *, min_parsed: int = 1) -> dict:
    source_ready = imported["parsed"] >= min_parsed
    gate_verdict = ops["gate_verdict"] if source_ready else "source_not_ready"
    gate_verdict_label = ops["gate_verdict_label"] if source_ready else "Discord 可視本文をまだ解析できていません。"
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
        "min_parsed": min_parsed,
        "source_ready": source_ready,
        "source_ready_label": "Discord 可視本文を解析できました。" if source_ready else "Discord 可視本文をまだ解析できていません。",
        "gate_verdict": gate_verdict,
        "gate_verdict_label": gate_verdict_label,
        "issue_count": audit["issue_count"],
        "outbound": ops["outbound"],
        "outbound_label": ops["outbound_label"],
        "text_output": "omitted",
    }


def classify_source_failure(reason: str) -> tuple[str, str]:
    normalized = reason.casefold()
    if "timeout" in normalized or "秒で完了しませんでした" in normalized:
        return "source_command_timeout", "source_command_timeout"
    if "permission" in normalized or "not authorized" in normalized:
        return "accessibility_permission", "accessibility_permission"
    if "ocr_empty" in normalized:
        return "ocr_empty", "ocr_empty"
    if "empty" in normalized or "空" in normalized:
        return "source_empty", "source_empty"
    if "not_found" in normalized:
        return "dependency_missing", "window_not_found"
    return "source_command_failed", "capture_failed"


def safe_source_failure_reason(reason: str) -> str:
    if "reason=" in reason:
        tail = reason.rsplit("reason=", 1)[1].strip()
        if tail in {"timeout", "permission", "empty", "ocr_empty", "not_found", "adapter_failed"}:
            return tail
    if "秒で完了しませんでした" in reason:
        return "timeout"
    safe_reason = safe_command_failure_reason(reason)
    if safe_reason == "adapter_failed" and "not_found" in reason.casefold():
        return "not_found"
    return safe_reason


def build_source_failure_summary(reason: str, *, min_parsed: int) -> dict:
    safe_reason = safe_source_failure_reason(reason)
    failure_stage, source_stage = classify_source_failure(safe_reason if safe_reason != "adapter_failed" else reason)
    return {
        "language": "ja",
        "message": "live ops smoke は source command で停止しました。",
        "store": "",
        "parsed": 0,
        "appended": 0,
        "duplicate": 0,
        "event_count": 0,
        "safe_labels": [],
        "last_seen": "",
        "delta_count": 0,
        "min_parsed": min_parsed,
        "source_ready": False,
        "source_ready_label": "Discord 可視本文をまだ解析できていません。",
        "gate_verdict": "source_not_ready",
        "gate_verdict_label": "source command の取得に失敗しました。",
        "failure_stage": failure_stage,
        "source_stage": source_stage,
        "reason": safe_reason,
        "issue_count": 0,
        "outbound": "disabled",
        "outbound_label": "送信系 action は無効です。",
        "outbound_actions": "disabled",
        "text_output": "omitted",
    }


def print_human(summary: dict) -> None:
    print(summary["message"])
    print(f"safe label: {', '.join(summary['safe_labels']) if summary['safe_labels'] else 'なし'}")
    print(f"parsed: {summary['parsed']}")
    print(f"min parsed: {summary['min_parsed']}")
    print(summary["source_ready_label"])
    print(f"appended: {summary['appended']}")
    print(f"duplicate: {summary['duplicate']}")
    print(f"event count: {summary['event_count']}")
    print(f"last_seen: {summary['last_seen'] or 'なし'}")
    print(f"delta count: {summary['delta_count']}")
    print(f"gate verdict: {summary['gate_verdict']}")
    print(summary["gate_verdict_label"])
    if summary.get("failure_stage"):
        print(f"failure stage: {summary['failure_stage']}")
    if summary.get("source_stage"):
        print(f"source stage: {summary['source_stage']}")
    print(summary["outbound_label"])
    print("message text: omitted")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.reset and args.store.exists():
        args.store.unlink()

    try:
        text = read_command_text(
            args.source_command,
            empty_message="source command の出力が空です。",
            timeout=args.source_timeout,
        )
    except SystemExit as exc:
        summary = build_source_failure_summary(str(exc), min_parsed=args.min_parsed)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_human(summary)
        return 2
    imported = import_visible_text(
        text,
        path=args.store,
        guild_label=args.guild,
        channel_label=args.channel,
    )
    audit = audit_event_store(args.store)
    ops = ops_view_summary(args.store)
    summary = build_summary(imported, ops, audit, min_parsed=args.min_parsed)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(summary)
    return 0 if summary["gate_verdict"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
