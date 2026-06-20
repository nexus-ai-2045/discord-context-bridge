#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import discord_bot_private_ingest
import discord_bot_route_preflight
import discord_channel_event_probe
import discord_live_text_source
import discord_main_route_smoke
import discord_plugin_route_status


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def read_input_text(path: Path | None) -> str:
    return discord_bot_private_ingest.read_input_text(path)


def resolve_source_text(
    *,
    input_path: Path | None,
    channel_dir: Path,
    source_command: str | None,
    source_timeout: float,
    source_label: str,
    use_channel_event: bool,
) -> tuple[str, dict[str, Any]]:
    if source_command:
        write_payload = discord_live_text_source.write_source_command_event(
            source_command,
            channel_dir=channel_dir,
            source_label=source_label,
            source_timeout=source_timeout,
        )
        if not write_payload["ok"]:
            return "", {
                "mode": "source_command_to_channel_event",
                "write": write_payload,
                "latest": None,
            }
        text, latest_payload = discord_live_text_source.latest_text_event(channel_dir)
        return text, {
            "mode": "source_command_to_channel_event",
            "write": write_payload,
            "latest": latest_payload,
        }
    if use_channel_event:
        text, latest_payload = discord_live_text_source.latest_text_event(channel_dir)
        return text, {
            "mode": "channel_event",
            "write": None,
            "latest": latest_payload,
        }
    return read_input_text(input_path), {
        "mode": "input",
        "write": None,
        "latest": None,
    }


def build_e2e_payload(
    text: str,
    *,
    channel_dir: Path,
    guild: str,
    channel: str,
    draft: str,
    min_parsed: int,
    require_channel_event: bool,
    source_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_status = discord_plugin_route_status.build_status(channel_dir)
    main_smoke = discord_main_route_smoke.build_smoke_payload(
        text,
        channel_dir=channel_dir,
        guild=guild,
        channel=channel,
        draft=draft,
        min_parsed=min_parsed,
    )
    channel_probe = discord_channel_event_probe.build_probe(channel_dir)
    fixture_path_ready = bool(main_smoke["ok"])
    channel_event_ready = bool(channel_probe["ok"])
    source_ready = source_payload is None or source_payload_is_ready(source_payload)
    ok = source_ready and fixture_path_ready and (channel_event_ready or not require_channel_event)
    blocked_stage = None if ok else first_blocked_stage(
        main_smoke,
        channel_probe,
        require_channel_event,
        source_payload,
    )
    return {
        "schema": "discord_route_e2e_check.v1",
        "ok": ok,
        "stage": "done" if ok else "blocked",
        "route": "bot_private_ingest",
        "route_class": "main",
        "recommended_route": route_status.get("recommended_route"),
        "checks": {
            "route_status_ok": bool(route_status.get("ok")),
            "main_route_smoke_ok": fixture_path_ready,
            "channel_event_source_ready": channel_event_ready,
            "channel_event_required": require_channel_event,
            "source_ready": source_ready,
        },
        "source": source_payload or {"mode": "input"},
        "main_route": {
            "route_ready": bool(main_smoke.get("route_ready")),
            "ingest_ready": bool(main_smoke.get("ingest_ready")),
            "parsed": int(main_smoke.get("parsed", 0)),
            "context_ready": bool(main_smoke.get("context_ready")),
            "quick_verdict": main_smoke.get("quick_verdict"),
        },
        "channel_event": {
            "preflight_ok": bool(channel_probe.get("preflight_ok")),
            "text_event_candidates": int(channel_probe.get("text_event_candidates", 0)),
            "media_inbox_count": int(channel_probe.get("media_inbox_count", 0)),
            "failure_stage": channel_probe.get("failure_stage"),
            "reason": channel_probe.get("reason"),
        },
        "blocked_stage": blocked_stage,
        "reason": None if ok else blocked_reason(main_smoke, channel_probe, require_channel_event, source_payload),
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


def source_payload_is_ready(source_payload: dict[str, Any]) -> bool:
    if source_payload.get("mode") == "input":
        return True
    write_payload = source_payload.get("write")
    latest_payload = source_payload.get("latest")
    if write_payload is not None and not write_payload.get("ok"):
        return False
    if latest_payload is not None and not latest_payload.get("ok"):
        return False
    return True


def first_blocked_stage(
    main_smoke: dict[str, Any],
    channel_probe: dict[str, Any],
    require_channel_event: bool,
    source_payload: dict[str, Any] | None = None,
) -> str:
    if source_payload is not None and not source_payload_is_ready(source_payload):
        write_payload = source_payload.get("write")
        latest_payload = source_payload.get("latest")
        failed = write_payload if write_payload is not None and not write_payload.get("ok") else latest_payload
        if failed:
            return str(failed.get("failure_stage") or "source_not_ready")
        return "source_not_ready"
    if not main_smoke.get("ok"):
        return str(main_smoke.get("failure_stage") or "main_route_smoke_failed")
    if require_channel_event and not channel_probe.get("ok"):
        return str(channel_probe.get("failure_stage") or "channel_event_not_ready")
    return "unknown"


def blocked_reason(
    main_smoke: dict[str, Any],
    channel_probe: dict[str, Any],
    require_channel_event: bool,
    source_payload: dict[str, Any] | None = None,
) -> str:
    if source_payload is not None and not source_payload_is_ready(source_payload):
        write_payload = source_payload.get("write")
        latest_payload = source_payload.get("latest")
        failed = write_payload if write_payload is not None and not write_payload.get("ok") else latest_payload
        if failed:
            return str(failed.get("reason") or "text event source が未準備です。")
        return "text event source が未準備です。"
    if not main_smoke.get("ok"):
        return str(main_smoke.get("reason") or "main route smoke が成功条件を満たしていません。")
    if require_channel_event and not channel_probe.get("ok"):
        return str(channel_probe.get("reason") or "text event source が未準備です。")
    return "E2E check が成功条件を満たしていません。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord main route の E2E 状態を本文なしで確認する。")
    parser.add_argument("--input", type=Path, help="fixture または private adapter からの一時テキスト。")
    parser.add_argument("--source-command", help="Discord 可視テキストを stdout に出すローカルコマンド。")
    parser.add_argument("--source-timeout", type=float, default=20.0)
    parser.add_argument("--source-label", default="e2e-source-command", help="実IDではなく安全な仮ラベル。")
    parser.add_argument("--use-channel-event", action="store_true", help="channel inbox の latest text event を入力として使う。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--guild", default="discord-bot-route", help="実IDではなく安全な仮ラベル。")
    parser.add_argument("--channel", default="e2e-route-check", help="実IDではなく安全な仮ラベル。")
    parser.add_argument("--draft", default="前提を確認します。")
    parser.add_argument("--min-parsed", type=int, default=1)
    parser.add_argument("--require-channel-event", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord route E2E check")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"stage: {payload['stage']}")
    print(f"recommended_route: {payload['recommended_route']}")
    print(f"main_route_smoke_ok: {str(payload['checks']['main_route_smoke_ok']).lower()}")
    print(f"channel_event_source_ready: {str(payload['checks']['channel_event_source_ready']).lower()}")
    if payload.get("blocked_stage"):
        print(f"blocked_stage: {payload['blocked_stage']}")
    if payload.get("reason"):
        print(f"reason: {payload['reason']}")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_text, source_payload = resolve_source_text(
        input_path=args.input,
        channel_dir=args.channel_dir,
        source_command=args.source_command,
        source_timeout=args.source_timeout,
        source_label=args.source_label,
        use_channel_event=args.use_channel_event,
    )
    payload = build_e2e_payload(
        source_text,
        channel_dir=args.channel_dir,
        guild=args.guild,
        channel=args.channel,
        draft=args.draft,
        min_parsed=args.min_parsed,
        require_channel_event=args.require_channel_event,
        source_payload=source_payload,
    )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
