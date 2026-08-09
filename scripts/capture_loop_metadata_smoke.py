#!/usr/bin/env python3
"""metadata-only capture-loop operational smoke.

Proves the landed CLI + ledger + full-capture bridge path without live Discord.
This is fixture evidence only. It must not claim live full capture.
"""

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


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _run_cli(store_root: Path, args: list[str]) -> tuple[int, dict[str, Any], str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "discord_context_bridge.cli",
            "capture-loop",
            *args,
            "--store-root",
            str(store_root),
            "--json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    stdout = completed.stdout or ""
    try:
        payload = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"parse_error": True, "stdout": stdout[:500]}
    return completed.returncode, payload if isinstance(payload, dict) else {}, stdout


def run_smoke() -> dict[str, Any]:
    private_target = "private-discord-url-must-not-leak"
    with tempfile.TemporaryDirectory(prefix="dcb-capture-loop-smoke-") as tmp:
        store_root = Path(tmp)
        windows_dir = store_root / "windows"
        windows_dir.mkdir(parents=True, exist_ok=True)

        code, started, start_out = _run_cli(
            store_root,
            [
                "start",
                "--target-key",
                private_target,
                "--route",
                "chrome_extension",
                "--upper-watermark",
                "message-2",
                "--scope",
                "thread_only",
                "--refresh-check",
            ],
        )
        if code != 0 or not started.get("capture_id"):
            return {
                "schema": "dcb_capture_loop_metadata_smoke.v1",
                "overall": "error",
                "reason": "start_failed",
                "returncode": code,
                "payload": started,
            }
        capture_id = str(started["capture_id"])

        cache_window = {
            "window_id": "cache-window-1",
            "source": "background_cache",
            "direction": "toward_latest",
            "scan_pass": 1,
            "oldest_reached": True,
            "latest_reached": True,
            "messages": [
                {"message_id": "message-1", "content_hash": "hash-1"},
                {"message_id": "message-2", "content_hash": "hash-2"},
            ],
        }
        live_window = {
            "window_id": "live-window-1",
            "source": "chrome_visible_dom",
            "direction": "toward_oldest",
            "scan_pass": 2,
            "oldest_reached": True,
            "latest_reached": True,
            "messages": [
                {"message_id": "message-1", "content_hash": "hash-1"},
                {"message_id": "message-2", "content_hash": "hash-2"},
            ],
        }
        cache_path = windows_dir / "cache.json"
        live_path = windows_dir / "live.json"
        cache_path.write_text(json.dumps(cache_window), encoding="utf-8")
        live_path.write_text(json.dumps(live_window), encoding="utf-8")

        code_cache, observe_cache, cache_out = _run_cli(
            store_root,
            [
                "observe",
                "--capture-id",
                capture_id,
                "--window-file",
                str(cache_path),
                "--expected-window-count",
                "0",
            ],
        )
        code_live, observe_live, live_out = _run_cli(
            store_root,
            [
                "observe",
                "--capture-id",
                capture_id,
                "--window-file",
                str(live_path),
                "--expected-window-count",
                "1",
            ],
        )
        code_status, status, status_out = _run_cli(
            store_root,
            ["status", "--capture-id", capture_id],
        )

        code_reconcile, reconcile, reconcile_out = _run_cli(
            store_root,
            [
                "reconcile",
                "--capture-id",
                capture_id,
            ],
        )
        combined_out = "\n".join(
            [start_out, cache_out, live_out, status_out, reconcile_out]
        )
        privacy_ok = private_target not in combined_out and "discord.com/channels" not in combined_out
        steps_ok = (
            code == 0
            and code_cache == 0
            and code_live == 0
            and code_status == 0
            and code_reconcile == 0
            and bool(status.get("capture_id"))
            and int((observe_live.get("coverage") or {}).get("window_count") or 0) == 2
            and bool(reconcile.get("full_capture_confirmed"))
            and bool(reconcile.get("receipt_persisted"))
            and privacy_ok
        )
        return {
            "schema": "dcb_capture_loop_metadata_smoke.v1",
            "overall": "ok" if steps_ok else "error",
            "live_discord": False,
            "claim": "fixture_metadata_only_path",
            "not_claimed": [
                "live_discord_full_capture",
                "attachment_live_inventory",
                "human_visible_thread_complete",
            ],
            "capture_id_present": bool(capture_id),
            "observe_window_count": int(
                (observe_live.get("coverage") or {}).get("window_count") or 0
            ),
            "full_capture_confirmed": bool(reconcile.get("full_capture_confirmed")),
            "receipt_persisted": bool(reconcile.get("receipt_persisted")),
            "reconcile_returncode": code_reconcile,
            "privacy_ok": privacy_ok,
            "returncodes": {
                "start": code,
                "observe_cache": code_cache,
                "observe_live": code_live,
                "status": code_status,
                "reconcile": code_reconcile,
            },
            "outbound_actions": "disabled",
            "raw_text_returned": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="JSON で出力する（既定も JSON）")
    parser.parse_args(argv)
    result = run_smoke()
    print(_json(result))
    return 0 if result.get("overall") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
