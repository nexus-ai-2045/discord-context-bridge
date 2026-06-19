#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OCR_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_+-]+$")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def public_safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("omitted" if key == "store" else public_safe_payload(child))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [public_safe_payload(item) for item in value]
    return value


def build_capture_source_command(*, capture_profile: str, capture_region: str, ocr_language: str, capture_timeout: float) -> str:
    if capture_profile != "macos-screencapture-region":
        raise ValueError("unsupported_capture_profile")
    if not capture_region:
        raise ValueError("capture_region_required")
    if not OCR_LANGUAGE_RE.fullmatch(ocr_language):
        raise ValueError("invalid_ocr_language")
    ocr_command = f"tesseract {{image}} stdout -l {ocr_language}"
    return " ".join(
        [
            shlex.quote(sys.executable),
            "scripts/read_screenshot_ocr_text.py",
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            shlex.quote(capture_region),
            "--ocr-command",
            shlex.quote(ocr_command),
            "--timeout",
            shlex.quote(str(capture_timeout)),
        ]
    )


def run_script(script_name: str, args: list[str], *, timeout: float) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT / "scripts"), env.get("PYTHONPATH", "")])
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script_name), *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        stage = "ops_check_timeout" if script_name == "ops_check.py" else "script_timeout"
        return {
            "returncode": 124,
            "payload": {
                "ok": False,
                "issue_count": 1,
                "failure_stage": "timeout",
                "reason": stage,
                "text_output": "omitted",
                "outbound_actions": "disabled",
            },
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        if script_name == "ops_check.py":
            payload = {
                "ok": completed.returncode == 0,
                "issue_count": 0 if completed.returncode == 0 else 1,
                "failure_stage": None if completed.returncode == 0 else "ops_check_failed",
                "reason": "ops_check_completed" if completed.returncode == 0 else "ops_check_failed",
                "text_output": "omitted",
                "outbound_actions": "disabled",
            }
        else:
            payload = {
                "ok": False,
                "failure_stage": "capture_failed",
                "reason": "script did not return valid JSON",
                "text_output": "omitted",
                "outbound_actions": "disabled",
            }
    return {"returncode": completed.returncode, "payload": public_safe_payload(payload)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="preflight -> live smoke -> ops check を本文なしで順番実行する実運用MVPステータス。"
    )
    parser.add_argument("--app-name", default="Discord")
    parser.add_argument("--source-command", default=os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND", ""))
    parser.add_argument("--capture-profile", choices=["macos-screencapture-region"], default="")
    parser.add_argument("--capture-region", default="", help="x,y,w,h。region 必須、full-screen capture は使わない。")
    parser.add_argument("--ocr-language", default="eng", help="tesseract language code")
    parser.add_argument("--capture-timeout", type=float, default=30.0)
    parser.add_argument("--guild", default="private-discord")
    parser.add_argument("--channel", default="active-thread")
    parser.add_argument("--min-parsed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--ops-timeout", type=float, default=90.0, help="ops_check.py の最大秒数")
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="fixture/private command の CI smoke 用。実機では使わない。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_command = args.source_command
    capture_profile_payload = None
    if not source_command.strip() and args.capture_profile:
        try:
            source_command = build_capture_source_command(
                capture_profile=args.capture_profile,
                capture_region=args.capture_region,
                ocr_language=args.ocr_language,
                capture_timeout=args.capture_timeout,
            )
            capture_profile_payload = {
                "name": args.capture_profile,
                "region_set": True,
                "ocr_language": args.ocr_language,
                "image_output": "temporary",
                "text_output": "omitted",
                "outbound_actions": "disabled",
            }
        except ValueError as exc:
            print(
                _json(
                    {
                        "schema": "discord_live_mvp_status.v1",
                        "ok": False,
                        "stage": "capture_profile_config",
                        "reason": str(exc),
                        "text_output": "omitted",
                        "outbound_actions": "disabled",
                    }
                )
            )
            return 2
    preflight = None
    if not args.skip_preflight:
        preflight_args = ["--app-name", args.app_name]
        if args.capture_profile:
            preflight_args.extend(["--capture-profile", args.capture_profile, "--capture-region", args.capture_region])
        preflight = run_script("ops_preflight.py", preflight_args, timeout=15)
        preflight_payload = preflight["payload"]
        hard_blockers = [
            item
            for item in preflight_payload.get("blockers", [])
            if item not in {"discord_window_not_visible"}
        ]
        if hard_blockers:
            print(
                _json(
                    {
                        "schema": "discord_live_mvp_status.v1",
                        "ok": False,
                        "stage": "preflight",
                        "preflight": preflight_payload,
                        "live_smoke": None,
                        "ops_check": None,
                        "text_output": "omitted",
                        "outbound_actions": "disabled",
                    }
                )
            )
            return 2

    with tempfile.TemporaryDirectory(prefix="dcb-live-mvp-") as temp_dir:
        store = str(Path(temp_dir) / "events.ndjson")
        if not source_command.strip():
            print(
                _json(
                    {
                        "schema": "discord_live_mvp_status.v1",
                        "ok": False,
                        "stage": "live_smoke_config",
                        "preflight": preflight["payload"] if preflight else None,
                        "live_smoke": None,
                        "ops_check": None,
                        "blockers": ["source_command_missing"],
                        "capture_profile": capture_profile_payload,
                        "text_output": "omitted",
                        "outbound_actions": "disabled",
                    }
                )
            )
            return 2
        live = run_script(
            "live_ops_smoke.py",
            [
                "--store",
                store,
                "--source-command",
                source_command,
                "--guild",
                args.guild,
                "--channel",
                args.channel,
                "--min-parsed",
                str(args.min_parsed),
                "--json",
            ],
            timeout=args.timeout,
        )
        if live["returncode"] != 0:
            print(
                _json(
                    {
                        "schema": "discord_live_mvp_status.v1",
                        "ok": False,
                        "stage": "live_smoke",
                        "preflight": preflight["payload"] if preflight else None,
                        "live_smoke": live["payload"],
                        "ops_check": None,
                        "text_output": "omitted",
                        "outbound_actions": "disabled",
                    }
                )
            )
            return 2
        ops = run_script("ops_check.py", [], timeout=args.ops_timeout)

    ok = live["returncode"] == 0 and ops["returncode"] == 0 and bool(ops["payload"].get("ok"))
    print(
        _json(
            {
                "schema": "discord_live_mvp_status.v1",
                "ok": ok,
                "stage": "done" if ok else "ops_check",
                "preflight": preflight["payload"] if preflight else None,
                "capture_profile": capture_profile_payload,
                "live_smoke": live["payload"],
                "ops_check": ops["payload"],
                "success_conditions": {
                    "parsed_minimum_met": live["payload"].get("parsed", 0) >= args.min_parsed,
                    "source_ready": bool(live["payload"].get("source_ready")),
                    "gate_verdict": live["payload"].get("gate_verdict"),
                    "text_output": live["payload"].get("text_output"),
                    "outbound_actions": live["payload"].get("outbound_actions") or live["payload"].get("outbound"),
                    "ops_issue_count": ops["payload"].get("issue_count"),
                },
                "text_output": "omitted",
                "outbound_actions": "disabled",
            }
        )
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
