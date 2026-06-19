#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


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


def run_script(script_name: str, args: list[str], *, timeout: float, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(extra_env or {})
    pythonpath = os.pathsep.join([str(ROOT / "src"), str(ROOT / "scripts"), env.get("PYTHONPATH", "")])
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script_name), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=timeout,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "ok": False,
            "failure_stage": "capture_failed",
            "reason": "script did not return valid JSON",
            "text_output": "omitted",
            "outbound_actions": "disabled",
        }
    return {
        "returncode": completed.returncode,
        "payload": public_safe_payload(payload),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="private adapter probe と live smoke を順番に回す read-only E2E check。")
    parser.add_argument("--source-command", default=os.environ.get("DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND", ""))
    parser.add_argument("--guild", default="private-discord")
    parser.add_argument("--channel", default="active-thread")
    parser.add_argument("--min-parsed", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=90.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    probe = run_script(
        "private_adapter_probe.py",
        [
            "--guild",
            args.guild,
            "--channel",
            args.channel,
            "--min-parsed",
            str(args.min_parsed),
        ],
        timeout=args.timeout,
        extra_env={"DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND": args.source_command} if args.source_command else None,
    )
    if probe["returncode"] != 0:
        print(
            _json(
                {
                    "schema": "discord_private_adapter_e2e.v1",
                    "ok": False,
                    "stage": "probe",
                    "probe": probe["payload"],
                    "live_smoke": None,
                    "text_output": "omitted",
                    "outbound_actions": "disabled",
                }
            )
        )
        return 2
    if not args.source_command.strip():
        print(
            _json(
                {
                    "schema": "discord_private_adapter_e2e.v1",
                    "ok": False,
                    "stage": "e2e_config",
                    "probe": probe["payload"],
                    "live_smoke": None,
                    "blockers": ["source_command_missing"],
                    "text_output": "omitted",
                    "outbound_actions": "disabled",
                }
            )
        )
        return 2

    live = run_script(
        "live_ops_smoke.py",
        [
            "--source-command",
            args.source_command,
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
    ok = (
        live["returncode"] == 0
        and bool(live["payload"].get("source_ready"))
        and live["payload"].get("gate_verdict") == "pass"
    )
    print(
        _json(
            {
                "schema": "discord_private_adapter_e2e.v1",
                "ok": ok,
                "stage": "done" if ok else "live_smoke",
                "probe": probe["payload"],
                "live_smoke": live["payload"],
                "success_conditions": {
                    "source_ready": bool(live["payload"].get("source_ready")),
                    "gate_verdict": live["payload"].get("gate_verdict"),
                    "text_output": live["payload"].get("text_output"),
                    "outbound_actions": live["payload"].get("outbound_actions") or live["payload"].get("outbound"),
                },
                "text_output": "omitted",
                "outbound_actions": "disabled",
            }
        )
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
