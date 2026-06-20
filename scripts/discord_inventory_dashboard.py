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
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import discord_bot_route_preflight
from discord_context_bridge.core import audit_event_store, load_events
from route_timing_log import read_entries, summarize_entries


TEXT_EVENT_SUFFIXES = {".jsonl", ".ndjson", ".txt"}
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ogg", ".mp3", ".mp4", ".mov", ".wav"}


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def count_suffixes(root: Path, suffixes: set[str]) -> dict[str, int]:
    counts = {suffix: 0 for suffix in sorted(suffixes)}
    if not root.exists():
        return counts
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in counts:
            counts[path.suffix.lower()] += 1
    return counts


def compact_nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {suffix: count for suffix, count in counts.items() if count}


def inbox_inventory(channel_dir: Path) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    inbox_dir = channel_dir / "inbox"
    text_counts = compact_nonzero(count_suffixes(inbox_dir, TEXT_EVENT_SUFFIXES))
    media_counts = compact_nonzero(count_suffixes(inbox_dir, MEDIA_SUFFIXES))
    text_event_candidates = sum(text_counts.values())
    media_inbox_count = sum(media_counts.values())
    return {
        "inbox_present": inbox_dir.exists(),
        "preflight_ok": bool(preflight["ok"]),
        "text_event_candidates": text_event_candidates,
        "text_event_suffix_counts": text_counts,
        "media_inbox_count": media_inbox_count,
        "media_suffix_counts": media_counts,
        "file_names_output": "omitted",
        "text_output": "omitted",
        "next": "pipe_text_event_to_main_route" if text_event_candidates else "wait_for_or_provide_private_text_event",
    }


def store_inventory(tmp_dir: Path, *, limit: int) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    for path in sorted(tmp_dir.glob("dcb-*.ndjson"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            events = load_events(path)
            audit = audit_event_store(path)
        except Exception as exc:
            stores.append(
                {
                    "name": path.name,
                    "readable": False,
                    "reason": type(exc).__name__,
                    "text_output": "omitted",
                }
            )
            continue
        channels = sorted({event.channel_label for event in events})
        guilds = sorted({event.guild_label for event in events})
        stores.append(
            {
                "name": path.name,
                "readable": True,
                "event_count": len(events),
                "guild_count": len(guilds),
                "channel_count": len(channels),
                "channel_labels_sample": channels[:3],
                "safe_for_tunnel": bool(audit.get("safe_for_tunnel")),
                "issue_count": int(audit.get("issue_count", 0)),
                "text_output": "omitted",
                "participant_names_output": "omitted",
            }
        )
        if len(stores) >= limit:
            break
    return stores


def timing_inventory(tmp_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for path in sorted(tmp_dir.glob("dcb-route-timing*.jsonl")):
        summary = summarize_entries(read_entries(path))
        summaries.append(
            {
                "name": path.name,
                "entry_count": summary["entry_count"],
                "routes": summary["routes"],
                "text_output": "omitted",
            }
        )
    return summaries


def build_inventory(channel_dir: Path, tmp_dir: Path, *, store_limit: int) -> dict[str, Any]:
    stores = store_inventory(tmp_dir, limit=store_limit)
    unsafe_stores = [store["name"] for store in stores if store.get("readable") and not store.get("safe_for_tunnel")]
    return {
        "schema": "discord_inventory_dashboard.v1",
        "ok": True,
        "channel_dir": {
            "configured": channel_dir.exists(),
            "path_output": "omitted",
        },
        "inbox": inbox_inventory(channel_dir),
        "stores": {
            "tmp_dir": "omitted",
            "shown_count": len(stores),
            "unsafe_store_count": len(unsafe_stores),
            "unsafe_store_names": unsafe_stores[:10],
            "items": stores,
        },
        "timing_logs": timing_inventory(tmp_dir),
        "safety_boundary": {
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "token_output": "omitted",
            "snowflake_values_output": "omitted",
            "inbox_file_names_output": "omitted",
            "outbound_actions": "disabled",
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord 受信箱・既取得store・速度ログを本文なしで棚卸しする。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--tmp-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--store-limit", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    inbox = payload["inbox"]
    stores = payload["stores"]
    print("Discord inventory dashboard")
    print(f"text_event_candidates: {inbox['text_event_candidates']}")
    print(f"media_inbox_count: {inbox['media_inbox_count']}")
    print(f"stores_shown: {stores['shown_count']}")
    print(f"unsafe_store_count: {stores['unsafe_store_count']}")
    print(f"timing_logs: {len(payload['timing_logs'])}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_inventory(args.channel_dir, args.tmp_dir, store_limit=args.store_limit)
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
