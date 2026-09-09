#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import discord_bot_route_preflight


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_status(
    channel_dir: Path, *, expected_url: str | None = None
) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(
        channel_dir, expected_url=expected_url
    )
    access = preflight["access"]
    bot_ready = bool(preflight["ok"])
    bot_token_set = bool(preflight["bot_token"]["set"])
    live_verified = bool(preflight["live_verification"]["live_verified"])
    token_provider = str(preflight["bot_token"].get("provider") or "missing")
    control_plane_ready = bot_token_set and bool(access.get("readable", True))
    main_route_ready = control_plane_ready and live_verified
    history_ready = main_route_ready and bool(
        preflight["live_verification"]["message_history_supported"]
    )
    parent_inventory_required = main_route_ready and not history_ready

    return {
        "schema": "discord_plugin_route_status.v1",
        "ok": history_ready,
        "recommended_route": (
            "rest_backfill"
            if history_ready
            else (
                "thread_inventory"
                if parent_inventory_required
                else (
                "discord_verify_target_access"
                if control_plane_ready
                else "discord_configure_or_access"
                )
            )
        ),
        "configuration_state": "configured" if control_plane_ready else "not_ready",
        "live_state": "live_verified" if live_verified else "live_unverified",
        "routes": {
            "discord_configure": {
                "route_class": "control",
                "role": "bot token 設定の入口",
                "status": "configured" if bot_token_set else "needs_token",
                "token_set": bot_token_set,
                "provider": token_provider,
                "token_output": "omitted",
                "command_output": "omitted",
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
                "status": "ready" if history_ready else "blocked",
                "next": (
                    "run_discord_rest_backfill"
                    if history_ready
                    else (
                        "run_discord_archived_thread_inventory"
                        if parent_inventory_required
                        else "obtain_explicit_target_live_verification_receipt"
                        if bot_token_set else "set_bot_token_env_or_secret_command"
                    )
                ),
                "credential_configured": bot_token_set,
                "live_verified": live_verified,
                "channel_type_class": preflight["live_verification"]["channel_type_class"],
                "message_history_supported": history_ready,
                "provider": token_provider,
                "token_output": "omitted",
                "command_output": "omitted",
                "raw_text_output": "omitted",
                "outbound_actions": "disabled",
                "send_capability": "disabled_by_policy",
            },
            "thread_inventory": {
                "route_class": "main",
                "role": "forum / media 親からthreadを列挙する経路",
                "status": "required" if parent_inventory_required else "not_required",
                "outbound_actions": "disabled",
            },
            "bot_private_ingest": {
                "route_class": "main",
                "role": "受け取った Discord 本文を文脈カード / 返信前 gate へ流す本線",
                "status": "ready" if bot_ready else "blocked",
                "next": preflight["next"],
                "credential_configured": bot_token_set,
                "live_verified": live_verified,
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
    parser = argparse.ArgumentParser(description="@discord / Computer Use route の安全な状態を一覧する。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument(
        "--expected-url",
        help="照合するDiscord対象URL。未指定時は主経路をreadyにしません。",
    )
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
    ):
        route = routes[name]
        print(f"{name}: {route['status']}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_status(args.channel_dir, expected_url=args.expected_url)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
