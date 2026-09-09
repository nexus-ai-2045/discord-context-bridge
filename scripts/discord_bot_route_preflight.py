#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge import configured_bot_token_provider
from discord_context_bridge.cli import JapaneseArgumentParser
from discord_context_bridge.credentials import _channel_env_path

DEFAULT_CHANNEL_DIR = Path.home() / ".claude" / "channels" / "discord"


def default_channel_dir() -> Path:
    """credential loader と同じ環境変数・既定 path 解決を再利用する。"""
    return _channel_env_path(os.environ, None).parent


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def default_access() -> dict[str, Any]:
    return {
        "dmPolicy": "pairing",
        "allowFrom": [],
        "groups": {},
        "pending": {},
    }


def read_access_status(access_path: Path) -> dict[str, Any]:
    if not access_path.exists():
        data = default_access()
        exists = False
    else:
        try:
            data = json.loads(access_path.read_text(encoding="utf-8"))
            exists = True
        except json.JSONDecodeError:
            return {
                "exists": True,
                "readable": False,
                "dm_policy": "unknown",
                "allow_count": 0,
                "group_count": 0,
                "pending_count": 0,
            }
    return {
        "exists": exists,
        "readable": True,
        "dm_policy": str(data.get("dmPolicy") or "pairing"),
        "allow_count": len(list(data.get("allowFrom") or [])),
        "group_count": len(dict(data.get("groups") or {})),
        "pending_count": len(dict(data.get("pending") or {})),
    }


def build_preflight(channel_dir: Path) -> dict[str, Any]:
    provider_status = configured_bot_token_provider(
        channel_env_path=channel_dir / ".env"
    )
    access_status = read_access_status(channel_dir / "access.json")
    blockers: list[str] = []
    warnings: list[str] = []
    token_configured = bool(provider_status["token_set"])
    provider = str(provider_status["provider"])

    if not token_configured:
        blockers.append(str(provider_status.get("failure_stage") or "bot_token_missing"))
    if not access_status.get("readable", True):
        blockers.append("access_json_invalid")
    if access_status.get("dm_policy") == "pairing":
        warnings.append("pairing_policy_should_be_locked_after_setup")
    if access_status.get("allow_count", 0) == 0 and access_status.get("group_count", 0) == 0:
        warnings.append("no_allowed_sender_or_group")

    return {
        "schema": "discord_bot_route_preflight.v1",
        "ok": not blockers,
        "route": "discord_bot_channel",
        "bot_token": {
            "set": token_configured,
            "provider": provider,
            "value_returned": False,
            "token_output": "omitted",
            "command_output": "omitted",
        },
        "credential_provider": provider_status,
        "access": access_status,
        "blockers": blockers,
        "warnings": warnings,
        "next": "run_discord_channel_pairing_or_lockdown" if blockers else "bot_route_ready_for_private_smoke",
        "safety_boundary": {
            "token_output": "omitted",
            "snowflake_values_output": "omitted",
            "access_mutation": "disabled",
            "outbound_actions": "disabled",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = JapaneseArgumentParser(description="@discord bot routeの設定状態をsecretなしで確認します。")
    parser.add_argument(
        "--channel-dir",
        type=Path,
        default=default_channel_dir(),
        help="Discord channel設定directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_preflight(args.channel_dir)
    print(_json(payload))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
