#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from typing import Any


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def run_osascript(script: str, *, timeout: float = 5.0) -> tuple[bool, str]:
    if not command_available("osascript"):
        return False, ""
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    return completed.returncode == 0, completed.stdout.strip()


def discord_process_status(app_name: str) -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {
            "checked": False,
            "running": False,
            "window_count": 0,
            "reason": "macOS only",
        }

    ok, running = run_osascript(
        f'tell application "System Events" to return (exists process "{app_name}") as text'
    )
    if not ok:
        return {
            "checked": False,
            "running": False,
            "window_count": 0,
            "reason": "system_events_timeout" if running == "timeout" else "System Events check failed",
        }

    is_running = running.casefold() == "true"
    window_count = 0
    if is_running:
        ok, count_text = run_osascript(
            f'tell application "System Events" to tell process "{app_name}" to return (count of windows as text)'
        )
        if ok and count_text.isdigit():
            window_count = int(count_text)

    return {
        "checked": True,
        "running": is_running,
        "window_count": window_count,
        "reason": "ok" if is_running and window_count > 0 else "visible window not detected",
    }


def build_preflight(app_name: str) -> dict[str, Any]:
    dependencies = {
        "screencapture": command_available("screencapture"),
        "tesseract": command_available("tesseract"),
        "osascript": command_available("osascript"),
    }
    private_adapter_configured = bool(
        os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_ADAPTER")
        or os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND")
    )
    discord = discord_process_status(app_name)
    blockers: list[str] = []
    if platform.system() != "Darwin":
        blockers.append("unsupported_screen_state")
    if not dependencies["screencapture"] or not dependencies["tesseract"]:
        blockers.append("dependency_missing")
    if not private_adapter_configured:
        blockers.append("private_adapter_not_configured")
    if discord["checked"] and not discord["running"]:
        blockers.append("discord_not_running")
    elif discord["checked"] and discord["window_count"] == 0:
        blockers.append("discord_window_not_visible")

    e2e_ready = not blockers or blockers == ["discord_window_not_visible"]
    return {
        "schema": "discord_ops_preflight.v1",
        "ok": not blockers,
        "e2e_ready": e2e_ready,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
        },
        "dependencies": dependencies,
        "discord": discord,
        "private_adapter_configured": private_adapter_configured,
        "blockers": blockers,
        "next": "run_e2e_private_adapter_check" if e2e_ready else "fix_preflight_blockers",
        "safety_boundary": {
            "text_output": "omitted",
            "outbound_actions": "disabled",
            "raw_text_checked": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord bridge の実機前 preflight を本文なしで確認する。")
    parser.add_argument("--app-name", default="Discord")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_preflight(args.app_name)
    print(_json(payload))
    return 0 if payload["e2e_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
