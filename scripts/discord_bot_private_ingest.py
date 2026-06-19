#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import discord_bot_route_preflight
from discord_context_bridge.core import (
    context_passport_from_text,
    guide_reply_from_text,
    import_visible_text,
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def read_input_text(path: Path | None) -> str:
    if path:
        return path.read_text(encoding="utf-8")
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="@discord bot channel route から渡された private text を本文なしで文脈化する。"
    )
    parser.add_argument("--input", type=Path, help="private adapter が保存した一時テキスト。本文は出力しない。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--guild", default="discord-bot-route", help="実IDではなく安全な仮ラベル")
    parser.add_argument("--channel", default="private-ingest", help="実IDではなく安全な仮ラベル")
    parser.add_argument("--draft", default="", help="任意。返信前 gate にかける下書き。")
    parser.add_argument("--min-parsed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def build_ingest_payload(
    text: str,
    *,
    channel_dir: Path,
    guild: str,
    channel: str,
    draft: str,
    min_parsed: int,
) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    if not text.strip():
        return {
            "schema": "discord_bot_private_ingest.v1",
            "ok": False,
            "stage": "input",
            "preflight": preflight,
            "failure_stage": "source_empty",
            "reason": "private bot route text が空です。",
            "parsed": 0,
            "context_ready": False,
            "text_output": "omitted",
            "outbound_actions": "disabled",
        }

    with tempfile.TemporaryDirectory(prefix="dcb-bot-ingest-") as temp_dir:
        store = Path(temp_dir) / "events.ndjson"
        imported = import_visible_text(text, path=store, guild_label=guild, channel_label=channel)

    passport = context_passport_from_text(text, guild_label=guild, channel_label=channel)
    reply_gate = guide_reply_from_text(text, draft, guild_label=guild, channel_label=channel) if draft else None
    parsed = int(imported["parsed"])
    context_ready = bool(passport.get("context_ready"))
    ok = bool(preflight["ok"]) and parsed >= min_parsed and context_ready
    return {
        "schema": "discord_bot_private_ingest.v1",
        "ok": ok,
        "stage": "done" if ok else "ingest",
        "preflight": preflight,
        "parsed": parsed,
        "min_parsed": min_parsed,
        "context_ready": context_ready,
        "context_ready_label": passport.get("context_ready_label"),
        "thread_purpose": passport.get("thread_purpose"),
        "people_temperature": passport.get("people_temperature"),
        "quick_verdict": reply_gate["reply_review"]["quick_verdict"] if reply_gate else None,
        "quick_verdict_label": reply_gate["reply_review"]["quick_verdict_label"] if reply_gate else None,
        "send_capability": "disabled",
        "text_output": "omitted",
        "text_saved": False,
        "outbound_actions": "disabled",
    }


def print_human(payload: dict[str, Any]) -> None:
    print("Discord bot private ingest status")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"stage: {payload['stage']}")
    print(f"parsed: {payload.get('parsed', 0)}")
    print(f"context_ready: {str(payload.get('context_ready', False)).lower()}")
    if payload.get("failure_stage"):
        print(f"failure_stage: {payload['failure_stage']}")
    if payload.get("quick_verdict"):
        print(f"quick_verdict: {payload['quick_verdict']}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_ingest_payload(
        read_input_text(args.input),
        channel_dir=args.channel_dir,
        guild=args.guild,
        channel=args.channel,
        draft=args.draft,
        min_parsed=args.min_parsed,
    )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
