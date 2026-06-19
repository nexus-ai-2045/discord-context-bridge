#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
import shlex
import subprocess
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

from discord_context_bridge import parse_visible_text
from read_visible_discord_text import audit_visible_text, normalize_visible_text, safe_probe_reason


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="private Discord visible-text adapter を本文なしで確認する probe"
    )
    parser.add_argument("--guild", default="private-discord", help="実IDではなく安全な仮ラベル")
    parser.add_argument("--channel", default="active-thread", help="実IDではなく安全な仮ラベル")
    parser.add_argument("--timeout", type=float, default=20.0, help="private command の最大秒数")
    parser.add_argument("--min-parsed", type=int, default=1, help="成功扱いに必要な最小解析件数")
    return parser


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def safe_stage(error_class: str) -> str:
    return {
        "adapter_not_configured": "dependency_missing",
        "adapter_config_error": "dependency_missing",
        "adapter_failed": "capture_failed",
        "adapter_timeout": "timeout",
        "ocr_empty": "ocr_empty",
        "parse_empty": "parse_empty",
        "min_parsed_failed": "min_parsed_failed",
    }.get(error_class, "capture_failed")


def safe_reason(error_class: str, detail: str = "") -> str:
    if detail and audit_visible_text(detail):
        return "安全監査により詳細を省略しました。"
    return {
        "adapter_not_configured": "private adapter が未設定です。",
        "adapter_config_error": "private adapter の設定形式が不正です。",
        "adapter_failed": "private adapter の実行に失敗しました。",
        "adapter_timeout": "private adapter が timeout しました。",
        "ocr_empty": "Discord 可視本文をまだ取得できていません。",
        "parse_empty": "Discord 可視本文を解析できませんでした。",
        "min_parsed_failed": "解析件数が成功条件に届いていません。",
    }.get(error_class, "private adapter を安全に確認できませんでした。")


def failure_payload(error_class: str, *, detail: str = "") -> dict[str, Any]:
    stage = safe_stage(error_class)
    return {
        "ok": False,
        "source_ready": False,
        "gate_verdict": "fail",
        "failure_stage": stage,
        "error_class": error_class,
        "reason": safe_reason(error_class, detail),
        "text_returned": False,
        "text_saved": False,
        "text_output": "omitted",
    }


def read_private_adapter(args: argparse.Namespace) -> tuple[str | None, dict[str, Any] | None]:
    adapter_spec = os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_ADAPTER", "").strip()
    command = os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND", "").strip()
    timeout = float(os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_TIMEOUT", str(args.timeout)))

    if adapter_spec:
        if ":" not in adapter_spec:
            return None, failure_payload("adapter_config_error")
        module_name, function_name = adapter_spec.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            adapter = getattr(module, function_name)
            value = adapter(args)
        except Exception as exc:
            return None, failure_payload("adapter_failed", detail=str(exc))
        if isinstance(value, dict):
            value = value.get("text", "")
        if not isinstance(value, str):
            return None, failure_payload("adapter_config_error")
        return value, None

    if command:
        try:
            completed = subprocess.run(
                shlex.split(command),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, failure_payload("adapter_timeout")
        except OSError as exc:
            return None, failure_payload("adapter_failed", detail=str(exc))
        if completed.returncode != 0:
            return None, failure_payload("adapter_failed", detail=safe_probe_reason(RuntimeError(completed.stderr)))
        return completed.stdout, None

    return None, failure_payload("adapter_not_configured")


def capture_payload(text: str, *, guild: str, channel: str, min_parsed: int) -> dict[str, Any]:
    normalized = normalize_visible_text(text)
    if not normalized.strip():
        return failure_payload("ocr_empty")
    events = parse_visible_text(normalized, guild_label=guild, channel_label=channel, source="private_adapter")
    if not events:
        payload = failure_payload("parse_empty")
    elif len(events) < min_parsed:
        payload = failure_payload("min_parsed_failed")
    else:
        payload = {
            "ok": True,
            "source_ready": True,
            "gate_verdict": "pass",
            "text_returned": False,
            "text_saved": False,
            "text_output": "omitted",
        }
    payload.update(
        {
            "char_count": len(normalized),
            "line_count": len([line for line in normalized.splitlines() if line.strip()]),
            "parsed_event_count": len(events),
            "author_count": len({event.author_label for event in events}),
        }
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text, failure = read_private_adapter(args)
    capture = failure or capture_payload(text or "", guild=args.guild, channel=args.channel, min_parsed=args.min_parsed)
    payload = {
        "schema": "discord_private_adapter_probe.v1",
        "ok": bool(capture["ok"]),
        "adapter_configured": capture.get("error_class") != "adapter_not_configured",
        "capture": capture,
        "text_output": "omitted",
        "outbound_actions": "disabled",
    }
    print(json_dumps(payload))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
