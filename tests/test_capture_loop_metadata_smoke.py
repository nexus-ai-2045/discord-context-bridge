from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_capture_loop_metadata_smoke_script_passes() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "scripts/capture_loop_metadata_smoke.py", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=env,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["overall"] == "ok"
    assert payload["live_discord"] is False
    assert payload["full_capture_confirmed"] is True
    assert payload["receipt_persisted"] is True
    assert payload["reconcile_returncode"] == 0
    assert payload["privacy_ok"] is True
    assert "live_discord_full_capture" in payload["not_claimed"]
