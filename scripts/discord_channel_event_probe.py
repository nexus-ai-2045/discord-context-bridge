#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import discord_bot_route_preflight


TEXT_EVENT_SUFFIXES = {".jsonl", ".ndjson", ".txt"}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ogg", ".mp3", ".mp4", ".mov", ".wav"}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def count_files(root: Path, suffixes: set[str]) -> int:
    if not root.exists():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes)


def build_probe(channel_dir: Path) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    inbox_dir = channel_dir / "inbox"
    text_event_candidates = count_files(inbox_dir, TEXT_EVENT_SUFFIXES)
    media_inbox_count = count_files(inbox_dir, MEDIA_SUFFIXES)
    source_ready = bool(preflight["ok"]) and text_event_candidates > 0
    failure_stage = None if source_ready else ("control_plane_not_ready" if not preflight["ok"] else "no_text_event_source")
    reason = None
    if failure_stage == "control_plane_not_ready":
        reason = "control plane が未準備です。discord:configure / discord:access を確認してください。"
    elif failure_stage == "no_text_event_source":
        reason = "bot channel server / private adapter から渡せる text event がまだ見つかっていません。"
    return {
        "schema": "discord_channel_event_probe.v1",
        "ok": source_ready,
        "source_ready": source_ready,
        "route": "bot_private_ingest",
        "route_class": "main",
        "preflight_ok": bool(preflight["ok"]),
        "inbox_present": inbox_dir.exists(),
        "text_event_candidates": text_event_candidates,
        "media_inbox_count": media_inbox_count,
        "failure_stage": failure_stage,
        "reason": reason,
        "next": "pipe_text_event_to_discord_main_route_smoke" if source_ready else "wait_for_or_provide_private_text_event",
        "text_output": "omitted",
        "file_names_output": "omitted",
        "outbound_actions": "disabled",
        "safety_boundary": {
            "token_output": "omitted",
            "snowflake_values_output": "omitted",
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "media_file_names_output": "omitted",
            "send_capability": "disabled",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="@discord channel dir に text event が届いているか安全に確認する。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord channel event probe")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"source_ready: {str(payload['source_ready']).lower()}")
    print(f"preflight_ok: {str(payload['preflight_ok']).lower()}")
    print(f"text_event_candidates: {payload['text_event_candidates']}")
    print(f"media_inbox_count: {payload['media_inbox_count']}")
    if payload.get("failure_stage"):
        print(f"failure_stage: {payload['failure_stage']}")
    if payload.get("reason"):
        print(f"reason: {payload['reason']}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_probe(args.channel_dir)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
