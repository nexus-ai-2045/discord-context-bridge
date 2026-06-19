import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "private_adapter_probe.py"
FIXTURE = ROOT / "tests" / "fixtures" / "visible_text.txt"
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import probe_visible_source
import read_visible_discord_text


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


def test_private_adapter_probe_classifies_source_not_found(tmp_path: Path):
    command = tmp_path / "adapter_fail.py"
    command.write_text(
        "import sys\n"
        "print('Discord 可視テキストの取得に失敗しました: not_found', file=sys.stderr)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )

    completed = run_probe({"DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND": f"{sys.executable} {command}"})

    payload = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["capture"]["error_class"] == "source_not_found"
    assert payload["capture"]["failure_stage"] == "dependency_missing"
    assert payload["capture"]["text_output"] == "omitted"
    assert str(tmp_path) not in completed.stdout


def test_private_adapter_probe_classifies_timeout(tmp_path: Path):
    command = tmp_path / "adapter_sleep.py"
    command.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")

    completed = run_probe(
        {
            "DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND": f"{sys.executable} {command}",
            "DISCORD_CONTEXT_BRIDGE_PRIVATE_TIMEOUT": "0.01",
        }
    )

    payload = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert payload["capture"]["error_class"] == "adapter_timeout"
    assert payload["capture"]["failure_stage"] == "timeout"
    assert payload["capture"]["text_output"] == "omitted"


def test_macos_accessibility_probe_payload_reports_ready_without_text():
    payload = read_visible_discord_text.build_macos_accessibility_probe_payload(
        [{"index": 1, "status": "ready", "chars": 120, "lines": 8}],
        focus_app=False,
    )

    assert payload["schema"] == "discord_macos_accessibility_probe.v1"
    assert payload["ok"] is True
    assert payload["any_ready"] is True
    assert payload["focus_app"] is False
    assert payload["text_output"] == "omitted"
    assert payload["outbound"] == "disabled"
    assert "failure_stage" not in payload


def test_macos_accessibility_probe_payload_classifies_blocker():
    payload = read_visible_discord_text.build_macos_accessibility_probe_payload(
        [],
        focus_app=False,
        reason="not_found",
    )

    assert payload["ok"] is False
    assert payload["any_ready"] is False
    assert payload["candidate_count"] == 0
    assert payload["failure_stage"] == "dependency_missing"
    assert payload["text_output"] == "omitted"


def test_macos_accessibility_probe_payload_classifies_permission_blocker():
    payload = read_visible_discord_text.build_macos_accessibility_probe_payload(
        [],
        focus_app=False,
        reason="permission",
    )

    assert payload["ok"] is False
    assert payload["failure_stage"] == "permission"
    assert payload["reason"] == "permission"
    assert payload["text_output"] == "omitted"


def test_macos_window_list_payload_omits_titles():
    payload = read_visible_discord_text.build_macos_window_list_payload(
        [(1, "private-server / secret-thread")],
        match_hint="secret",
    )

    assert payload["schema"] == "discord_macos_window_list.v1"
    assert payload["ok"] is True
    assert payload["candidate_count"] == 1
    assert payload["windows"] == [
        {"index": 1, "title_present": True, "title_length": 30, "hint_match": True}
    ]
    assert payload["text_output"] == "omitted"
    assert "private-server" not in str(payload)


def test_macos_window_list_payload_classifies_timeout():
    payload = read_visible_discord_text.build_macos_window_list_payload(
        [],
        reason="timeout",
        source_stage="window_list_timeout",
    )

    assert payload["ok"] is False
    assert payload["candidate_count"] == 0
    assert payload["failure_stage"] == "timeout"
    assert payload["reason"] == "timeout"
    assert payload["source_stage"] == "window_list_timeout"
    assert payload["outbound"] == "disabled"


def test_macos_accessibility_probe_payload_reports_source_stage():
    payload = read_visible_discord_text.build_macos_accessibility_probe_payload(
        [],
        focus_app=False,
        reason="timeout",
        source_stage="window_list_timeout",
    )

    assert payload["ok"] is False
    assert payload["failure_stage"] == "timeout"
    assert payload["reason"] == "timeout"
    assert payload["source_stage"] == "window_list_timeout"
    assert payload["text_output"] == "omitted"


def test_safe_probe_source_stage_classifies_timeout_contexts():
    assert (
        read_visible_discord_text.safe_probe_source_stage(
            RuntimeError("command timed out after 2s"),
            operation="window_list",
        )
        == "window_list_timeout"
    )
    assert (
        read_visible_discord_text.safe_probe_source_stage(
            RuntimeError("command timed out after 2s"),
            operation="accessibility_scan",
        )
        == "full_scan_timeout"
    )
    assert (
        read_visible_discord_text.safe_probe_source_stage(
            RuntimeError("process not found: Discord"),
            operation="window_list",
        )
        == "process_not_found"
    )
    assert (
        read_visible_discord_text.safe_probe_source_stage(
            RuntimeError("command failed with exit code 1: reason=not_found"),
            operation="window_list",
        )
        == "process_not_found"
    )
    assert (
        read_visible_discord_text.safe_probe_source_stage(
            RuntimeError("Discord window candidates not found."),
            operation="window_preflight",
        )
        == "window_not_found"
    )


def test_visible_source_ax_probe_warns_when_no_focus_probe_not_ready(monkeypatch):
    def fake_command_result(name, command, **kwargs):
        assert name == "macos_accessibility_probe"
        assert "--json" in command
        assert "--no-focus" in command
        return {
            "name": name,
            "status": "pass",
            "exit_code": 2,
            "failure_stage": "unsupported_screen_state",
            "source_stage": "window_not_found",
            "text_output": "omitted",
            "outbound": "disabled",
        }

    monkeypatch.setattr(probe_visible_source, "command_result", fake_command_result)

    result = probe_visible_source.ax_probe_check(timeout=1.0, limit=1)

    assert result["status"] == "warn"
    assert result["reason"] == "unsupported_screen_state"
    assert result["source_stage"] == "window_not_found"
    assert result["text_output"] == "omitted"
