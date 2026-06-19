import sys
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import discord_bot_route_preflight
import e2e_private_adapter_check
import live_ops_smoke
import live_mvp_status
import ops_preflight
from discord_context_bridge.cli import safe_command_failure_reason


def test_public_safe_payload_omits_nested_store_paths():
    payload = {
        "live_smoke": {
            "store": "/tmp/private/events.ndjson",
            "nested": [{"store": "/tmp/other/events.ndjson"}],
        }
    }

    assert e2e_private_adapter_check.public_safe_payload(payload) == {
        "live_smoke": {
            "store": "omitted",
            "nested": [{"store": "omitted"}],
        }
    }
    assert live_mvp_status.public_safe_payload(payload)["live_smoke"]["store"] == "omitted"


def test_bot_route_env_status_detects_token_without_returning_value(tmp_path: Path):
    env_path = tmp_path / ".env"
    token_key = "DISCORD_" + "BOT_TOKEN"
    env_path.write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")

    status = discord_bot_route_preflight.read_env_status(env_path)

    assert status == {"exists": True, "token_set": True}
    assert "synthetic-secret" not in str(status)


def test_ops_preflight_reports_system_events_timeout(monkeypatch):
    monkeypatch.setattr(ops_preflight.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ops_preflight, "run_osascript", lambda *args, **kwargs: (False, "timeout"))

    status = ops_preflight.discord_process_status("Discord")

    assert status == {
        "checked": False,
        "running": False,
        "window_count": 0,
        "reason": "system_events_timeout",
    }


def test_ops_preflight_accepts_region_capture_profile(monkeypatch):
    monkeypatch.setattr(ops_preflight.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ops_preflight, "command_available", lambda name: True)
    monkeypatch.setattr(
        ops_preflight,
        "discord_process_status",
        lambda app_name: {"checked": True, "running": True, "window_count": 1, "reason": "ok"},
    )

    payload = ops_preflight.build_preflight(
        "Discord",
        capture_profile="macos-screencapture-region",
        capture_region="10,20,300,400",
    )

    assert payload["ok"] is True
    assert payload["private_adapter_configured"] is True
    assert payload["capture_profile"]["configured"] is True
    assert payload["capture_profile"]["region_set"] is True
    assert payload["capture_profile"]["text_output"] == "omitted"


def test_live_mvp_status_builds_capture_source_command_without_fullscreen():
    command = live_mvp_status.build_capture_source_command(
        capture_profile="macos-screencapture-region",
        capture_region="10,20,300,400",
        ocr_language="jpn+eng",
        capture_timeout=12,
    )

    assert "scripts/read_screenshot_ocr_text.py" in command
    assert "--capture-profile macos-screencapture-region" in command
    assert "--capture-region 10,20,300,400" in command
    assert "tesseract {image} stdout -l jpn+eng" in command
    assert "--screenshot-command" not in command


def test_live_mvp_status_returns_safe_json_on_ops_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["ops_check.py"], timeout=0.01)

    monkeypatch.setattr(live_mvp_status.subprocess, "run", fake_run)

    result = live_mvp_status.run_script("ops_check.py", [], timeout=0.01)

    assert result["returncode"] == 124
    assert result["payload"] == {
        "ok": False,
        "issue_count": 1,
        "failure_stage": "timeout",
        "reason": "ops_check_timeout",
        "text_output": "omitted",
        "outbound_actions": "disabled",
    }


def test_live_mvp_status_passes_source_timeout(monkeypatch):
    calls = []

    def fake_run_script(script_name, args, *, timeout):
        calls.append({"script_name": script_name, "args": args, "timeout": timeout})
        if script_name == "live_ops_smoke.py":
            return {
                "returncode": 0,
                "payload": {
                    "parsed": 1,
                    "source_ready": True,
                    "gate_verdict": "pass",
                    "text_output": "omitted",
                    "outbound": "disabled",
                },
            }
        return {
            "returncode": 0,
            "payload": {
                "ok": True,
                "issue_count": 0,
                "text_output": "omitted",
                "outbound_actions": "disabled",
            },
        }

    monkeypatch.setattr(live_mvp_status, "run_script", fake_run_script)

    result = live_mvp_status.main(
        [
            "--source-command",
            "discord-visible-text",
            "--source-timeout",
            "61",
            "--timeout",
            "90",
            "--skip-preflight",
        ]
    )

    live_call = next(call for call in calls if call["script_name"] == "live_ops_smoke.py")
    source_timeout_index = live_call["args"].index("--source-timeout") + 1
    assert result == 0
    assert live_call["args"][source_timeout_index] == "61.0"


def test_ocr_empty_source_failure_is_classified_without_adapter_failed():
    assert safe_command_failure_reason("OCR 結果が空です。対象範囲を確認してください。") == "ocr_empty"

    summary = live_ops_smoke.build_source_failure_summary(
        "local command の実行に失敗しました: exit_code=2 reason=ocr_empty",
        min_parsed=1,
    )

    assert summary["failure_stage"] == "ocr_empty"
    assert summary["source_stage"] == "ocr_empty"
    assert summary["reason"] == "ocr_empty"
    assert summary["text_output"] == "omitted"
