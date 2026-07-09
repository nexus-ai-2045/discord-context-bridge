#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import codex_discord_ingress_smoke
import discord_route_retry_decider


DEFAULT_SNAPSHOT_ROOT = Path(
    os.environ.get(
        "DISCORD_CONTEXT_BRIDGE_SNAPSHOT_ROOT",
        str(Path.home() / "Projects" / "Documents" / "discord" / "raw-snapshots" / "discord"),
    )
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _discord_path_parts(url: str) -> list[str]:
    parsed = urlparse(url)
    return [part for part in parsed.path.split("/") if part]


def _target_snapshot_dir(url: str, snapshot_root: Path) -> Path | None:
    parts = _discord_path_parts(url)
    if len(parts) < 3 or parts[0] != "channels":
        return None
    guild, channel = parts[1], parts[2]
    if guild == "@me":
        return snapshot_root / "dms" / channel
    return snapshot_root / "servers" / guild / "channels" / channel


def build_cache_probe(url: str, snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT) -> dict[str, Any]:
    target_dir = _target_snapshot_dir(url, snapshot_root)
    target_present = bool(target_dir and target_dir.exists())
    snapshot_dirs_count = 0
    text_like_file_count = 0
    if target_present and target_dir:
        snapshot_dirs_count = sum(1 for path in target_dir.iterdir() if path.is_dir())
        text_like_file_count = sum(
            1
            for path in target_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".json", ".jsonl", ".ndjson", ".txt"}
        )
    return {
        "ok": target_present,
        "target_cache_present": target_present,
        "snapshot_dirs_count": snapshot_dirs_count,
        "text_like_file_count": text_like_file_count,
        "snapshot_root_configured": snapshot_root.exists(),
        "path_output": "omitted",
        "file_names_output": "omitted",
        "raw_identifiers_output": "omitted",
        "text_output": "omitted",
    }


def build_measurement(
    *,
    url: str,
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
    chrome_opened: bool = False,
    current_title: str = "Discord",
    human_state: str = "ready",
    confirm_decision: str = "pending",
    channel_dir: Path = discord_route_retry_decider.discord_bot_route_preflight.DEFAULT_CHANNEL_DIR,
    gateway_command: str | None = None,
    rest_command: str | None = None,
    attempts: int = 1,
    timeout: float = 5.0,
    interval: float = 0.2,
) -> dict[str, Any]:
    ingress = codex_discord_ingress_smoke.build_ingress_payload(
        chrome_opened=chrome_opened,
        current_url=url,
        current_title=current_title,
        human_state=human_state,
        confirm_decision=confirm_decision,
    )
    route = discord_route_retry_decider.build_decision(
        channel_dir=channel_dir,
        gateway_command=gateway_command,
        rest_command=rest_command,
        attempts=attempts,
        timeout=timeout,
        interval=interval,
    )
    cache = build_cache_probe(url, snapshot_root)
    next_action = "use_local_cache_metadata" if cache["ok"] else route.get("decision")
    ok = bool(ingress["steps"][3]["ok"]) and (cache["ok"] or route["ok"])
    return {
        "schema": "discord_url_measure.v1",
        "ok": ok,
        "ingress": {
            "ok": ingress["ok"],
            "stage": ingress["stage"],
            "blocked_stage": ingress.get("blocked_stage"),
            "has_channel_route": ingress["steps"][3]["has_channel_route"],
            "has_thread_like_route": ingress["steps"][3]["has_thread_like_route"],
            "raw_url_output": "omitted",
            "raw_title_output": "omitted",
            "raw_identifiers_output": "omitted",
        },
        "cache": cache,
        "route": {
            "ok": route["ok"],
            "decision": route["decision"],
            "selected_route": route["selected_route"],
            "chrome_fallback": route["chrome_fallback"],
            "raw_discord_text_output": "omitted",
        },
        "next_action": next_action,
        "safety_boundary": {
            "raw_url_output": "omitted",
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "snowflake_values_output": "omitted",
            "path_output": "omitted",
            "outbound_actions": "disabled",
            "browser_auto_fallback": "disabled",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord URL を本文なしで実測し、cache / route / 次アクションを返す。")
    parser.add_argument("--url", required=True, help="測定対象URL。出力には表示しない")
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--chrome-opened", action="store_true")
    parser.add_argument("--current-title", default="Discord", help="現在タブtitle。出力には表示しない")
    parser.add_argument("--human-state", choices=["navigating", "ready"], default="ready")
    parser.add_argument("--confirm-decision", choices=["pending", "approved", "rejected"], default="pending")
    parser.add_argument("--channel-dir", type=Path, default=discord_route_retry_decider.discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--gateway-command", default=os.environ.get("DISCORD_CONTEXT_BRIDGE_GATEWAY_COMMAND"))
    parser.add_argument("--rest-command", default=os.environ.get("DISCORD_CONTEXT_BRIDGE_REST_COMMAND"))
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord URL measure")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"ingress_stage: {payload['ingress']['stage']}")
    if payload["ingress"].get("blocked_stage"):
        print(f"ingress_blocked_stage: {payload['ingress']['blocked_stage']}")
    print(f"cache_present: {str(payload['cache']['target_cache_present']).lower()}")
    print(f"snapshot_dirs_count: {payload['cache']['snapshot_dirs_count']}")
    print(f"route_decision: {payload['route']['decision']}")
    print(f"next_action: {payload['next_action']}")
    print("raw_url_output: omitted")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_measurement(
        url=args.url,
        snapshot_root=args.snapshot_root,
        chrome_opened=args.chrome_opened,
        current_title=args.current_title,
        human_state=args.human_state,
        confirm_decision=args.confirm_decision,
        channel_dir=args.channel_dir,
        gateway_command=args.gateway_command,
        rest_command=args.rest_command,
        attempts=args.attempts,
        timeout=args.timeout,
        interval=args.interval,
    )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
