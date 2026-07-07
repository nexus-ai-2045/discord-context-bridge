#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import discord_bot_private_ingest
import discord_bot_route_preflight
import discord_plugin_route_status


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def read_input_text(path: Path | None) -> str:
    return discord_bot_private_ingest.read_input_text(path)


def build_smoke_payload(
    text: str,
    *,
    channel_dir: Path,
    guild: str,
    channel: str,
    draft: str,
    min_parsed: int,
    understanding_confirmed: bool = False,
) -> dict[str, Any]:
    route_status = discord_plugin_route_status.build_status(channel_dir)
    ingest = discord_bot_private_ingest.build_ingest_payload(
        text,
        channel_dir=channel_dir,
        guild=guild,
        channel=channel,
        draft=draft,
        min_parsed=min_parsed,
        understanding_confirmed=understanding_confirmed,
    )
    main_route = route_status["routes"]["bot_private_ingest"]
    main_route_ready = (
        main_route.get("route_class") == "main"
        and main_route.get("status") == "ready"
    )
    ingest_ready = bool(ingest.get("ok")) and bool(ingest.get("context_ready"))
    ok = main_route_ready and ingest_ready
    return {
        "schema": "discord_main_route_smoke.v1",
        "ok": ok,
        "route": "bot_private_ingest",
        "route_class": "main",
        "route_ready": main_route_ready,
        "ingest_ready": ingest_ready,
        "parsed": int(ingest.get("parsed", 0)),
        "min_parsed": min_parsed,
        "context_ready": bool(ingest.get("context_ready", False)),
        "quick_verdict": ingest.get("quick_verdict"),
        "quick_verdict_label": ingest.get("quick_verdict_label"),
        "failure_stage": None if ok else failure_stage(route_status, ingest),
        "reason": None if ok else failure_reason(route_status, ingest),
        "text_output": "omitted",
        "text_saved": False,
        "outbound_actions": "disabled",
        "safety_boundary": {
            "token_output": "omitted",
            "snowflake_values_output": "omitted",
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "send_capability": "disabled",
        },
    }


def failure_stage(route_status: dict[str, Any], ingest: dict[str, Any]) -> str:
    main_route = route_status.get("routes", {}).get("bot_private_ingest", {})
    if main_route.get("status") != "ready":
        return "main_route_not_ready"
    if ingest.get("failure_stage"):
        return str(ingest["failure_stage"])
    if not ingest.get("context_ready"):
        return "context_not_ready"
    return "ingest_not_ready"


def failure_reason(route_status: dict[str, Any], ingest: dict[str, Any]) -> str:
    main_route = route_status.get("routes", {}).get("bot_private_ingest", {})
    if main_route.get("status") != "ready":
        return "control plane が未準備です。discord:configure / discord:access を確認してください。"
    if ingest.get("reason"):
        return str(ingest["reason"])
    if not ingest.get("context_ready"):
        return "private ingest は動きましたが、返信前 gate に使える文脈が不足しています。"
    return "private ingest smoke が成功条件を満たしていません。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="@discord main route を本文なしで smoke する。")
    parser.add_argument("--input", type=Path, help="bot channel server / private adapter からの一時テキスト。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--guild", default="discord-bot-route", help="実IDではなく安全な仮ラベル。")
    parser.add_argument("--channel", default="main-route-smoke", help="実IDではなく安全な仮ラベル。")
    parser.add_argument("--draft", default="前提を確認します。")
    parser.add_argument("--understanding-confirmed", action="store_true", help="理解サマリを人間確認済みとして返信reviewへ進む")
    parser.add_argument("--min-parsed", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord main route smoke")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"route: {payload['route']}")
    print(f"route_class: {payload['route_class']}")
    print(f"route_ready: {str(payload['route_ready']).lower()}")
    print(f"ingest_ready: {str(payload['ingest_ready']).lower()}")
    print(f"parsed: {payload['parsed']}")
    print(f"context_ready: {str(payload['context_ready']).lower()}")
    if payload.get("quick_verdict"):
        print(f"quick_verdict: {payload['quick_verdict']}")
    if payload.get("failure_stage"):
        print(f"failure_stage: {payload['failure_stage']}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_smoke_payload(
        read_input_text(args.input),
        channel_dir=args.channel_dir,
        guild=args.guild,
        channel=args.channel,
        draft=args.draft,
        min_parsed=args.min_parsed,
        understanding_confirmed=args.understanding_confirmed,
    )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
