#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge import load_bot_token_from_provider  # noqa: E402
from discord_context_bridge.cli import JapaneseArgumentParser  # noqa: E402
from discord_context_bridge.live_verification import (  # noqa: E402
    LIVE_VERIFICATION_RECEIPT,
    LiveVerificationError,
    produce_live_verification_receipt,
)

import discord_bot_route_preflight  # noqa: E402


def _public(status: str, reason: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "discord_bot_live_verify.v1",
        "ok": status == "live_verified",
        "status": status,
        "bot_identity_verified": status == "live_verified",
        "target_guild_membership_verified": status == "live_verified",
        "target_channel_access_verified": status == "live_verified",
        "raw_text_returned": False,
        "identifiers_returned": False,
        "url_output": "omitted",
        "token_output": "omitted",
        "digest_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
        "request_method": "GET_only",
    }
    if reason:
        payload["failure_stage"] = reason
        payload["blockers"] = [reason]
    else:
        payload["blockers"] = []
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = JapaneseArgumentParser(
        description="Bot本人・対象guild所属・対象channel読取をGETだけで実測します"
    )
    parser.add_argument("--expected-url", required=True, help="照合するDiscord対象URL")
    parser.add_argument(
        "--channel-dir",
        type=Path,
        default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR,
        help="private設定directory",
    )
    parser.add_argument("--json", action="store_true", help="安全なmetadataだけをJSON出力")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    credential = load_bot_token_from_provider(channel_env_path=args.channel_dir / ".env")
    if not credential.ok:
        report = _public("blocked", credential.failure_stage or "bot_token_missing")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 2
    try:
        produce_live_verification_receipt(
            expected_url=args.expected_url,
            token=credential.token,
            path=args.channel_dir / LIVE_VERIFICATION_RECEIPT,
        )
    except LiveVerificationError as error:
        report = _public("blocked", str(error))
    except OSError:
        report = _public("blocked", "live_verification_receipt_write_failed")
    else:
        report = _public("live_verified")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
