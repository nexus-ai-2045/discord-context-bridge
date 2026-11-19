#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SmokeCase:
    name: str
    args: list[str]
    expected_returncode: int
    expected_state: str
    expected_ok_to_send: bool
    expected_blockers: set[str]
    expected_route_failures: set[str]


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="send-pdca-preflight の route matrix を metadata-only で smoke する。")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def smoke_cases() -> list[SmokeCase]:
    return [
        SmokeCase(
            name="attachment_ready",
            args=[
                "--target-verified",
                "--draft-verified",
                "--attachment-required",
                "--attachment-file-verified",
                "--attachment-preview-verified",
                "--browser-route-status",
                "ready",
                "--webhook-route-status",
                "not-used",
            ],
            expected_returncode=0,
            expected_state="ready_for_human_send",
            expected_ok_to_send=True,
            expected_blockers=set(),
            expected_route_failures=set(),
        ),
        SmokeCase(
            name="attachment_preview_missing",
            args=[
                "--target-verified",
                "--draft-verified",
                "--attachment-required",
                "--attachment-file-verified",
                "--browser-route-status",
                "filechooser-timeout",
                "--webhook-route-status",
                "not-used",
                "--failure-note",
                "attachment preview was not visible before send",
            ],
            expected_returncode=2,
            expected_state="blocked",
            expected_ok_to_send=False,
            expected_blockers={"attachment_preview_not_verified"},
            expected_route_failures={"browser_route_unstable"},
        ),
        SmokeCase(
            name="wrong_webhook_target",
            args=[
                "--target-verified",
                "--draft-verified",
                "--browser-route-status",
                "ready",
                "--webhook-route-status",
                "wrong-target",
            ],
            expected_returncode=2,
            expected_state="blocked",
            expected_ok_to_send=False,
            expected_blockers=set(),
            expected_route_failures={"webhook_target_mismatch"},
        ),
        SmokeCase(
            name="text_only_manual_route",
            args=[
                "--target-verified",
                "--draft-verified",
                "--browser-route-status",
                "manual-only",
                "--webhook-route-status",
                "not-used",
            ],
            expected_returncode=0,
            expected_state="ready_for_human_send",
            expected_ok_to_send=True,
            expected_blockers=set(),
            expected_route_failures=set(),
        ),
    ]


def run_case(case: SmokeCase) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    command = [
        sys.executable,
        "-m",
        "discord_context_bridge.cli",
        "send-pdca-preflight",
        *case.args,
        "--json",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    issues: list[str] = []
    payload: dict[str, Any] = {}
    if completed.returncode != case.expected_returncode:
        issues.append(f"returncode: expected {case.expected_returncode}, got {completed.returncode}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        issues.append(f"json: {exc}")
    if payload:
        if payload.get("schema") != "discord_send_pdca_preflight.v1":
            issues.append("schema mismatch")
        if payload.get("state") != case.expected_state:
            issues.append(f"state: expected {case.expected_state!r}, got {payload.get('state')!r}")
        if payload.get("ok_to_send") is not case.expected_ok_to_send:
            issues.append(f"ok_to_send: expected {case.expected_ok_to_send!r}, got {payload.get('ok_to_send')!r}")
        blockers = set(payload.get("blockers") or [])
        route_failures = set(payload.get("route_failures") or [])
        if blockers != case.expected_blockers:
            issues.append(f"blockers: expected {sorted(case.expected_blockers)}, got {sorted(blockers)}")
        if route_failures != case.expected_route_failures:
            issues.append(
                f"route_failures: expected {sorted(case.expected_route_failures)}, got {sorted(route_failures)}"
            )
        boundary = payload.get("safety_boundary") or {}
        if boundary.get("discord_send_executed_by_this_tool") is not False:
            issues.append("discord_send_executed_by_this_tool is not false")
        if boundary.get("outbound_actions") != "disabled":
            issues.append("outbound_actions is not disabled")
        if boundary.get("raw_discord_text_output") != "omitted":
            issues.append("raw_discord_text_output is not omitted")
    forbidden_fragments = (
        "https://discord.com/channels/",
        "discord.com/api/webhooks",
        "111111111111111111",
        str(ROOT),
    )
    combined_output = completed.stdout + completed.stderr
    for fragment in forbidden_fragments:
        if fragment and fragment in combined_output:
            issues.append(f"forbidden_output_fragment:{fragment}")
    return {
        "name": case.name,
        "ok": not issues,
        "issues": issues,
        "returncode": completed.returncode,
        "state": payload.get("state"),
        "ok_to_send": payload.get("ok_to_send"),
        "blockers": payload.get("blockers") or [],
        "route_failures": payload.get("route_failures") or [],
    }


def build_report() -> dict[str, Any]:
    results = [run_case(case) for case in smoke_cases()]
    issues = [
        {"case": result["name"], "issues": result["issues"]}
        for result in results
        if not result["ok"]
    ]
    return {
        "schema": "discord_send_pdca_preflight_smoke.v1",
        "ok": not issues,
        "failure_stage": None if not issues else "route_matrix",
        "case_count": len(results),
        "issues": issues,
        "cases": results,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
        "discord_send_executed_by_this_tool": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report()
    if args.json:
        print(_json(report))
    else:
        status = "成功" if report["ok"] else "失敗"
        print(f"send-pdca-preflight smoke: {status}")
        if not report["ok"]:
            print(_json(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
