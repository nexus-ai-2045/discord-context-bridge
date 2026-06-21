import json
import sys
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import discord_bot_route_preflight
import discord_bot_private_ingest
import discord_plugin_route_status
import discord_main_route_smoke
import discord_channel_event_probe
import discord_inventory_dashboard
import discord_live_text_source
import discord_route_retry_decider
import e2e_discord_route_check
import e2e_private_adapter_check
import live_ops_smoke
import live_mvp_status
import ops_preflight
import route_timing_log
import fixture_13_step_e2e
from discord_context_bridge.cli import safe_command_failure_reason


def ready_channel_dir(tmp_path: Path) -> Path:
    channel_dir = tmp_path / "discord"
    channel_dir.mkdir()
    token_key = "DISCORD_" + "BOT_TOKEN"
    (channel_dir / ".env").write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")
    (channel_dir / "access.json").write_text(
        '{"dmPolicy":"allowlist","allowFrom":["123456789012345678"],"groups":{},"pending":{}}',
        encoding="utf-8",
    )
    return channel_dir


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


def test_bot_private_ingest_returns_context_without_text(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    text = "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n"

    payload = discord_bot_private_ingest.build_ingest_payload(
        text,
        channel_dir=channel_dir,
        guild="safe-guild",
        channel="safe-channel",
        draft="公開時期の前提を確認します。",
        min_parsed=1,
    )

    rendered = discord_bot_private_ingest._json(payload)
    assert payload["ok"] is True
    assert payload["parsed"] == 2
    assert payload["context_ready"] is True
    assert payload["send_capability"] == "disabled"
    assert payload["text_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_bot_private_ingest_empty_input_is_safe(tmp_path: Path):
    payload = discord_bot_private_ingest.build_ingest_payload(
        "",
        channel_dir=tmp_path,
        guild="safe-guild",
        channel="safe-channel",
        draft="",
        min_parsed=1,
    )

    assert payload["ok"] is False
    assert payload["failure_stage"] == "source_empty"
    assert payload["text_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"


def test_discord_plugin_route_status_masks_control_plane_values(tmp_path: Path):
    channel_dir = tmp_path / "discord"
    channel_dir.mkdir()
    token_key = "DISCORD_" + "BOT_TOKEN"
    (channel_dir / ".env").write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")
    (channel_dir / "access.json").write_text(
        (
            '{"dmPolicy":"allowlist",'
            '"allowFrom":["123456789012345678"],'
            '"groups":{"987654321098765432":["111111111111111111"]},'
            '"pending":{"222222222222222222":{"code":"333333"}}}'
        ),
        encoding="utf-8",
    )

    payload = discord_plugin_route_status.build_status(channel_dir)
    rendered = discord_plugin_route_status._json(payload)

    assert payload["ok"] is True
    assert payload["recommended_route"] == "bot_private_ingest"
    assert payload["routes"]["discord_configure"]["route_class"] == "control"
    assert payload["routes"]["discord_access"]["route_class"] == "control"
    assert payload["routes"]["bot_private_ingest"]["route_class"] == "main"
    assert payload["routes"]["computer_use_discord"]["route_class"] == "visual_fallback"
    assert payload["routes"]["ocr_region"]["route_class"] == "last_fallback"
    assert payload["routes"]["discord_configure"]["token_output"] == "omitted"
    assert payload["routes"]["discord_access"]["snowflake_values_output"] == "omitted"
    assert payload["routes"]["discord_access"]["pending_count"] == 1
    assert payload["routes"]["bot_private_ingest"]["outbound_actions"] == "disabled"
    assert payload["routes"]["computer_use_discord"]["send_capability"] == "disabled_by_policy"
    assert payload["routes"]["ocr_region"]["full_screen_capture"] == "disabled_by_policy"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered
    assert "987654321098765432" not in rendered
    assert "111111111111111111" not in rendered
    assert "222222222222222222" not in rendered
    assert "333333" not in rendered


def test_discord_main_route_smoke_reaches_gate_without_text(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    text = "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n"

    payload = discord_main_route_smoke.build_smoke_payload(
        text,
        channel_dir=channel_dir,
        guild="safe-guild",
        channel="safe-channel",
        draft="公開時期の前提を確認します。",
        min_parsed=1,
    )
    rendered = discord_main_route_smoke._json(payload)

    assert payload["ok"] is True
    assert payload["route"] == "bot_private_ingest"
    assert payload["route_class"] == "main"
    assert payload["route_ready"] is True
    assert payload["ingest_ready"] is True
    assert payload["parsed"] == 2
    assert payload["context_ready"] is True
    assert payload["quick_verdict"] in {"go", "wait", "ask-context", "risky"}
    assert payload["text_output"] == "omitted"
    assert payload["text_saved"] is False
    assert payload["outbound_actions"] == "disabled"
    assert payload["safety_boundary"]["send_capability"] == "disabled"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_discord_main_route_smoke_empty_input_is_safe(tmp_path: Path):
    payload = discord_main_route_smoke.build_smoke_payload(
        "",
        channel_dir=tmp_path,
        guild="safe-guild",
        channel="safe-channel",
        draft="",
        min_parsed=1,
    )

    assert payload["ok"] is False
    assert payload["failure_stage"] in {"main_route_not_ready", "source_empty"}
    assert payload["text_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"


def test_discord_channel_event_probe_reports_missing_text_without_names(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    inbox = channel_dir / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "123456789012345678-sensitive.png").write_bytes(b"fake")

    payload = discord_channel_event_probe.build_probe(channel_dir)
    rendered = discord_channel_event_probe._json(payload)

    assert payload["ok"] is False
    assert payload["preflight_ok"] is True
    assert payload["text_event_candidates"] == 0
    assert payload["media_inbox_count"] == 1
    assert payload["failure_stage"] == "no_text_event_source"
    assert payload["text_output"] == "omitted"
    assert payload["file_names_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678-sensitive.png" not in rendered
    assert "123456789012345678" not in rendered


def test_discord_channel_event_probe_detects_text_candidate_without_reading_it(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    inbox = channel_dir / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "event.ndjson").write_text("member-a: 公開時期の前提を確認したいです。\n", encoding="utf-8")

    payload = discord_channel_event_probe.build_probe(channel_dir)
    rendered = discord_channel_event_probe._json(payload)

    assert payload["ok"] is True
    assert payload["source_ready"] is True
    assert payload["text_event_candidates"] == 1
    assert payload["next"] == "pipe_text_event_to_discord_main_route_smoke"
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_discord_live_text_source_writes_latest_without_leaking_text(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    text = "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n"

    payload = discord_live_text_source.write_text_event(
        text,
        channel_dir=channel_dir,
        source_label="fixture source",
    )
    rendered = discord_live_text_source._json(payload)
    latest_text, latest_payload = discord_live_text_source.latest_text_event(channel_dir)
    latest_rendered = discord_live_text_source._json(latest_payload)

    assert payload["ok"] is True
    assert payload["written"] is True
    assert payload["text_event_candidates"] == 1
    assert payload["text_output"] == "omitted"
    assert payload["file_names_output"] == "omitted"
    assert latest_text == text
    assert latest_payload["ok"] is True
    assert latest_payload["event"]["lines"] == 2
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered
    assert "member-a" not in latest_rendered
    assert "公開時期の前提" not in latest_rendered


def test_e2e_discord_route_check_passes_fixture_without_channel_event(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    text = "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n"

    payload = e2e_discord_route_check.build_e2e_payload(
        text,
        channel_dir=channel_dir,
        guild="safe-guild",
        channel="safe-channel",
        draft="公開時期の前提を確認します。",
        min_parsed=1,
        require_channel_event=False,
        source_payload={"mode": "input", "write": None, "latest": None},
    )
    rendered = e2e_discord_route_check._json(payload)

    assert payload["ok"] is True
    assert payload["checks"]["main_route_smoke_ok"] is True
    assert payload["checks"]["channel_event_source_ready"] is False
    assert payload["main_route"]["quick_verdict"] in {"go", "wait", "ask-context", "risky"}
    assert payload["text_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_e2e_discord_route_check_blocks_when_channel_event_required(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    text = "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n"

    payload = e2e_discord_route_check.build_e2e_payload(
        text,
        channel_dir=channel_dir,
        guild="safe-guild",
        channel="safe-channel",
        draft="公開時期の前提を確認します。",
        min_parsed=1,
        require_channel_event=True,
        source_payload={"mode": "input", "write": None, "latest": None},
    )

    assert payload["ok"] is False
    assert payload["stage"] == "blocked"
    assert payload["blocked_stage"] == "no_text_event_source"
    assert payload["checks"]["main_route_smoke_ok"] is True
    assert payload["checks"]["channel_event_source_ready"] is False


def test_e2e_discord_route_check_uses_channel_event_when_requested(tmp_path: Path):
    channel_dir = ready_channel_dir(tmp_path)
    text = "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n"
    write_payload = discord_live_text_source.write_text_event(text, channel_dir=channel_dir)
    latest_text, latest_payload = discord_live_text_source.latest_text_event(channel_dir)

    payload = e2e_discord_route_check.build_e2e_payload(
        latest_text,
        channel_dir=channel_dir,
        guild="safe-guild",
        channel="safe-channel",
        draft="公開時期の前提を確認します。",
        min_parsed=1,
        require_channel_event=True,
        source_payload={"mode": "channel_event", "write": write_payload, "latest": latest_payload},
    )
    rendered = e2e_discord_route_check._json(payload)

    assert payload["ok"] is True
    assert payload["checks"]["source_ready"] is True
    assert payload["checks"]["channel_event_source_ready"] is True
    assert payload["source"]["mode"] == "channel_event"
    assert payload["text_output"] == "omitted"
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered


def test_fixture_13_step_e2e_passes_without_private_output(tmp_path: Path):
    payload = fixture_13_step_e2e.build_fixture_e2e_payload(
        "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n",
        server_context="サーバールール: 個人情報の共有は禁止です。",
        channel_context="チャンネル目的: 公開前の企画相談です。",
        thread_context="スレッドルール: 未確認の断定は禁止です。",
        draft="公開時期の前提を確認してから返信します。",
        thread_key="fixture-thread",
        work_dir=tmp_path,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "discord_13_step_fixture_e2e.v1"
    assert payload["ok"] is True
    assert payload["step_count"] == 13
    assert payload["passed"] == 13
    assert [item["name"] for item in payload["steps"]] == [
        "01_observation_entry",
        "02_ssot_registry_lookup",
        "03_read_scope_decision",
        "04_understanding_summary",
        "05_provisional_draft",
        "06_risk_review",
        "07_markdown_review_artifact",
        "08_final_candidate",
        "09_human_gate",
        "10_copy_block",
        "11_follow_up_state",
        "12_review_state_registry",
        "13_handoff_packet",
    ]
    assert payload["safety_boundary"]["raw_discord_text_output"] == "omitted"
    assert payload["safety_boundary"]["outbound_actions"] == "disabled"
    assert "member-a" not in rendered
    assert "公開時期の前提を確認" not in rendered
    assert str(tmp_path) not in rendered


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


def test_route_timing_log_appends_and_summarizes(tmp_path: Path):
    log = tmp_path / "timing.jsonl"

    route_timing_log.append_entry(
        log,
        route_timing_log.compact_entry(
            route="chrome-visible-dom",
            status="pass",
            elapsed_ms=1234.5678,
            parsed=10,
            gate_verdict="pass",
            source_ready=True,
        ),
    )
    route_timing_log.append_entry(
        log,
        route_timing_log.compact_entry(route="source-command", status="fail", elapsed_ms=50, stage="timeout"),
    )

    entries = route_timing_log.read_entries(log)
    summary = route_timing_log.summarize_entries(entries)

    rendered = json.dumps(summary, ensure_ascii=False)
    assert len(entries) == 2
    assert entries[0]["elapsed_ms"] == 1234.568
    assert entries[0]["text_output"] == "omitted"
    assert summary["routes"]["chrome-visible-dom"]["pass_count"] == 1
    assert summary["routes"]["source-command"]["last_status"] == "fail"
    assert "Discord本文" not in rendered


def test_live_ops_smoke_writes_timing_log(tmp_path: Path):
    source = tmp_path / "visible.txt"
    store = tmp_path / "events.ndjson"
    log = tmp_path / "timing.jsonl"
    source.write_text("member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n", encoding="utf-8")

    result = live_ops_smoke.main(
        [
            "--source-command",
            f"{sys.executable} -c \"from pathlib import Path; print(Path(r'{source}').read_text(), end='')\"",
            "--store",
            str(store),
            "--timing-log",
            str(log),
            "--route-label",
            "fixture-source-command",
            "--json",
        ]
    )

    entries = route_timing_log.read_entries(log)
    assert result == 0
    assert entries[0]["route"] == "fixture-source-command"
    assert entries[0]["status"] == "pass"
    assert entries[0]["parsed"] == 2
    assert entries[0]["elapsed_ms"] >= 0
    assert entries[0]["outbound_actions"] == "disabled"


def test_discord_inventory_dashboard_omits_sensitive_values(tmp_path: Path):
    channel_dir = tmp_path / "discord"
    inbox = channel_dir / "inbox"
    inbox.mkdir(parents=True)
    token_key = "DISCORD_" + "BOT_TOKEN"
    (channel_dir / ".env").write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")
    (channel_dir / "access.json").write_text(
        '{"dmPolicy":"allowlist","allowFrom":["123456789012345678"],"groups":{},"pending":{}}',
        encoding="utf-8",
    )
    (inbox / "123456789012345678-sensitive.png").write_bytes(b"fake")
    (inbox / "event.ndjson").write_text("member-a: 公開時期の前提を確認したいです。\n", encoding="utf-8")

    store = tmp_path / "dcb-safe.ndjson"
    live_ops_smoke.main(
        [
            "--source-command",
            f"{sys.executable} -c \"print('member-a: 公開時期の前提を確認したいです。')\"",
            "--store",
            str(store),
            "--json",
        ]
    )
    timing = tmp_path / "dcb-route-timing.jsonl"
    route_timing_log.append_entry(
        timing,
        route_timing_log.compact_entry(route="bot-private-ingest-fixture", status="pass", elapsed_ms=10, parsed=1),
    )

    payload = discord_inventory_dashboard.build_inventory(channel_dir, tmp_path, store_limit=10)
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "discord_inventory_dashboard.v1"
    assert payload["inbox"]["text_event_candidates"] == 1
    assert payload["inbox"]["media_inbox_count"] == 1
    assert payload["stores"]["shown_count"] == 1
    assert payload["timing_logs"][0]["entry_count"] == 1
    assert payload["safety_boundary"]["inbox_file_names_output"] == "omitted"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678-sensitive.png" not in rendered
    assert "123456789012345678" not in rendered
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_route_retry_decider_retries_api_routes_before_browser_fallback(tmp_path: Path):
    channel_dir = tmp_path / "discord"
    channel_dir.mkdir()
    token_key = "DISCORD_" + "BOT_TOKEN"
    (channel_dir / ".env").write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")
    (channel_dir / "access.json").write_text(
        '{"dmPolicy":"allowlist","allowFrom":["123456789012345678"],"groups":{},"pending":{}}',
        encoding="utf-8",
    )
    calls = []

    def fake_timeout(command: str, timeout: float) -> dict[str, object]:
        calls.append((command, timeout))
        return {
            "ok": False,
            "failure_stage": "timeout",
            "returncode": 124,
            "elapsed_ms": 10,
            "stdout_chars": 0,
            "stderr_chars": 0,
            "text_output": "omitted",
        }

    payload = discord_route_retry_decider.build_decision(
        channel_dir=channel_dir,
        gateway_command="gateway-fetch",
        rest_command="rest-fetch",
        attempts=4,
        timeout=0.01,
        interval=0,
        runner=fake_timeout,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is False
    assert payload["decision"] == "ask_browser_fallback"
    assert payload["chrome_fallback"]["auto_open"] is False
    assert payload["chrome_fallback"]["requires_user_go"] is True
    assert payload["routes"]["gateway_live_event"]["attempted"] == 4
    assert payload["routes"]["rest_backfill"]["attempted"] == 4
    assert len(calls) == 8
    assert "Chrome / visible fallback に切り替えますか" in payload["fallback_prompt"]
    assert "synthetic-secret" not in rendered
    assert "123456789012345678" not in rendered


def test_route_retry_decider_local_command_timeout_kills_process_group():
    result = discord_route_retry_decider.run_local_command(
        f"{sys.executable} -c \"import time; time.sleep(5)\"",
        timeout=0.05,
    )

    assert result["ok"] is False
    assert result["failure_stage"] == "timeout"
    assert result["returncode"] == 124
    assert result["elapsed_ms"] < 1000
    assert result["text_output"] == "omitted"


def test_route_retry_decider_uses_gateway_without_leaking_text(tmp_path: Path):
    channel_dir = tmp_path / "discord"
    channel_dir.mkdir()

    def fake_gateway(command: str, timeout: float) -> dict[str, object]:
        return {
            "ok": True,
            "failure_stage": None,
            "returncode": 0,
            "elapsed_ms": 3,
            "stdout_chars": len("member-a: 公開時期の前提を確認したいです。"),
            "stderr_chars": 0,
            "text_output": "omitted",
        }

    payload = discord_route_retry_decider.build_decision(
        channel_dir=channel_dir,
        gateway_command="gateway-fetch",
        rest_command="rest-fetch",
        attempts=5,
        timeout=1,
        interval=0,
        runner=fake_gateway,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["decision"] == "use_api_route"
    assert payload["selected_route"] == "gateway_live_event"
    assert payload["routes"]["gateway_live_event"]["attempted"] == 1
    assert payload["text_output"] == "omitted"
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_route_retry_decider_uses_inbox_when_api_unconfigured(tmp_path: Path):
    channel_dir = tmp_path / "discord"
    inbox = channel_dir / "inbox"
    inbox.mkdir(parents=True)
    token_key = "DISCORD_" + "BOT_TOKEN"
    (channel_dir / ".env").write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")
    (channel_dir / "access.json").write_text(
        '{"dmPolicy":"allowlist","allowFrom":["123456789012345678"],"groups":{},"pending":{}}',
        encoding="utf-8",
    )
    (inbox / "event.ndjson").write_text("member-a: 公開時期の前提を確認したいです。\n", encoding="utf-8")

    payload = discord_route_retry_decider.build_decision(
        channel_dir=channel_dir,
        gateway_command=None,
        rest_command=None,
        attempts=5,
        timeout=1,
        interval=0,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["decision"] == "use_bot_private_ingest"
    assert payload["selected_route"] == "bot_private_ingest"
    assert payload["routes"]["gateway_live_event"]["failure_stage"] == "not_configured"
    assert payload["routes"]["rest_backfill"]["failure_stage"] == "not_configured"
    assert payload["routes"]["bot_text_event_inbox"]["text_event_candidates"] == 1
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_route_retry_decider_notification_reports_osascript_failure(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["script"] = args[-1]
        return subprocess.CompletedProcess(args=args, returncode=7, stdout="", stderr="denied")

    monkeypatch.setattr(discord_route_retry_decider.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(discord_route_retry_decider.subprocess, "run", fake_run)

    payload = discord_route_retry_decider.notify_user(
        "Discord route fallback",
        "ログインできませんでした。Chrome に切り替えますか？",
    )

    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["ok"] is False
    assert payload["reason"] == "notification_failed"
    assert payload["returncode"] == 7
    assert payload["stderr_chars"] == len("denied")
    assert "ログインできませんでした" in captured["script"]
    assert "\\u30ed" not in captured["script"]
    assert "denied" not in rendered
