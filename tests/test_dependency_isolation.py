import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
URL = "https://discord.com/channels/11111111111111111/22222222222222222"


def _run_without_site_packages(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    return subprocess.run(
        [sys.executable, "-S", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_pdca_plan_starts_without_site_adapter_dependencies() -> None:
    completed = _run_without_site_packages(
        "scripts/pdca_e2e_inventory.py", "--dry-run", "--json"
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_cache_inventory_isolated_from_site_adapter_dependencies(tmp_path: Path) -> None:
    completed = _run_without_site_packages(
        "-m",
        "discord_context_bridge.cli",
        "cache-inventory",
        "--url",
        URL,
        "--snapshot-store",
        str(tmp_path / "snapshots.ndjson"),
        "--cache-root",
        str(tmp_path / "cache"),
        "--config-path",
        str(tmp_path / "config.json"),
        "--json",
    )
    assert completed.returncode == 2, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["state"] == "snapshot_missing"
    assert payload["outbound_actions"] == "disabled"
    assert "Traceback" not in completed.stderr


def test_site_adapter_reports_dependency_missing_without_traceback(tmp_path: Path) -> None:
    source = tmp_path / "visible.txt"
    source.write_text("visible fixture", encoding="utf-8")
    completed = _run_without_site_packages(
        "-m",
        "discord_context_bridge.cli",
        "capture-visible-snapshot",
        "--source-url",
        URL,
        "--input",
        str(source),
        "--output-root",
        str(tmp_path / ".local"),
        "--json",
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["failure_stage"] == "dependency_missing"
    assert "Traceback" not in completed.stderr
