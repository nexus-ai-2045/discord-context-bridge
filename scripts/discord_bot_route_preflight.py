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

from discord_context_bridge import (  # noqa: E402
    configured_bot_token_provider,
    load_bot_token_from_provider,
)
from discord_context_bridge.cli import JapaneseArgumentParser  # noqa: E402
from discord_context_bridge.live_verification import (  # noqa: E402
    CHANNEL_TYPE_CLASS,
    LIVE_VERIFICATION_RECEIPT,
    MESSAGE_HISTORY_CHANNEL_TYPES,
    read_private_receipt,
    verify_saved_receipt,
)


DEFAULT_CHANNEL_DIR = Path.home() / ".claude" / "channels" / "discord"


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


def _receipt_status(status: str, *, verified: bool = False) -> dict[str, Any]:
    return {
        "schema": "discord_bot_live_verification_status.v1",
        "status": status,
        "live_verified": verified,
        "bot_identity_verified": verified,
        "target_guild_membership_verified": verified,
        "target_channel_access_verified": verified,
        "credential_bound": verified,
        "target_bound": verified,
        "receipt_path_output": "omitted",
        "identifiers_returned": False,
        "channel_type_class": "unverified",
        "message_history_supported": False,
    }


def read_live_verification_status(
    receipt_path: Path,
    *,
    channel_env_path: Path,
    expected_url: str | None,
) -> dict[str, Any]:
    """private receiptを現在credentialへ結び付けて検証する。"""

    if not expected_url:
        return _receipt_status("target_not_specified")
    payload, read_status = read_private_receipt(receipt_path)
    if payload is None:
        return _receipt_status(read_status)
    credential = load_bot_token_from_provider(channel_env_path=channel_env_path)
    if not credential.ok:
        return _receipt_status("credential_unavailable")
    status = verify_saved_receipt(
        payload=payload,
        expected_url=expected_url,
        token=credential.token,
    )
    result = _receipt_status(status, verified=status == "verified")
    if status == "verified":
        channel_type = payload["target_channel_type"]
        result["channel_type_class"] = CHANNEL_TYPE_CLASS[channel_type]
        result["message_history_supported"] = (
            channel_type in MESSAGE_HISTORY_CHANNEL_TYPES
        )
    return result


def build_preflight(
    channel_dir: Path, *, expected_url: str | None = None
) -> dict[str, Any]:
    provider_status = configured_bot_token_provider(
        channel_env_path=channel_dir / ".env"
    )
    access_status = read_access_status(channel_dir / "access.json")
    blockers: list[str] = []
    warnings: list[str] = []
    token_configured = bool(provider_status["token_set"])
    provider = str(provider_status["provider"])
    live_verification = read_live_verification_status(
        channel_dir / LIVE_VERIFICATION_RECEIPT,
        channel_env_path=channel_dir / ".env",
        expected_url=expected_url,
    )
    live_verified = bool(live_verification["live_verified"])

    if not token_configured:
        blockers.append(str(provider_status.get("failure_stage") or "bot_token_missing"))
    elif not live_verified:
        blockers.append("credential_configured_but_live_unverified")
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
        "credential_state": "configured" if token_configured else "not_configured",
        "live_verification": live_verification,
        "access": access_status,
        "blockers": blockers,
        "warnings": warnings,
        "next": (
            "bot_route_ready_for_private_smoke"
            if not blockers
            else (
                "obtain_explicit_target_live_verification_receipt"
                if token_configured and not live_verified
                else "run_discord_channel_pairing_or_lockdown"
            )
        ),
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
        default=Path(os.environ.get("DISCORD_CONTEXT_BRIDGE_CHANNEL_DIR", DEFAULT_CHANNEL_DIR)),
        help="Discord channel設定directory",
    )
    parser.add_argument(
        "--expected-url",
        help="照合するDiscord対象URL。未指定時はreadyになりません",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_preflight(args.channel_dir, expected_url=args.expected_url)
    print(_json(payload))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
