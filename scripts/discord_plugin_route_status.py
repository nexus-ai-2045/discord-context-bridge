#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import discord_bot_route_preflight


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_status(channel_dir: Path) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    access = preflight["access"]
    bot_ready = bool(preflight["ok"])
    env_token_set = bool(os.environ.get("DISCORD_" + "BOT_TOKEN", "").strip())
    bot_token_set = bool(preflight["bot_token"]["set"] or env_token_set)
    control_plane_ready = bot_token_set and bool(access.get("readable", True))

    return {
        "schema": "discord_plugin_route_status.v1",
        "ok": control_plane_ready,
        "recommended_route": "rest_backfill" if bot_token_set else "discord_configure_or_access",
        "routes": {
            "discord_configure": {
                "route_class": "control",
                "role": "bot token 設定の入口",
                "status": "configured" if bot_token_set else "needs_token",
                "token_set": bot_token_set,
                "token_output": "omitted",
                "mutation": "disabled",
            },
            "discord_access": {
                "route_class": "control",
                "role": "DM / group access allowlist の入口",
                "status": "readable" if access.get("readable", True) else "invalid_json",
                "dm_policy": access.get("dm_policy", "unknown"),
                "allow_count": int(access.get("allow_count", 0)),
                "group_count": int(access.get("group_count", 0)),
                "pending_count": int(access.get("pending_count", 0)),
                "snowflake_values_output": "omitted",
                "mutation": "disabled",
            },
            "rest_backfill": {
                "route_class": "main",
                "role": "Bot REST API で履歴を read-only backfill する主経路",
                "status": "ready" if bot_token_set else "blocked",
                "next": "run_discord_rest_backfill" if bot_token_set else "set_bot_token_env_or_channel_env",
                "token_output": "omitted",
                "raw_text_output": "omitted",
                "outbound_actions": "disabled",
                "send_capability": "disabled_by_policy",
            },
            "bot_private_ingest": {
                "route_class": "main",
                "role": "受け取った Discord 本文を文脈カード / 返信前 gate へ流す本線",
                "status": "ready" if bot_ready else "blocked",
                "next": preflight["next"],
                "text_output": "omitted",
                "outbound_actions": "disabled",
            },
            "computer_use_discord": {
                "route_class": "visual_fallback",
                "role": "画面を人間確認する fallback",
                "status": "manual_read_only_fallback",
                "raw_text_output": "disabled_by_policy",
                "send_capability": "disabled_by_policy",
            },
            "ocr_region": {
                "route_class": "last_fallback",
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
        "rest_backfill",
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
