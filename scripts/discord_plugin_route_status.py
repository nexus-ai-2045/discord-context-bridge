#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import discord_bot_route_preflight


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_status(channel_dir: Path) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    access = preflight["access"]
    bot_ready = bool(preflight["ok"])
    control_plane_ready = bool(preflight["bot_token"]["set"]) and bool(access.get("readable", True))

    return {
        "schema": "discord_plugin_route_status.v1",
        "ok": control_plane_ready,
        "recommended_route": "bot_private_ingest" if bot_ready else "discord_configure_or_access",
        "routes": {
            "discord_configure": {
                "role": "bot token 設定の入口",
                "status": "configured" if preflight["bot_token"]["set"] else "needs_token",
                "token_set": bool(preflight["bot_token"]["set"]),
                "token_output": "omitted",
                "mutation": "disabled",
            },
            "discord_access": {
                "role": "DM / group access allowlist の入口",
                "status": "readable" if access.get("readable", True) else "invalid_json",
                "dm_policy": access.get("dm_policy", "unknown"),
                "allow_count": int(access.get("allow_count", 0)),
                "group_count": int(access.get("group_count", 0)),
                "pending_count": int(access.get("pending_count", 0)),
                "snowflake_values_output": "omitted",
                "mutation": "disabled",
            },
            "bot_private_ingest": {
                "role": "受け取った Discord 本文を文脈カード / 返信前 gate へ流す本線",
                "status": "ready" if bot_ready else "blocked",
                "next": preflight["next"],
                "text_output": "omitted",
                "outbound_actions": "disabled",
            },
            "computer_use_discord": {
                "role": "画面を人間確認する fallback",
                "status": "manual_read_only_fallback",
                "raw_text_output": "disabled_by_policy",
                "send_capability": "disabled_by_policy",
            },
            "ocr_region": {
                "role": "bot route が使えない時の pixel fallback",
                "status": "fallback",
                "requires": "explicit_region",
                "full_screen_capture": "disabled_by_policy",
                "text_output": "omitted",
            },
        },
        "blockers": preflight["blockers"],
        "warnings": preflight["warnings"],
        "stoplines": [
            "Discord send / reaction / delete はしない",
            "token / cookie / webhook / browser profile を出力しない",
            "raw Discord 本文 / 参加者名 / snowflake 値を出力しない",
            "access.json の変更は明示コマンドなしではしない",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="@discord / Computer Use / OCR route の安全な状態を一覧する。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--json", action="store_true", help="JSON で出力する。")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    routes = payload["routes"]
    print("Discord plugin route status")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"recommended_route: {payload['recommended_route']}")
    for name in (
        "discord_configure",
        "discord_access",
        "bot_private_ingest",
        "computer_use_discord",
        "ocr_region",
    ):
        route = routes[name]
        print(f"{name}: {route['status']}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_status(args.channel_dir)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
