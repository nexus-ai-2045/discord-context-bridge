#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import discord_bot_private_ingest
import discord_bot_route_preflight
import discord_channel_event_probe
from discord_context_bridge.cli import read_command_text, safe_command_failure_reason


SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def public_source_error(exc: BaseException) -> str:
    return safe_command_failure_reason(str(exc))


def safe_label(value: str) -> str:
    label = SAFE_LABEL_RE.sub("-", value.strip())[:40].strip(".-")
    return label or "discord-source"


def inbox_dir(channel_dir: Path) -> Path:
    return channel_dir / "inbox"


def text_event_files(channel_dir: Path) -> list[Path]:
    inbox = inbox_dir(channel_dir)
    if not inbox.exists():
        return []
    return sorted(
        (
            path
            for path in inbox.rglob("*")
            if path.is_file() and path.suffix.lower() in discord_channel_event_probe.TEXT_EVENT_SUFFIXES
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )


def event_metadata(text: str) -> dict[str, Any]:
    return {
        "chars": len(text),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
    }


def write_text_event(
    text: str,
    *,
    channel_dir: Path,
    source_label: str = "source-command",
) -> dict[str, Any]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    if not text.strip():
        return {
            "schema": "discord_live_text_source.v1",
            "ok": False,
            "stage": "input",
            "route": "bot_private_ingest",
            "route_class": "main",
            "preflight_ok": bool(preflight["ok"]),
            "failure_stage": "source_empty",
            "reason": "private adapter から渡せる text event が空です。",
            "written": False,
            "text_output": "omitted",
            "file_names_output": "omitted",
            "outbound_actions": "disabled",
        }

    inbox = inbox_dir(channel_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    event_name = f"{int(time.time() * 1000)}-{safe_label(source_label)}-{digest}.txt"
    (inbox / event_name).write_text(text, encoding="utf-8")
    probe = discord_channel_event_probe.build_probe(channel_dir)
    ok = bool(preflight["ok"]) and bool(probe["ok"])
    return {
        "schema": "discord_live_text_source.v1",
        "ok": ok,
        "stage": "done" if ok else "control_plane",
        "route": "bot_private_ingest",
        "route_class": "main",
        "preflight_ok": bool(preflight["ok"]),
        "source_ready": bool(probe["ok"]),
        "text_event_candidates": int(probe.get("text_event_candidates", 0)),
        "media_inbox_count": int(probe.get("media_inbox_count", 0)),
        "written": True,
        "source_label": safe_label(source_label),
        "event": event_metadata(text),
        "failure_stage": None if ok else probe.get("failure_stage"),
        "reason": None if ok else probe.get("reason"),
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


def latest_text_event(channel_dir: Path) -> tuple[str, dict[str, Any]]:
    preflight = discord_bot_route_preflight.build_preflight(channel_dir)
    files = text_event_files(channel_dir)
    if not files:
        return "", {
            "schema": "discord_live_text_source_latest.v1",
            "ok": False,
            "stage": "input",
            "route": "bot_private_ingest",
            "route_class": "main",
            "preflight_ok": bool(preflight["ok"]),
            "source_ready": False,
            "text_event_candidates": 0,
            "failure_stage": "no_text_event_source",
            "reason": "bot channel server / private adapter から渡せる text event がまだ見つかっていません。",
            "text_output": "omitted",
            "file_names_output": "omitted",
            "outbound_actions": "disabled",
        }
    text = files[0].read_text(encoding="utf-8")
    if not text.strip():
        return "", {
            "schema": "discord_live_text_source_latest.v1",
            "ok": False,
            "stage": "input",
            "route": "bot_private_ingest",
            "route_class": "main",
            "preflight_ok": bool(preflight["ok"]),
            "source_ready": False,
            "text_event_candidates": len(files),
            "failure_stage": "source_empty",
            "reason": "latest text event が空です。",
            "text_output": "omitted",
            "file_names_output": "omitted",
            "outbound_actions": "disabled",
        }
    probe = discord_channel_event_probe.build_probe(channel_dir)
    ok = bool(preflight["ok"]) and bool(probe["ok"])
    return text, {
        "schema": "discord_live_text_source_latest.v1",
        "ok": ok,
        "stage": "done" if ok else "control_plane",
        "route": "bot_private_ingest",
        "route_class": "main",
        "preflight_ok": bool(preflight["ok"]),
        "source_ready": bool(probe["ok"]),
        "text_event_candidates": int(probe.get("text_event_candidates", len(files))),
        "media_inbox_count": int(probe.get("media_inbox_count", 0)),
        "event": event_metadata(text),
        "failure_stage": None if ok else probe.get("failure_stage"),
        "reason": None if ok else probe.get("reason"),
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


def write_source_command_event(
    command: str,
    *,
    channel_dir: Path,
    source_label: str,
    source_timeout: float,
) -> dict[str, Any]:
    try:
        text = read_command_text(
            command,
            empty_message="source command の出力が空です。",
            timeout=source_timeout,
        )
    except SystemExit as exc:
        return {
            "schema": "discord_live_text_source.v1",
            "ok": False,
            "stage": "source_command",
            "route": "bot_private_ingest",
            "route_class": "main",
            "failure_stage": "source_command_failed",
            "reason": public_source_error(exc),
            "written": False,
            "text_output": "omitted",
            "file_names_output": "omitted",
            "outbound_actions": "disabled",
        }
    return write_text_event(text, channel_dir=channel_dir, source_label=source_label)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord private adapter の本文を安全に inbox text event へ渡す。")
    parser.add_argument("--input", type=Path, help="private adapter が保存した一時テキスト。")
    parser.add_argument("--source-command", help="Discord 可視テキストを stdout に出すローカルコマンド。")
    parser.add_argument("--source-timeout", type=float, default=20.0)
    parser.add_argument("--source-label", default="source-command", help="実IDではなく安全な仮ラベル。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--read-latest", action="store_true", help="保存済み latest text event のメタデータだけ確認する。")
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord live text source")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"stage: {payload['stage']}")
    if "text_event_candidates" in payload:
        print(f"text_event_candidates: {payload['text_event_candidates']}")
    if payload.get("failure_stage"):
        print(f"failure_stage: {payload['failure_stage']}")
    if payload.get("reason"):
        print(f"reason: {payload['reason']}")
    print("text_output: omitted")
    print("file_names_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.read_latest:
        _, payload = latest_text_event(args.channel_dir)
    elif args.source_command:
        payload = write_source_command_event(
            args.source_command,
            channel_dir=args.channel_dir,
            source_label=args.source_label,
            source_timeout=args.source_timeout,
        )
    else:
        payload = write_text_event(
            discord_bot_private_ingest.read_input_text(args.input),
            channel_dir=args.channel_dir,
            source_label=args.source_label,
        )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
