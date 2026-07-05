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
    parser = argparse.ArgumentParser(description="url-intake-fast-path の metadata-only smoke を合成 snapshot で確認する。")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def run_fast_path(snapshot_store: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "discord_context_bridge.cli",
            "url-intake-fast-path",
            "--url",
            "https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333",
            "--snapshot-store",
            str(snapshot_store),
            "--hook-snapshot-status",
            "snapshot_missing",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def validate_payload(payload: dict[str, Any], output: str, snapshot_store: Path) -> list[str]:
    issues: list[str] = []
    expectations = {
        "schema": "discord_url_intake_fast_path.v1",
        "decision": "need_visible_text",
        "next_step": "ask_for_visible_text_or_paste",
        "recommended_command": "report-latest",
        "raw_text_returned": False,
        "paths_output": "omitted",
        "outbound_actions": "disabled",
    }
    for key, expected in expectations.items():
        if payload.get(key) != expected:
            issues.append(f"{key}: expected {expected!r}, got {payload.get(key)!r}")

    forbidden_fragments = (
        "https://discord.com/channels/",
        "111111111111111111",
        str(snapshot_store),
    )
    for fragment in forbidden_fragments:
        if fragment and fragment in output:
            issues.append(f"forbidden_output_fragment:{fragment}")
    return issues


def build_report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dcb-url-fast-path-") as tmpdir:
        snapshot_store = Path(tmpdir) / "text-snapshots.ndjson"
        snapshot_store.write_text("", encoding="utf-8")
        completed = run_fast_path(snapshot_store)
        if completed.returncode != 2:
            return {
                "schema": "discord_url_intake_fast_path_smoke.v1",
                "ok": False,
                "failure_stage": "fast_path_command",
                "returncode": completed.returncode,
                "stdout_chars": len(completed.stdout),
                "stderr_tail": "\n".join(completed.stderr.splitlines()[-20:]),
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return {
                "schema": "discord_url_intake_fast_path_smoke.v1",
                "ok": False,
                "failure_stage": "parse_json",
                "reason": str(exc),
                "stdout_chars": len(completed.stdout),
            }
        issues = validate_payload(payload, completed.stdout, snapshot_store)
        return {
            "schema": "discord_url_intake_fast_path_smoke.v1",
            "ok": not issues,
            "failure_stage": None if not issues else "metadata_contract",
            "issue_count": len(issues),
            "issues": issues,
            "decision": payload.get("decision"),
            "next_step": payload.get("next_step"),
            "raw_text_returned": payload.get("raw_text_returned"),
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report()
    if args.json:
        print(_json(report))
    else:
        status = "成功" if report["ok"] else "失敗"
        print(f"url-intake-fast-path smoke: {status}")
        if not report["ok"]:
            print(_json(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
