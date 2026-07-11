import argparse
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
import discord_rest_backfill
import e2e_discord_route_check
import e2e_private_adapter_check
import codex_discord_ingress_smoke
import live_ops_smoke
import local_smoke
import live_mvp_status
import ops_check
import ops_preflight
import route_timing_log
import fixture_13_step_e2e
import gh_pr_read
from discord_context_bridge.cli import safe_command_failure_reason
from discord_context_bridge.credentials import (
    BOT_TOKEN_ENV,
    TOKEN_COMMAND_ENV,
    configured_bot_token_provider,
    load_bot_token_from_provider,
)


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


def test_configured_bot_token_provider_detects_secret_command_without_value(monkeypatch):
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(TOKEN_COMMAND_ENV, f'"{sys.executable}" -c "print(12345)"')

    status = configured_bot_token_provider()

    assert status["ok"] is True
    assert status["provider"] == "secret_command"
    assert status["token_set"] is True
    assert status["value_returned"] is False
    assert "12345" not in json.dumps(status, ensure_ascii=False)


def test_secret_command_loads_token_without_public_value(monkeypatch):
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(TOKEN_COMMAND_ENV, f'"{sys.executable}" -c "print(12345)"')

    result = load_bot_token_from_provider()

    assert result.ok is True
    assert result.provider == "secret_command"
    assert result.token == "12345"
    assert "12345" not in json.dumps(result.public_status(), ensure_ascii=False)


def test_secret_command_failure_omits_stdout_and_stderr(monkeypatch):
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(
        TOKEN_COMMAND_ENV,
        f'"{sys.executable}" -c "import sys; sys.stderr.write(\'hidden-secret\'); sys.exit(7)"',
    )

    result = load_bot_token_from_provider()
    status = result.public_status()

    assert result.ok is False
    assert status["failure_stage"] == "token_command_failed"
    assert status["exit_code"] == 7
    assert "hidden-secret" not in json.dumps(status, ensure_ascii=False)


def test_bot_route_preflight_accepts_secret_command_provider(tmp_path: Path, monkeypatch):
    channel_dir = tmp_path / "discord"
    channel_dir.mkdir()
    (channel_dir / "access.json").write_text(json.dumps({"dmPolicy": "locked", "allowFrom": ["safe"]}), encoding="utf-8")
    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(TOKEN_COMMAND_ENV, f'"{sys.executable}" -c "print(12345)"')

    payload = discord_bot_route_preflight.build_preflight(channel_dir)

    assert payload["ok"] is True
    assert payload["bot_token"]["set"] is True
    assert payload["bot_token"]["provider"] == "secret_command"
    assert payload["bot_token"]["value_returned"] is False
    assert "12345" not in json.dumps(payload, ensure_ascii=False)


def test_rest_backfill_blocks_missing_bot_token_without_leaking(monkeypatch):
    monkeypatch.delenv("DISCORD_" + "BOT_TOKEN", raising=False)
    monkeypatch.delenv(TOKEN_COMMAND_ENV, raising=False)

    result = discord_rest_backfill.main(["--url", "https://discord.com/channels/7/8/9", "--json"])

    assert result == 2


def test_rest_backfill_uses_secret_command_provider_without_leaking(monkeypatch, tmp_path: Path, capsys):
    captured: dict[str, str] = {}

    def fake_fetch_discord_messages(*, channel_id: str, token: str, limit: int, page_size: int, sleep_seconds: float):
        captured["token"] = token
        return ([{"id": "123456789012345678", "content": "secret-command private text"}], None)

    monkeypatch.delenv(BOT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(TOKEN_COMMAND_ENV, f'"{sys.executable}" -c "print(12345)"')
    monkeypatch.setattr(discord_rest_backfill, "fetch_discord_messages", fake_fetch_discord_messages)
    raw_output = tmp_path / "raw.ndjson"
    manifest_output = tmp_path / "manifest.json"

    result = discord_rest_backfill.main(
        [
            "--url",
            "https://discord.com/channels/7/8/9",
            "--raw-output",
            str(raw_output),
            "--manifest-output",
            str(manifest_output),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert captured["token"] == "12345"
    assert payload["credential_provider"]["provider"] == "secret_command"
    assert payload["credential_provider"]["value_returned"] is False
    assert "12345" not in output
    assert "secret-command private text" not in output


def test_rest_backfill_fixture_writes_private_artifacts_without_stdout_text(tmp_path: Path, capsys):
    fixture = tmp_path / "messages.jsonl"
    fixture.write_text(
        json.dumps(
            {
                "id": "123456789012345678",
                "content": "member-h private fixture text should not print",
                "attachments": [{"filename": "evidence.png"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    raw_output = tmp_path / "raw" / "rest.ndjson"
    manifest_output = tmp_path / "manifest" / "rest.json"

    result = discord_rest_backfill.main(
        [
            "--url",
            "https://discord.com/channels/7/8/9",
            "--fixture-input",
            str(fixture),
            "--raw-output",
            str(raw_output),
            "--manifest-output",
            str(manifest_output),
            "--full-thread-confirmed",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["route"] == "rest_backfill"
    assert payload["coverage"]["message_count"] == 1
    assert payload["coverage"]["attachment_candidate_count"] == 1
    assert payload["coverage"]["full_thread_confirmed"] is True
    assert payload["token_output"] == "omitted"
    assert payload["path_output"] == "omitted"
    assert "private fixture text" not in output
    assert "123456789012345678" not in output
    assert str(raw_output) not in output
    assert raw_output.exists()
    assert manifest_output.exists()


def test_rest_backfill_rate_limit_payload_is_retryable():
    payload = discord_rest_backfill.blocked_payload("rate_limited_retryable", retry_after=1.5)

    assert payload["ok"] is False
    assert payload["rate_limit"]["status"] == "rate_limited_retryable"
    assert payload["rate_limit"]["retryable"] is True
    assert payload["raw_text_returned"] is False


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
        understanding_confirmed=True,
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
    assert payload["recommended_route"] == "rest_backfill"
    assert payload["routes"]["discord_configure"]["route_class"] == "control"
    assert payload["routes"]["discord_access"]["route_class"] == "control"
    assert payload["routes"]["rest_backfill"]["route_class"] == "main"
    assert payload["routes"]["bot_private_ingest"]["route_class"] == "main"
    assert payload["routes"]["computer_use_discord"]["route_class"] == "visual_fallback"
    assert payload["routes"]["ocr_region"]["route_class"] == "last_fallback"
    assert payload["routes"]["discord_configure"]["token_output"] == "omitted"
    assert payload["routes"]["rest_backfill"]["token_output"] == "omitted"
    assert payload["routes"]["rest_backfill"]["raw_text_output"] == "omitted"
    assert payload["routes"]["rest_backfill"]["outbound_actions"] == "disabled"
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
        understanding_confirmed=True,
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
        understanding_confirmed=True,
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
        understanding_confirmed=True,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "discord_13_step_fixture_e2e.v1"
    assert payload["ok"] is True
    assert payload["step_count"] == 13
    assert payload["passed"] == 13
    assert payload["regression_contract"] == {
        "schema": "discord_13_step_regression_contract.v1",
        "required_step_count": 13,
        "review_artifact_checked": True,
        "human_gate_checked": True,
        "handoff_packet_checked": True,
        "missing_required_checks": [],
        "raw_text_output": "omitted",
        "outbound_actions": "disabled",
    }
    assert [item["name"] for item in payload["steps"]] == [
        "01_observation_entry",
        "02_ssot_registry_lookup",
        "03_read_scope_decision",
        "04_understanding_summary",
        "05_understanding_gate",
        "06_provisional_draft",
        "07_risk_review",
        "08_markdown_review_artifact",
        "09_final_candidate",
        "10_human_gate",
        "11_copy_block",
        "12_review_state_registry",
        "13_handoff_packet",
    ]
    assert payload["safety_boundary"]["raw_discord_text_output"] == "omitted"
    assert payload["safety_boundary"]["outbound_actions"] == "disabled"
    assert "member-a" not in rendered
    assert "公開時期の前提を確認" not in rendered
    assert str(tmp_path) not in rendered


def test_fixture_13_step_e2e_accepts_codex_ingress_metadata(tmp_path: Path):
    payload = fixture_13_step_e2e.build_fixture_e2e_payload(
        "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n",
        server_context="サーバールール: 個人情報の共有は禁止です。",
        channel_context="チャンネル目的: 公開前の企画相談です。",
        thread_context="スレッドルール: 未確認の断定は禁止です。",
        draft="公開時期の前提を確認してから返信します。",
        thread_key="fixture-thread",
        work_dir=tmp_path,
        understanding_confirmed=True,
        entry_metadata={"schema": "codex_discord_ingress_smoke.v1", "ok": True, "stage": "ready_for_bridge"},
    )
    observation = payload["steps"][0]
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert observation["codex_ingress_ready"] is True
    assert observation["entry_surface"] == "discord_web"
    assert "member-a" not in rendered
    assert str(tmp_path) not in rendered


def test_fixture_13_step_e2e_blocks_without_understanding_confirmation(tmp_path: Path):
    payload = fixture_13_step_e2e.build_fixture_e2e_payload(
        "member-a: 公開時期の前提を確認したいです。\nmember-b: まず文脈を揃えましょう。\n",
        server_context="サーバールール: 個人情報の共有は禁止です。",
        channel_context="チャンネル目的: 公開前の企画相談です。",
        thread_context="スレッドルール: 未確認の断定は禁止です。",
        draft="公開時期の前提を確認してから返信します。",
        thread_key="fixture-thread",
        work_dir=tmp_path,
    )
    steps = {item["name"]: item for item in payload["steps"]}

    assert payload["ok"] is False
    assert payload["stage"] == "blocked"
    assert steps["05_understanding_gate"]["ok"] is False
    assert steps["05_understanding_gate"]["status"] == "blocked"
    assert steps["09_final_candidate"]["ok"] is False
    assert steps["11_copy_block"]["status"] == "blocked"


def test_codex_discord_ingress_ready_state_is_safe():
    payload = codex_discord_ingress_smoke.build_ingress_payload(
        chrome_opened=True,
        current_url="https://discord.com/channels/@me/111111111111111111/222222222222222222",
        current_title="private thread title",
        human_state="ready",
        confirm_decision="approved",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "codex_discord_ingress_smoke.v1"
    assert payload["ok"] is True
    assert payload["stage"] == "ready_for_bridge"
    assert payload["safety_boundary"]["manual_text_copy_required"] is False
    assert payload["safety_boundary"]["discord_app_required"] is False
    assert payload["safety_boundary"]["outbound_actions"] == "disabled"
    assert "111111111111111111" not in rendered
    assert "222222222222222222" not in rendered
    assert "private thread title" not in rendered


def test_codex_discord_ingress_waits_for_human_navigation_without_copying_text():
    payload = codex_discord_ingress_smoke.build_ingress_payload(
        chrome_opened=True,
        current_url="https://discord.com/channels/@me",
        current_title="Discord",
        human_state="navigating",
        confirm_decision="pending",
    )

    assert payload["ok"] is False
    assert payload["blocked_stage"] == "human_navigation_pending"
    assert payload["next_action"] == "人間が Chrome 上で目的のサーバー、チャンネル、スレッドへ移動するまで待ちます。"
    assert payload["safety_boundary"]["manual_text_copy_required"] is False


def test_codex_discord_ingress_blocks_non_discord_tab():
    payload = codex_discord_ingress_smoke.build_ingress_payload(
        chrome_opened=True,
        current_url="https://example.com/channels/@me/111",
        current_title="not discord",
        human_state="ready",
        confirm_decision="approved",
    )

    assert payload["ok"] is False
    assert payload["blocked_stage"] == "discord_not_open"
    assert payload["steps"][3]["host_label"] == "other"


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


def test_ops_preflight_accepts_json_flag_for_cli_consistency():
    args = ops_preflight.build_parser().parse_args(["--app-name", "Discord", "--json"])

    assert args.app_name == "Discord"
    assert args.json is True


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
            f"{sys.executable} -c \"from pathlib import Path; print(Path(r'{source}').read_text(encoding='utf-8'), end='')\"",
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


def test_local_smoke_reset_allows_missing_store_files(tmp_path: Path, monkeypatch):
    store = tmp_path / "missing-events.ndjson"
    context_store = tmp_path / "missing-context.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "local_smoke.py",
            "--reset",
            "--skip-http",
            "--store",
            str(store),
            "--context-store",
            str(context_store),
        ],
    )

    assert local_smoke.main() == 0
    assert store.exists()
    assert context_store.exists()


def test_live_ops_smoke_reset_allows_missing_store_file(tmp_path: Path):
    store = tmp_path / "missing-live-events.ndjson"

    result = live_ops_smoke.main(
        [
            "--reset",
            "--source-command",
            f"{sys.executable} -c \"print('member-a: 公開時期の前提を確認したいです。')\"",
            "--store",
            str(store),
            "--json",
        ]
    )

    assert result == 0
    assert store.exists()


def test_ops_check_local_smoke_uses_run_scoped_store_paths(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run_command(name: str, command: list[str], **kwargs) -> ops_check.CheckResult:
        captured[name] = command
        return ops_check.CheckResult(name, True, 0.0, command, "")

    monkeypatch.setattr(ops_check, "run_command", fake_run_command)
    args = ops_check.parse_args(["--skip-http"])

    checks = ops_check.build_checks(args)
    checks["ローカルスモーク"]()
    command = captured["ローカルスモーク"]

    assert "--store" in command
    assert "--context-store" in command
    store = Path(command[command.index("--store") + 1])
    context_store = Path(command[command.index("--context-store") + 1])
    assert store.name.startswith("dcb-local-smoke-")
    assert context_store.name.startswith("dcb-context-library-smoke-")
    assert str(store) != "/tmp/dcb-local-smoke.ndjson"
    assert str(context_store) != "/tmp/dcb-context-library-smoke.json"


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
    assert payload["operational_status"]["schema"] == "discord_inventory_operational_status.v1"
    assert payload["operational_status"]["now"]["working_tree"] in {"clean", "dirty"}
    assert payload["operational_status"]["safety_boundary"]["raw_discord_text_output"] == "omitted"
    assert payload["operational_status"]["safety_boundary"]["local_paths_output"] == "omitted"
    assert payload["safety_boundary"]["inbox_file_names_output"] == "omitted"
    assert "synthetic-secret" not in rendered
    assert "123456789012345678-sensitive.png" not in rendered
    assert "123456789012345678" not in rendered
    assert "member-a" not in rendered
    assert "公開時期の前提" not in rendered


def test_discord_inventory_dashboard_reports_zero_residual_operational_state(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    def fake_run(command: list[str]):
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed("{}")
        if command[:3] == ["git", "status", "--short"]:
            return Completed("")
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed("main\n")
        if command[:5] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return Completed("", returncode=1)
        if command[:4] == ["git", "rev-list", "--left-right", "--count"]:
            return Completed("0\t0\n")
        if command[:3] == ["gh", "pr", "list"]:
            return Completed("[]")
        raise AssertionError(command)

    monkeypatch.setattr(discord_inventory_dashboard, "run_command", fake_run)

    payload = discord_inventory_dashboard.build_operational_status()

    assert payload["now"]["state"] == "done"
    assert payload["residual_count"] == 0
    assert payload["github"]["open_pr_count"] == 0
    assert payload["safety_boundary"]["outbound_actions"] == "disabled"


def test_discord_inventory_dashboard_reports_actionable_residuals(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    def fake_run(command: list[str]):
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed("{}")
        if command[:3] == ["git", "status", "--short"]:
            return Completed(" M README.md\n")
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed("codex/example\n")
        if command[:5] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return Completed("", returncode=1)
        if command[:4] == ["git", "rev-list", "--left-right", "--count"]:
            return Completed("1\t2\n")
        if command[:3] == ["gh", "pr", "list"]:
            return Completed(
                json.dumps(
                    [
                        {
                            "number": 36,
                            "title": "impact-todo の旧clone整理を完了に更新する",
                            "headRefName": "codex/impact-todo-old-clone-done-20260704",
                            "mergeStateStatus": "CLEAN",
                            "isDraft": False,
                            "url": "https://github.com/nexus-ai-2045/discord-context-bridge/pull/36",
                        }
                    ]
                )
            )
        raise AssertionError(command)

    monkeypatch.setattr(discord_inventory_dashboard, "run_command", fake_run)

    payload = discord_inventory_dashboard.build_operational_status()
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["now"]["state"] == "attention_required"
    assert payload["residual_count"] == 4
    assert {item["code"] for item in payload["residual"]} == {
        "working_tree_dirty",
        "branch_behind_upstream",
        "local_commits_unpushed",
        "open_pull_requests",
    }
    assert payload["github"]["open_pr_count"] == 1
    assert "synthetic-secret" not in rendered


def test_discord_inventory_dashboard_checks_gh_account_before_pr_lookup(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(command: list[str]):
        calls.append(command)
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed('{"active_account_after":"nexus-ai-2045","ok":true}')
        if command[:3] == ["git", "status", "--short"]:
            return Completed("")
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed("main\n")
        if command[:5] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return Completed("", returncode=1)
        if command[:4] == ["git", "rev-list", "--left-right", "--count"]:
            return Completed("0\t0\n")
        if command[:3] == ["gh", "pr", "list"]:
            return Completed("[]")
        raise AssertionError(command)

    monkeypatch.setattr(discord_inventory_dashboard, "run_command", fake_run)

    payload = discord_inventory_dashboard.build_operational_status()

    assert payload["github"]["error"] is None
    assert payload["residual_count"] == 0
    assert calls.index([sys.executable, "scripts/gh_guard.py", "--account-only", "--json"]) < calls.index(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,mergeStateStatus,isDraft,url",
            "--limit",
            "50",
        ]
    )


def test_discord_inventory_dashboard_blocks_pr_lookup_on_gh_account_mismatch(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(command: list[str]):
        calls.append(command)
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed('{"account_matches":false,"ok":false}', returncode=2)
        if command[:3] == ["git", "status", "--short"]:
            return Completed("")
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed("main\n")
        if command[:5] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return Completed("", returncode=1)
        if command[:4] == ["git", "rev-list", "--left-right", "--count"]:
            return Completed("0\t0\n")
        if command[:3] == ["gh", "pr", "list"]:
            raise AssertionError("gh pr list should not run when account boundary fails")
        raise AssertionError(command)

    monkeypatch.setattr(discord_inventory_dashboard, "run_command", fake_run)

    payload = discord_inventory_dashboard.build_operational_status()

    assert payload["github"]["error"] == "gh_account_boundary_failed"
    assert {item["code"] for item in payload["residual"]} == {"gh_account_boundary_failed"}
    assert [sys.executable, "scripts/gh_guard.py", "--account-only", "--json"] in calls


def test_gh_pr_read_checks_account_before_list(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(command: list[str]):
        calls.append(command)
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed(
                json.dumps(
                    {
                        "actual_owner": "nexus-ai-2045",
                        "actual_repository": "discord-context-bridge",
                        "active_account_after": "nexus-ai-2045",
                        "ok": True,
                    }
                )
            )
        if command[:3] == ["gh", "pr", "list"]:
            return Completed("[]")
        raise AssertionError(command)

    monkeypatch.setattr(gh_pr_read, "run_command", fake_run)

    payload = gh_pr_read.build_payload(argparse.Namespace(command="list", limit=50, switch=False))

    assert payload["ok"] is True
    assert payload["prs"] == []
    assert calls.index([sys.executable, "scripts/gh_guard.py", "--account-only", "--json"]) < calls.index(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,title,headRefName,mergeStateStatus,isDraft,url",
            "--limit",
            "50",
        ]
    )


def test_gh_pr_read_blocks_view_on_account_mismatch(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(command: list[str]):
        calls.append(command)
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed('{"account_matches":false,"ok":false}', returncode=2)
        if command[:3] == ["gh", "pr", "view"]:
            raise AssertionError("gh pr view should not run when account boundary fails")
        raise AssertionError(command)

    monkeypatch.setattr(gh_pr_read, "run_command", fake_run)

    try:
        gh_pr_read.build_payload(argparse.Namespace(command="view", number="4", switch=False))
    except SystemExit as exc:
        payload = json.loads(str(exc))
    else:
        raise AssertionError("expected SystemExit")

    assert payload["ok"] is False
    assert payload["error"] == "gh_account_boundary_failed"
    assert [sys.executable, "scripts/gh_guard.py", "--account-only", "--json"] in calls


def test_gh_pr_read_can_switch_before_view(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(command: list[str]):
        calls.append(command)
        if command[:4] == [sys.executable, "scripts/gh_guard.py", "--switch", "--account-only"]:
            return Completed(
                json.dumps(
                    {
                        "actual_owner": "nexus-ai-2045",
                        "actual_repository": "discord-context-bridge",
                        "active_account_after": "nexus-ai-2045",
                        "ok": True,
                        "switched": True,
                    }
                )
            )
        if command[:3] == ["gh", "pr", "view"]:
            return Completed(
                json.dumps(
                    {
                        "number": 4,
                        "title": "REST backfill",
                        "url": "https://github.com/nexus-ai-2045/discord-context-bridge/pull/4",
                    }
                )
            )
        raise AssertionError(command)

    monkeypatch.setattr(gh_pr_read, "run_command", fake_run)

    payload = gh_pr_read.build_payload(argparse.Namespace(command="view", number="4", switch=True))

    assert payload["ok"] is True
    assert payload["pr"]["number"] == 4
    assert [sys.executable, "scripts/gh_guard.py", "--switch", "--account-only", "--json"] in calls


def test_discord_inventory_dashboard_uses_upstream_for_feature_branch_sync(monkeypatch):
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    def fake_run(command: list[str]):
        if command[:3] == [sys.executable, "scripts/gh_guard.py", "--account-only"]:
            return Completed("{}")
        if command[:3] == ["git", "status", "--short"]:
            return Completed("")
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed("codex/example\n")
        if command[:5] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"]:
            return Completed("origin/codex/example\n")
        if command[:4] == ["git", "rev-list", "--left-right", "--count"] and command[4] == "HEAD...origin/main":
            return Completed("1\t0\n")
        if command[:4] == ["git", "rev-list", "--left-right", "--count"] and command[4] == "HEAD...origin/codex/example":
            return Completed("0\t0\n")
        if command[:3] == ["gh", "pr", "list"]:
            return Completed("[]")
        raise AssertionError(command)

    monkeypatch.setattr(discord_inventory_dashboard, "run_command", fake_run)

    payload = discord_inventory_dashboard.build_operational_status()

    assert payload["residual_count"] == 0
    assert payload["now"]["branch_upstream"] == "origin/codex/example"
    assert payload["now"]["origin_main_ahead"] == 1


def test_ops_check_includes_status_dashboard():
    args = ops_check.parse_args(["--skip-http"])

    checks = ops_check.build_checks(args)

    assert "status dashboard" in checks


def test_repo_goal_status_reports_done_when_dashboard_is_clean(monkeypatch):
    import repo_goal_status

    monkeypatch.setattr(
        repo_goal_status.discord_inventory_dashboard,
        "build_inventory",
        lambda channel_dir, tmp_dir, store_limit: {
            "ok": True,
            "operational_status": {
                "now": {
                    "state": "done",
                    "working_tree": "clean",
                    "origin_main_ahead": 0,
                    "origin_main_behind": 0,
                },
                "github": {"open_pr_count": 0, "error": None},
                "residual_count": 0,
                "residual": [],
                "next": [],
            },
            "safety_boundary": {
                "raw_discord_text_output": "omitted",
                "outbound_actions": "disabled",
            },
        },
    )

    payload = repo_goal_status.build_goal_status()

    assert payload["ok"] is True
    assert payload["state"] == "done"
    assert payload["dashboard"]["residual_count"] == 0
    assert payload["external_actions_performed"] is False


def test_repo_goal_status_blocks_when_feature_upstream_is_clean_but_origin_main_differs(monkeypatch):
    import repo_goal_status

    monkeypatch.setattr(
        repo_goal_status.discord_inventory_dashboard,
        "build_inventory",
        lambda channel_dir, tmp_dir, store_limit: {
            "ok": True,
            "operational_status": {
                "now": {
                    "state": "done",
                    "working_tree": "clean",
                    "branch_upstream_ahead": 0,
                    "branch_upstream_behind": 0,
                    "origin_main_ahead": 1,
                    "origin_main_behind": 0,
                },
                "github": {"open_pr_count": 0, "error": None},
                "residual_count": 0,
                "residual": [],
                "next": [],
            },
            "safety_boundary": {
                "raw_discord_text_output": "omitted",
                "outbound_actions": "disabled",
            },
        },
    )

    payload = repo_goal_status.build_goal_status()

    assert payload["ok"] is False
    assert payload["state"] == "attention_required"
    assert {item["name"]: item["status"] for item in payload["requirements"]}["repo_sync_clean"] == "blocked"


def test_repo_goal_status_blocks_dirty_or_open_pr(monkeypatch):
    import repo_goal_status

    monkeypatch.setattr(
        repo_goal_status.discord_inventory_dashboard,
        "build_inventory",
        lambda channel_dir, tmp_dir, store_limit: {
            "ok": False,
            "operational_status": {
                "now": {
                    "state": "attention_required",
                    "working_tree": "dirty",
                    "origin_main_ahead": 1,
                    "origin_main_behind": 0,
                },
                "github": {"open_pr_count": 1, "error": None},
                "residual_count": 2,
                "residual": [{"code": "working_tree_dirty"}, {"code": "open_pull_requests"}],
                "next": ["review open PR"],
            },
            "safety_boundary": {
                "raw_discord_text_output": "omitted",
                "outbound_actions": "disabled",
            },
        },
    )

    payload = repo_goal_status.build_goal_status()

    assert payload["ok"] is False
    assert payload["state"] == "attention_required"
    assert {item["name"]: item["status"] for item in payload["requirements"]}["repo_sync_clean"] == "blocked"
    assert {item["name"]: item["status"] for item in payload["requirements"]}["no_open_pr"] == "blocked"


def test_repo_goal_status_includes_ops_smoke_failure(monkeypatch):
    import repo_goal_status

    monkeypatch.setattr(
        repo_goal_status.discord_inventory_dashboard,
        "build_inventory",
        lambda channel_dir, tmp_dir, store_limit: {
            "ok": True,
            "operational_status": {
                "now": {
                    "state": "done",
                    "working_tree": "clean",
                    "origin_main_ahead": 0,
                    "origin_main_behind": 0,
                },
                "github": {"open_pr_count": 0, "error": None},
                "residual_count": 0,
                "residual": [],
                "next": [],
            },
            "safety_boundary": {
                "raw_discord_text_output": "omitted",
                "outbound_actions": "disabled",
            },
        },
    )
    monkeypatch.setattr(
        repo_goal_status,
        "run_smoke",
        lambda: {
            "command": "python scripts/ops_check.py",
            "exit_code": 2,
            "ok": False,
            "stdout": "failed",
            "stderr": "",
        },
    )

    payload = repo_goal_status.build_goal_status(run_ops_smoke=True)

    assert payload["ok"] is False
    assert payload["smoke"]["ok"] is False
    assert {item["name"]: item["status"] for item in payload["requirements"]}["ops_smoke"] == "blocked"


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
    assert result["cleanup_elapsed_ms"] >= 0
    assert result["text_output"] == "omitted"


def test_ops_check_accepts_explicit_skip_http_flag():
    args = ops_check.parse_args(["--skip-http"])

    assert args.skip_http is True
    assert args.http is False


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
