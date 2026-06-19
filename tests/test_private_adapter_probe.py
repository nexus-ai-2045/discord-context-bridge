import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "private_adapter_probe.py"
FIXTURE = ROOT / "tests" / "fixtures" / "visible_text.txt"


def run_probe(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    pythonpath_parts = [str(ROOT / "src"), str(ROOT / "scripts")]
    if extra_env and extra_env.get("PYTHONPATH"):
        pythonpath_parts.insert(0, extra_env["PYTHONPATH"])
    env = {**os.environ, **(extra_env or {}), "PYTHONPATH": os.pathsep.join(pythonpath_parts)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        check=False,
    )


def test_private_adapter_probe_reports_not_configured_without_secret_names():
    completed = run_probe(
        {
            "DISCORD_CONTEXT_BRIDGE_PRIVATE_ADAPTER": "",
            "DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND": "",
        }
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["adapter_configured"] is False
    assert payload["capture"]["failure_stage"] == "dependency_missing"
    assert payload["text_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"
    assert "DISCORD_CONTEXT_BRIDGE_PRIVATE" not in completed.stdout


def test_private_adapter_probe_command_reports_metadata_only():
    command = f"{sys.executable} scripts/read_visible_discord_text.py --input {FIXTURE}"
    completed = run_probe({"DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND": command})

    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["adapter_configured"] is True
    assert payload["capture"]["source_ready"] is True
    assert payload["capture"]["gate_verdict"] == "pass"
    assert payload["capture"]["parsed_event_count"] == 3
    assert payload["capture"]["text_returned"] is False
    assert "Can you clarify the premise" not in completed.stdout
    assert "member-a" not in completed.stdout


def test_private_adapter_probe_module_reports_metadata_only(tmp_path: Path):
    module = tmp_path / "private_adapter.py"
    module.write_text(
        "def read(args):\n"
        "    return 'member-a: private text must stay hidden\\nmember-b: reply\\n'\n",
        encoding="utf-8",
    )

    completed = run_probe(
        {
            "PYTHONPATH": str(tmp_path),
            "DISCORD_CONTEXT_BRIDGE_PRIVATE_ADAPTER": "private_adapter:read",
        }
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["capture"]["parsed_event_count"] == 2
    assert payload["capture"]["text_output"] == "omitted"
    assert "private text must stay hidden" not in completed.stdout
