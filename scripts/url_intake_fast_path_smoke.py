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
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.core import target_key_for_url


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="url-intake-fast-path の metadata-only smoke を合成 snapshot で確認する。")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def run_fast_path(snapshot_store: Path, raw_cache: Path, visible_text: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
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
            "--raw-cache",
            str(raw_cache),
            "--input",
            str(visible_text),
            "--json",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def validate_payload(payload: dict[str, Any], output: str, snapshot_store: Path, raw_cache: Path) -> list[str]:
    issues: list[str] = []
    expectations = {
        "schema": "discord_url_intake_fast_path.v1",
        "decision": "snapshot_metadata_ready",
        "next_step": "use_saved_snapshot_metadata",
        "recommended_command": "report-latest",
        "raw_text_returned": False,
        "paths_output": "omitted",
        "outbound_actions": "disabled",
        "refresh_needed_before_decision": True,
        "refresh_attempted_before_decision": True,
        "refresh_source_available": True,
    }
    for key, expected in expectations.items():
        if payload.get(key) != expected:
            issues.append(f"{key}: expected {expected!r}, got {payload.get(key)!r}")
    nested_expectations = {
        ("initial_recency", "status"): "stale",
        ("final_recency", "status"): "recent",
        ("cache_comparison", "raw_cache_checked"): True,
        ("cache_comparison", "snapshot_match_count"): 2,
        ("cache_comparison", "raw_cache_match_count"): 1,
        ("cache_comparison", "content_hash_match"): False,
        ("cache_comparison", "raw_text_returned"): False,
        ("refresh_snapshot", "saved"): True,
        ("refresh_snapshot", "raw_text_returned"): False,
    }
    for (parent, key), expected in nested_expectations.items():
        observed = payload.get(parent, {}).get(key)
        if observed != expected:
            issues.append(f"{parent}.{key}: expected {expected!r}, got {observed!r}")

    forbidden_fragments = (
        "https://discord.com/channels/",
        "111111111111111111",
        str(snapshot_store),
        str(raw_cache),
        "old smoke text",
        "latest smoke text",
        "member-smoke",
    )
    for fragment in forbidden_fragments:
        if fragment and fragment in output:
            issues.append(f"forbidden_output_fragment:{fragment}")
    return issues


def build_report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="dcb-url-fast-path-") as tmpdir:
        snapshot_store = Path(tmpdir) / "text-snapshots.ndjson"
        raw_cache = Path(tmpdir) / "raw-cache.ndjson"
        visible_text = Path(tmpdir) / "visible.txt"
        url = "https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333"
        stale_record = {
            "url": url,
            "target_key": target_key_for_url(url),
            "text": "member-smoke: old smoke text stays private",
            "captured_at": "2026-07-01T00:00:00+00:00",
            "content_hash": "old-smoke-hash",
            "outbound_actions": "disabled",
        }
        snapshot_store.write_text(json.dumps(stale_record, ensure_ascii=False) + "\n", encoding="utf-8")
        raw_cache.write_text(json.dumps(stale_record, ensure_ascii=False) + "\n", encoding="utf-8")
        visible_text.write_text("member-smoke: latest smoke text stays private", encoding="utf-8")
        completed = run_fast_path(snapshot_store, raw_cache, visible_text)
        if completed.returncode != 0:
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
        issues = validate_payload(payload, completed.stdout, snapshot_store, raw_cache)
        return {
            "schema": "discord_url_intake_fast_path_smoke.v1",
            "ok": not issues,
            "failure_stage": None if not issues else "metadata_contract",
            "issue_count": len(issues),
            "issues": issues,
            "decision": payload.get("decision"),
            "next_step": payload.get("next_step"),
            "refresh_attempted_before_decision": payload.get("refresh_attempted_before_decision"),
            "cache_comparison": payload.get("cache_comparison"),
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
