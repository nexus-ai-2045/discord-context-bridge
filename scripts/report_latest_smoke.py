#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="report-latest の metadata-only smoke を合成 snapshot で確認する。")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def run_report_latest(snapshot_store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "discord_context_bridge.cli",
            "report-latest",
            "--snapshot-store",
            str(snapshot_store),
            "--json",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def write_synthetic_snapshot(snapshot_store: Path) -> None:
    payload = {
        "captured_at": "2026-07-02T00:00:00+00:00",
        "source": "chrome_extension_dom",
        "target_key": "synthetic-report-latest",
        "url": "https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333",
        "title": "synthetic private title",
        "text": "member-a: private launch wording should not leak\nmember-b: keep the report metadata-only",
        "acquisition_context": {
            "schema": "discord_bridge_acquisition_context.v1",
            "source_kind": "Chrome DOM",
            "source_route": "chrome_extension_dom",
            "record_origin": "local_visible_text_snapshot",
            "capture_method": "chrome_dom_visible_range",
            "live_browser_access_required_for_capture": True,
            "private_adapter_required_for_capture": True,
            "token_or_cookie_read": False,
            "outbound_actions": "disabled",
        },
    }
    snapshot_store.parent.mkdir(parents=True, exist_ok=True)
    snapshot_store.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def validate_payload(payload: dict[str, Any], output: str, snapshot_store: Path) -> list[str]:
    issues: list[str] = []
    report_context = (payload.get("source_summary") or {}).get("report_acquisition_context") or {}
    latest = payload.get("latest_saved_or_visible_message") or {}
    evidence = latest.get("body_evidence") or {}

    expectations = {
        "ok": True,
        "schema": "discord_bridge_latest_snapshot_report.v1",
        "raw_text_returned": False,
        "participant_names_returned": False,
        "local_paths_returned": False,
        "report_mode": "existing_saved_snapshot",
        "live_browser_access": False,
        "new_capture": False,
        "preview": "omitted",
    }
    observed = {
        "ok": payload.get("ok"),
        "schema": payload.get("schema"),
        "raw_text_returned": payload.get("raw_text_returned"),
        "participant_names_returned": payload.get("participant_names_returned"),
        "local_paths_returned": payload.get("local_paths_returned"),
        "report_mode": report_context.get("mode"),
        "live_browser_access": report_context.get("live_browser_access"),
        "new_capture": report_context.get("new_capture"),
        "preview": evidence.get("preview"),
    }
    for key, expected in expectations.items():
        if observed.get(key) != expected:
            issues.append(f"{key}: expected {expected!r}, got {observed.get(key)!r}")

    forbidden_fragments = (
        "private launch wording",
        "keep the report metadata-only",
        "member-a",
        "member-b",
        "synthetic private title",
        str(snapshot_store),
    )
    for fragment in forbidden_fragments:
        if fragment and fragment in output:
            issues.append(f"forbidden_output_fragment:{fragment}")
    return issues


def build_report(*, include_payload: bool = False) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dcb-report-latest-") as tmpdir:
        snapshot_store = Path(tmpdir) / "text-snapshots.ndjson"
        write_synthetic_snapshot(snapshot_store)
        completed = run_report_latest(snapshot_store)
        if completed.returncode != 0:
            return {
                "schema": "discord_bridge_report_latest_smoke.v1",
                "ok": False,
                "failure_stage": "report_latest_command",
                "returncode": completed.returncode,
                "stdout_chars": len(completed.stdout),
                "stderr_tail": "\n".join(completed.stderr.splitlines()[-20:]),
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return {
                "schema": "discord_bridge_report_latest_smoke.v1",
                "ok": False,
                "failure_stage": "parse_json",
                "reason": str(exc),
                "stdout_chars": len(completed.stdout),
            }
        issues = validate_payload(payload, completed.stdout, snapshot_store)
        report = {
            "schema": "discord_bridge_report_latest_smoke.v1",
            "ok": not issues,
            "failure_stage": None if not issues else "metadata_contract",
            "issue_count": len(issues),
            "issues": issues,
            "raw_text_returned": payload.get("raw_text_returned"),
            "live_browser_access": ((payload.get("source_summary") or {}).get("report_acquisition_context") or {}).get("live_browser_access"),
            "new_capture": ((payload.get("source_summary") or {}).get("report_acquisition_context") or {}).get("new_capture"),
        }
        if include_payload:
            report["payload"] = payload
        return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report()
    if args.json:
        print(_json(report))
    else:
        status = "成功" if report["ok"] else "失敗"
        print(f"report-latest smoke: {status}")
        if not report["ok"]:
            print(_json(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
