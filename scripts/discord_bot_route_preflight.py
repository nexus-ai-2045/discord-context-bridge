#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CHANNEL_DIR = Path.home() / ".claude" / "channels" / "discord"


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def read_env_status(env_path: Path) -> dict[str, Any]:
    if not env_path.exists():
        return {"exists": False, "token_set": False}
    token_key = "DISCORD_" + "BOT_TOKEN"
    token_set = False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(token_key + "=") and line.split("=", 1)[1].strip():
            token_set = True
            break
    return {"exists": True, "token_set": token_set}


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
    env_status = read_env_status(channel_dir / ".env")
    access_status = read_access_status(channel_dir / "access.json")
    blockers: list[str] = []
    warnings: list[str] = []

    if not env_status["token_set"]:
        blockers.append("bot_token_missing")
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
            "set": env_status["token_set"],
            "value_returned": False,
        },
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
    parser = argparse.ArgumentParser(description="@discord bot route の設定状態を secret なしで確認する。")
    parser.add_argument(
        "--channel-dir",
        type=Path,
        default=Path(os.environ.get("DISCORD_CONTEXT_BRIDGE_CHANNEL_DIR", DEFAULT_CHANNEL_DIR)),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_preflight(args.channel_dir)
    print(_json(payload))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
