import importlib.util
import json
import os
from pathlib import Path
import sys
import tomllib

import pytest

from discord_context_bridge import (
    DisabledCapability,
    DiscordEvent,
    acquisition_context_for_source,
    append_event,
    audit_context_store,
    audit_event_store,
    build_attachment_ledger,
    build_copy_block,
    build_discord_post_send_closeout_packet,
    build_discord_send_staging_packet,
    build_discord_send_operation_status,
    build_cache_first_intake,
    build_coverage_report,
    build_handoff_packet,
    build_human_gate,
    build_latest_snapshot_report,
    build_bridge_intake,
    build_url_intake_fast_path,
    build_review_artifact_markdown,
    build_reply_context_gate,
    build_reply_context_gate_from_messages,
    write_attachment_ocr_log,
    build_url_intake_gate,
    context_passport_from_text,
    context_operating_mode_from_text,
    digest_context_from_text,
    fast_briefing,
    get_context_document,
    get_review_state,
    guide_reply_from_text,
    import_visible_text,
    load_text_snapshots,
    list_context_documents,
    load_jsonl_records,
    load_review_registry,
    load_events,
    ops_view_summary,
    parse_visible_text,
    plan_discord_url_read,
    plan_full_thread_capture,
    rest_backfill_config_safety,
    review_reply_intent,
    send_message,
    snapshot_visible_text,
    status_dashboard,
    upsert_review_state,
    upsert_context_document,
    verify_chrome_extension_fill_only_dry_run,
    analyze_discord_forum_url_shape,
    write_rest_backfill_capture,
    target_key_for_url,
)
from discord_context_bridge import cli as cli_module
from discord_context_bridge.cli import build_parser
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.core import resolve_context_bindings
from discord_context_bridge import mcp_server

FIXTURE = Path(__file__).parent / "fixtures" / "visible_text.txt"
RICH_COPY_FIXTURE = Path(__file__).parent / "fixtures" / "discord_rich_copy.txt"
PASSPORT_FIXTURE = Path(__file__).parent / "fixtures" / "thread_context_passport.txt"
SERVER_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "server_context.txt"
CHANNEL_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "channel_context.txt"
THREAD_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "thread_context_rules.txt"
ROOT = Path(__file__).resolve().parents[1]


def load_bump_version_module():
    spec = importlib.util.spec_from_file_location("bump_version", ROOT / "scripts" / "bump_version.py")
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_visible_text_supports_copied_blocks():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "Can you clarify the premise before we reply?"
    assert events[2].text_snippet == "I think the reply should mention the audience."
    assert all(event.actions_allowed == ["read"] for event in events)


def test_parse_visible_text_supports_discord_rich_copy_metadata():
    events = parse_visible_text(RICH_COPY_FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "前提を確認してから返事したいです。 公開時期の話ですよね。"
    assert events[1].text_snippet == "そうです。相手に伝わる言い方にしたいです。"
    assert events[2].text_snippet == "返信前に文脈を揃えましょう。"


def test_import_visible_text_appends_and_deduplicates(tmp_path):
    store = tmp_path / "events.ndjson"

    first = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)
    second = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)
    stored_text = store.read_text(encoding="utf-8")
    stored_events = load_events(store)

    assert first["parsed"] == 3
    assert first["appended"] == 3
    assert first["language"] == "ja"
    assert first["message"] == "Discord の可視テキストを取り込みました。"
    assert first["preview"][0]["source_label"] == "Discord の可視テキスト"
    assert first["preview"][0]["actions_allowed_label"] == "読み取りのみ"
    assert second["duplicate"] == 3
    assert len(stored_events) == 3
    assert [event.author_label for event in stored_events] == [
        "participant-001",
        "participant-002",
        "participant-003",
    ]
    assert {event.text_snippet for event in stored_events} == {"omitted"}
    assert "Can you clarify" not in stored_text
    assert "member-a" not in stored_text


def test_import_visible_text_dry_run_previews_without_writing(tmp_path):
    store = tmp_path / "events.ndjson"

    result = import_visible_text(RICH_COPY_FIXTURE.read_text(encoding="utf-8"), path=store, dry_run=True)

    assert result["dry_run"] is True
    assert result["parsed"] == 3
    assert result["appended"] == 0
    assert result["message"] == "保存せずに取り込み結果を確認しました。"
    assert result["preview"][0]["author_label"] == "member-a"
    assert result["briefing"]["event_count"] == 3
    assert not store.exists()


def test_plan_discord_url_read_keeps_browser_visible_boundary():
    plan = plan_discord_url_read("https://discord.com/channels/1464854485779218535/1515372533442937013")

    assert plan["ok_to_open"] is True
    assert plan["read_method"] == "browser_visible_text"
    assert plan["private_local_only"] is True
    assert plan["external_share_allowed"] is False
    assert plan["outbound_actions"] == "disabled"


def test_snapshot_visible_text_appends_every_observation(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"

    first = snapshot_visible_text(
        text="member-a: visible hello",
        url="https://discord.com/channels/1/2/3",
        path=snapshot_store,
    )
    second = snapshot_visible_text(
        text="member-a: visible hello",
        url="https://discord.com/channels/1/2/3",
        path=snapshot_store,
    )

    assert first["saved"] is True
    assert first["changed"] is True
    assert first["duplicate_content"] is False
    assert second["saved"] is True
    assert second["changed"] is False
    assert second["duplicate_content"] is True
    assert second["snapshot_count_for_target"] == 2
    assert second["observation_index_for_target"] == 2
    stored = load_text_snapshots(snapshot_store)
    assert len(stored) == 2
    assert stored[0]["schema"] == "discord_context_bridge_text_snapshot_observation.v1"
    assert stored[0]["event_type"] == "discord.visible_text.snapshot_observed"
    assert stored[0]["stream_id"] == stored[0]["target_key"]
    assert stored[0]["stream_sequence"] == 1
    assert stored[0]["expected_previous_stream_sequence"] == 0
    assert stored[0]["specversion"] == "1.0"
    assert stored[0]["type"] == "discord.visible_text.snapshot_observed"
    assert stored[0]["subject"] == stored[0]["target_key"]
    assert stored[0]["time"] == stored[0]["captured_at"]
    assert stored[0]["datacontenttype"] == "text/plain; charset=utf-8"
    assert stored[0]["dataschema"] == "discord_context_bridge_text_snapshot_observation.v1"
    assert stored[0]["event_id"]
    assert stored[0]["previous_event_hash"] == ""
    assert stored[0]["event_hash"]
    assert stored[0]["private_local_only"] is True
    assert stored[1]["stream_sequence"] == 2
    assert stored[1]["expected_previous_stream_sequence"] == 1
    assert stored[1]["previous_content_hash"] == stored[0]["content_hash"]
    assert stored[1]["previous_event_hash"] == stored[0]["event_hash"]
    assert stored[1]["event_hash"] != stored[0]["event_hash"]
    assert stored[1]["duplicate_content"] is True


def test_snapshot_visible_text_uses_legacy_count_as_previous_sequence(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    url = "https://discord.com/channels/1/2/3"
    legacy = {
        "captured_at": "2026-07-01T00:00:00Z",
        "source": "manual_visible_text",
        "url": url,
        "title": "",
        "target_key": target_key_for_url(url),
        "content_hash": "legacy-hash",
        "text": "member-a: old visible text",
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
    }
    snapshot_store.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")

    result = snapshot_visible_text(
        text="member-a: new visible text",
        url=url,
        path=snapshot_store,
    )

    stored = load_text_snapshots(snapshot_store)
    assert result["observation_index_for_target"] == 2
    assert stored[1]["stream_sequence"] == 2
    assert stored[1]["expected_previous_stream_sequence"] == 1
    assert stored[1]["previous_event_hash"]


def test_snapshot_visible_text_saves_acquisition_context(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"

    snapshot_visible_text(
        text="member-a: visible hello",
        url="https://discord.com/channels/1/2/3",
        source="chrome_extension_dom",
        path=snapshot_store,
    )
    stored = load_text_snapshots(snapshot_store)[0]

    assert stored["acquisition_context"]["source_kind"] == "Chrome DOM"
    assert stored["acquisition_context"]["capture_method"] == "chrome_dom_visible_range"
    assert stored["acquisition_context"]["live_browser_access_required_for_capture"] is True
    assert stored["acquisition_context"]["token_or_cookie_read"] is False


def test_build_latest_snapshot_report_is_metadata_only_by_default(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    secret_text = "member-a: private launch wording should not leak"
    snapshot_visible_text(
        text=secret_text,
        url="https://discord.com/channels/1/2/3",
        source="chrome_extension_dom",
        path=snapshot_store,
    )

    report = build_latest_snapshot_report(path=snapshot_store)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["ok"] is True
    assert report["raw_text_returned"] is False
    assert report["participant_names_returned"] is False
    assert report["local_paths_returned"] is False
    assert report["latest_saved_or_visible_message"]["speaker"] == "omitted"
    assert report["latest_saved_or_visible_message"]["body_evidence"]["preview"] == "omitted"
    assert report["source_summary"]["source_kind"] == "Chrome DOM"
    assert report["source_summary"]["stored_acquisition_context"]["live_browser_access_required_for_capture"] is True
    assert report["source_summary"]["report_acquisition_context"]["mode"] == "existing_saved_snapshot"
    assert report["source_summary"]["report_acquisition_context"]["live_browser_access"] is False
    assert report["source_summary"]["report_acquisition_context"]["new_capture"] is False
    assert "private launch wording" not in encoded
    assert "member-a" not in encoded
    assert str(snapshot_store) not in encoded


def test_build_latest_snapshot_report_can_include_bounded_preview(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    snapshot_visible_text(
        text="member-a: short local-only preview source",
        url="https://discord.com/channels/1/2/3",
        source="visible_text",
        path=snapshot_store,
    )

    report = build_latest_snapshot_report(path=snapshot_store, include_preview=True)
    evidence = report["latest_saved_or_visible_message"]["body_evidence"]

    assert report["raw_text_returned"] is False
    assert evidence["full_content_returned"] is False
    assert evidence["preview_returned"] is True
    assert evidence["preview_char_count"] <= 24
    assert evidence["preview"] == "member-a: short local-on"
    assert "source" not in evidence["preview"]


def test_build_latest_snapshot_report_missing_snapshot_is_safe(tmp_path):
    report = build_latest_snapshot_report(path=tmp_path / "missing.ndjson")

    assert report["ok"] is False
    assert report["reason"] == "snapshot_missing"
    assert report["raw_text_returned"] is False
    assert report["report_acquisition_context"]["mode"] == "existing_saved_snapshot"
    assert report["report_acquisition_context"]["live_browser_access"] is False


def test_attachment_ledger_keeps_urls_and_local_paths_out_of_output(tmp_path):
    source = tmp_path / "raw.ndjson"
    ledger = tmp_path / "attachment-ledger.md"
    source.write_text(
        json.dumps(
            {
                "message_id": "123456789012345678",
                "timestamp": "2026-07-03T00:00:00+00:00",
                "content": "fixture message body",
                "attachments": [
                    {
                        "id": "223456789012345678",
                        "filename": "screen.png",
                        "content_type": "image/png",
                        "url": "https://example.invalid/attachments/screen.png?ex=secret",
                        "width": 640,
                        "height": 480,
                        "size": 1200,
                        "local_path": str(tmp_path / "screen.png"),
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_attachment_ledger(records=load_jsonl_records(source), output=ledger)
    rendered = json.dumps(payload, ensure_ascii=False) + ledger.read_text(encoding="utf-8")

    assert payload["schema"] == "discord_attachment_ledger.v1"
    assert payload["attachment_count"] == 1
    assert payload["entries"][0]["attachment_type"] == "image"
    assert payload["entries"][0]["local_status"] == "missing"
    assert payload["raw_url_returned"] is False
    assert payload["local_paths_returned"] is False
    assert "example.invalid" not in rendered
    assert "123456789012345678" not in rendered
    assert str(tmp_path) not in rendered
    assert "fixture message body" not in rendered


def test_attachment_ocr_log_writes_private_artifact_without_stdout_text(tmp_path):
    output = tmp_path / "ocr.md"
    payload = write_attachment_ocr_log(
        source_key="safe-source",
        ocr_text="fixture OCR text saved in the local artifact",
        output=output,
        note_label="safe-note",
    )
    stdout_payload = json.dumps(payload, ensure_ascii=False)
    artifact = output.read_text(encoding="utf-8")

    assert payload["schema"] == "discord_attachment_ocr_log.v1"
    assert payload["created"] is True
    assert payload["raw_text_returned"] is False
    assert payload["local_paths_returned"] is False
    assert "fixture OCR text" not in stdout_payload
    assert "fixture OCR text saved in the local artifact" in artifact
    assert str(tmp_path) not in stdout_payload


def test_discord_forum_url_shape_reports_parent_channel_presence():
    ready = analyze_discord_forum_url_shape("https://discord.com/channels/1/10/20")
    missing_parent = analyze_discord_forum_url_shape("https://discord.com/channels/1/20")

    assert ready["valid_discord_channel_url"] is True
    assert ready["parent_channel_id_present"] is True
    assert ready["thread_id_present"] is True
    assert ready["blocked_reason"] == ""
    assert missing_parent["parent_channel_id_present"] is False
    assert missing_parent["blocked_reason"] == "forum_parent_channel_missing"
    assert missing_parent["same_guild_fuzzy_match_allowed"] is False


def test_url_intake_gate_reports_ai_log_stale_and_syncs(tmp_path):
    url = "https://discord.com/channels/1/10/20"
    key = target_key_for_url(url)
    raw_cache = tmp_path / "raw.ndjson"
    ai_log = tmp_path / "ai.ndjson"
    raw_cache.write_text(
        json.dumps(
            {
                "url": url,
                "target_key": key,
                "text": "member-a: local raw cache text",
                "captured_at": "2026-07-03T00:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    stale = build_url_intake_gate(
        url,
        raw_cache_path=raw_cache,
        ai_log_path=ai_log,
        generated_at="2026-07-03T00:00:00+00:00",
    )
    synced = build_url_intake_gate(
        url,
        raw_cache_path=raw_cache,
        ai_log_path=ai_log,
        sync=True,
        generated_at="2026-07-03T00:00:00+00:00",
    )

    assert stale["state"] == "ai_log_stale"
    assert stale["exact_coverage"] == "no"
    assert stale["recency"]["reason"] == "raw_cache_exact_match_but_ai_log_missing"
    assert stale["stale_policy"]["fallback_allowed"] == "manual_visible_text_or_chrome_extension_only"
    assert synced["state"] == "ready"
    assert synced["exact_coverage"] == "yes"
    assert synced["raw_text_returned"] is False
    assert "member-a" not in json.dumps(synced, ensure_ascii=False)


def test_url_intake_gate_splits_forum_parent_missing_from_raw_cache_miss(tmp_path):
    url = "https://discord.com/channels/1/20"
    raw_cache = tmp_path / "raw.ndjson"
    raw_cache.write_text('{"url": "https://discord.com/channels/1/other", "text": "other"}\n', encoding="utf-8")

    payload = build_url_intake_gate(
        url,
        raw_cache_path=raw_cache,
        ai_log_path=tmp_path / "ai.ndjson",
        generated_at="2026-07-03T00:00:00+00:00",
    )

    assert payload["state"] == "raw_cache_missing"
    assert payload["blocked_reason"] == "forum_parent_channel_missing"
    assert payload["url_shape"]["parent_channel_id_present"] is False
    assert payload["url_shape"]["same_guild_fuzzy_match_allowed"] is False


def test_url_intake_fast_path_stops_at_missing_snapshot_without_text_tools(tmp_path):
    url = "https://discord.com/channels/1/10/20"
    key = target_key_for_url(url)
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    snapshot_store.write_text("", encoding="utf-8")

    payload = build_url_intake_fast_path(
        url=url,
        snapshot_store=snapshot_store,
        target_key=key,
        hook_snapshot_status="snapshot_missing",
        generated_at="2026-07-05T00:00:00+00:00",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "discord_url_intake_fast_path.v1"
    assert payload["decision"] == "need_visible_text"
    assert payload["recommended_command"] == "report-latest"
    assert payload["text_required_tools_allowed"] is False
    assert payload["next_step"] == "ask_for_visible_text_or_paste"
    assert payload["operations"]["discord_outbound_actions"] == "disabled"
    assert payload["target"]["target_key"] == key
    assert str(snapshot_store) not in rendered
    assert url not in rendered


def test_bridge_intake_blocks_when_snapshot_missing(tmp_path):
    url = "https://discord.com/channels/1/10/30"
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    snapshot_store.write_text("", encoding="utf-8")

    payload = build_bridge_intake(
        url=url,
        snapshot_store=snapshot_store,
        generated_at="2026-07-09T00:00:00+00:00",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "discord_bridge_intake.v1"
    assert payload["decision"] == "need_visible_text"
    assert payload["blocked_reason"] == "snapshot_missing"
    assert payload["context_passport"]["built"] is False
    assert payload["guide_reply"]["built"] is False
    assert "coverage" in payload["pipeline"]["completed"]
    assert payload["raw_text_returned"] is False
    assert str(snapshot_store) not in rendered
    assert url not in rendered


def test_bridge_intake_runs_snapshot_coverage_passport_and_optional_guide(tmp_path):
    url = "https://discord.com/channels/1/10/31"
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    private_text = "member-bridge: 次の前提を確認してから進めたいです。ルールは短縮です。"
    draft = "まず前提を確認してから返事します。"

    payload = build_bridge_intake(
        url=url,
        snapshot_store=snapshot_store,
        text=private_text,
        draft=draft,
        understanding_confirmed=True,
        generated_at="2026-07-09T00:00:00+00:00",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["decision"] == "guide_ready"
    assert payload["pipeline"]["completed"] == [
        "snapshot",
        "coverage",
        "context_passport",
        "guide_reply",
    ]
    assert payload["snapshot"]["saved"] is True
    assert payload["coverage"]["exact_coverage"] is True
    assert payload["context_passport"]["built"] is True
    assert payload["context_passport"]["parsed"] >= 1
    assert payload["guide_reply"]["built"] is True
    assert payload["guide_reply"]["send_capability"] == "disabled"
    assert payload["outbound_actions"] == "disabled"
    assert payload["raw_text_returned"] is False
    assert private_text not in rendered
    assert "member-bridge" not in rendered
    assert draft not in rendered
    assert url not in rendered
    assert str(snapshot_store) not in rendered

    records = load_jsonl_records(snapshot_store)
    assert len(records) == 1
    assert records[0]["text"] == private_text


def test_cli_bridge_intake_metadata_only_end_to_end(tmp_path, capsys):
    url = "https://discord.com/channels/1/10/32"
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    visible = tmp_path / "visible.txt"
    private_text = "member-cli: 実装前に前提を揃えたいです。"
    visible.write_text(private_text, encoding="utf-8")

    result = cli_main(
        [
            "bridge-intake",
            "--url",
            url,
            "--snapshot-store",
            str(snapshot_store),
            "--input",
            str(visible),
            "--draft",
            "前提を確認してから進めます。",
            "--understanding-confirmed",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["schema"] == "discord_bridge_intake.v1"
    assert payload["decision"] == "guide_ready"
    assert payload["pipeline"]["completed"][-1] == "guide_reply"
    assert private_text not in output
    assert "member-cli" not in output
    assert url not in output
    assert str(snapshot_store) not in output


def test_cli_url_intake_fast_path_outputs_metadata_only_missing_snapshot(tmp_path, capsys):
    url = "https://discord.com/channels/1/10/20"
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    snapshot_store.write_text("", encoding="utf-8")

    result = cli_main(
        [
            "url-intake-fast-path",
            "--url",
            url,
            "--snapshot-store",
            str(snapshot_store),
            "--hook-snapshot-status",
            "snapshot_missing",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 2
    assert payload["decision"] == "need_visible_text"
    assert payload["raw_text_returned"] is False
    assert payload["paths_output"] == "omitted"
    assert str(snapshot_store) not in output
    assert url not in output


def test_coverage_report_marks_stale_snapshot_recency_and_policy(tmp_path):
    url = "https://discord.com/channels/1/10/20"
    key = target_key_for_url(url)
    ai_log = tmp_path / "ai.ndjson"
    ai_log.write_text(
        json.dumps(
            {
                "url": url,
                "target_key": key,
                "text": "member-a: saved snapshot text",
                "captured_at": "2026-07-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_coverage_report(
        url=url,
        raw_cache_path=tmp_path / "raw.ndjson",
        ai_log_path=ai_log,
        generated_at="2026-07-03T00:00:00+00:00",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["coverage"]["exact_coverage"] is True
    assert payload["freshness"]["status"] == "stale"
    assert payload["stale_policy"]["usable_for_reply"] is False
    assert payload["stale_policy"]["usable_for_routing"] is True
    assert payload["stale_policy"]["required_action"] == "refresh_exact_url_snapshot"
    assert payload["url_shape"]["parent_channel_id_present"] is True
    assert "member-a" not in rendered
    assert url not in rendered


def test_acquisition_context_for_source_classifies_local_routes():
    assert acquisition_context_for_source("discord_app_cache")["source_kind"] == "cache"
    assert acquisition_context_for_source("source-command")["capture_method"] == "local_source_command_stdout"
    assert acquisition_context_for_source("clipboard")["private_adapter_required_for_capture"] is False


def test_audit_event_store_reports_safe_empty_store(tmp_path):
    report = audit_event_store(tmp_path / "events.ndjson")

    assert report["safe_for_tunnel"] is True
    assert report["safe_for_tunnel_label"] == "外部公開前の監査を通過しました。"
    assert report["event_count"] == 0
    assert report["issues"] == []


def test_audit_event_store_flags_private_identifiers(tmp_path):
    store = tmp_path / "events.ndjson"
    append_event(
        DiscordEvent(
            observed_at="2026-01-01T00:00:00+00:00",
            source="test",
            guild_label="example-community",
            channel_label="general",
            author_label="member-a",
            text_snippet="webhook is https://discord.com/api/webhooks/123456789012345678/token",
        ),
        store,
    )
    append_event(
        DiscordEvent(
            observed_at="2026-01-01T00:00:01+00:00",
            source="test",
            guild_label="example-community",
            channel_label="general",
            author_label="member-b",
            text_snippet="user id 987654321098765432 should not be stored",
        ),
        store,
    )
    append_event(
        DiscordEvent(
            observed_at="2026-01-01T00:00:02+00:00",
            source="test",
            guild_label="example-community",
            channel_label="general",
            author_label="member-c",
            text_snippet="local path /Users/example/Library/Application Support/Discord",
        ),
        store,
    )

    report = audit_event_store(store)

    assert report["safe_for_tunnel"] is False
    assert report["event_count"] == 3
    assert [issue["kind"] for issue in report["issues"]] == [
        "discord_webhook_url",
        "discord_snowflake_id",
        "local_absolute_path",
    ]


def test_ops_view_summary_omits_message_text_and_reports_boundary(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    summary = ops_view_summary(store)

    assert summary["message"] == "運用ログ表示を作成しました。"
    assert summary["safe_labels"] == ["safe-general"]
    assert summary["event_count"] == 3
    assert summary["delta_count"] == 3
    assert summary["last_seen"]
    assert summary["gate_verdict"] == "pass"
    assert summary["outbound"] == "disabled"
    assert "このツールから Discord へ送信しません。" == summary["outbound_label"]
    assert "text_snippet" not in summary
    assert "authors" not in summary


def test_status_dashboard_splits_operational_state_without_private_text(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    dashboard = status_dashboard(store, github_state="merged")

    assert dashboard["schema"] == "discord_bridge_status_dashboard.v1"
    assert dashboard["now"]["context_available"] is True
    assert dashboard["now"]["event_count"] == 3
    assert dashboard["broken"] == []
    assert dashboard["blocked"] == []
    assert dashboard["github"]["state"] == "merged"
    assert dashboard["safety_boundary"]["outbound_actions"] == "disabled"
    assert dashboard["safety_boundary"]["raw_text_included"] is False
    assert "Can you clarify" not in str(dashboard)
    assert "member-a" not in str(dashboard)
    assert str(tmp_path) not in str(dashboard)


def test_status_dashboard_reports_missing_context_without_local_path(tmp_path):
    dashboard = status_dashboard(tmp_path / "missing.ndjson")

    assert dashboard["now"]["context_available"] is False
    assert dashboard["blocked"] == ["Discord 可視本文はまだ取り込まれていません。"]
    assert dashboard["next"] == ["private adapter probe または import-visible-text を実行します。"]
    assert str(tmp_path) not in str(dashboard)


def test_fast_briefing_and_reply_review(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    briefing = fast_briefing(load_events(store))
    review = review_reply_intent(
        "I understand this is about launch timing and my reply will ask for the premise.",
        load_events(store),
        understanding_confirmed=True,
    )

    assert briefing["event_count"] == 3
    assert briefing["language"] == "ja"
    assert briefing["message"] == "直近文脈の短い要約を作成しました。"
    assert briefing["partial_label"] == "直近の一部だけを使った要約です。"
    assert review["language"] == "ja"
    assert review["message"] == "返信前レビューが完了しました。"
    assert review["ok_to_reply"] == "likely_ok"
    assert review["ok_to_reply_label"] == "返信してよさそうです。"
    assert review["send_capability"] == "disabled"
    assert review["alignment_label"] == "文脈に合っています。"
    assert review["topic_warning_label"] == "話題の大きなズレは見つかりません。"


def test_guide_reply_from_text_returns_conversation_guide():
    guide = guide_reply_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "公開時期の前提を確認してから返信します。",
        understanding_confirmed=True,
    )

    assert guide["language"] == "ja"
    assert guide["message"] == "Discord 返信ガイドを作成しました。"
    assert guide["parsed"] == 3
    assert "公開時期の話ですよね" in guide["counterparty_context"]
    assert guide["reply_review"]["ok_to_reply_label"] == "返信してよさそうです。"
    assert guide["reply_review"]["quick_verdict"] == "go"
    assert guide["reply_review"]["quick_verdict_label"].startswith("go:")
    assert guide["send_capability_label"] == "このツールから Discord へ送信しません。"
    assert "文脈と返信意図は大きくずれていません。" in guide["next_actions"]


def test_digest_context_from_text_returns_safe_chewing_packet():
    digestion = digest_context_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        focus="公開時期",
    )
    rendered = json.dumps(digestion, ensure_ascii=False)

    assert digestion["schema"] == "discord_context_digestion.v1"
    assert digestion["mode"] == "chew"
    assert digestion["parsed"] == 3
    assert digestion["focus"] == "公開時期"
    assert digestion["focus_matched"] is True
    assert digestion["safety_boundary"]["raw_text_returned"] is False
    assert digestion["safety_boundary"]["participant_names_returned"] is False
    assert digestion["send_capability"] == "disabled"
    assert any(layer.startswith("目的:") for layer in digestion["understanding_layers"])
    assert "member-a" not in rendered
    assert "公開時期の話ですよね" not in rendered


@pytest.mark.parametrize(
    ("mode", "expected_route"),
    [
        ("triage", "chew_context"),
        ("catchup", "caught_up_for_light_entry"),
        ("join-thread", "read_then_join"),
        ("boundary", "safe_to_continue"),
    ],
)
def test_context_operating_modes_return_safe_packets(mode, expected_route):
    snowflake = "1" * 18
    discord_url = "https://discord.com/" + f"channels/{snowflake}/{'2' * 18}"
    local_path = "/" + "Users" + "/example/private.txt"
    packet = context_operating_mode_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        mode=mode,
        focus=f"公開時期 {discord_url} {local_path}",
    )
    rendered = json.dumps(packet, ensure_ascii=False)

    assert packet["schema"] == "discord_context_operating_mode.v1"
    assert packet["mode"] == mode
    assert packet["route"] == expected_route
    assert packet["safety_boundary"]["raw_text_returned"] is False
    assert packet["safety_boundary"]["participant_names_returned"] is False
    assert packet["send_capability"] == "disabled"
    assert packet["checklist"]
    assert packet["next_actions"]
    assert "member-a" not in rendered
    assert "公開時期の話ですよね" not in rendered
    assert "discord.com/channels" not in rendered
    assert snowflake not in rendered
    assert local_path not in rendered


def test_join_thread_mode_holds_without_visible_context():
    packet = context_operating_mode_from_text("", mode="join-thread")

    assert packet["route"] == "observe_only"
    assert packet["parsed"] == 0
    assert packet["send_capability"] == "disabled"


def test_guide_reply_warns_about_topic_mismatch():
    guide = guide_reply_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "価格について返信します。",
        understanding_confirmed=True,
    )

    assert guide["reply_review"]["ok_to_reply"] == "ask_first"
    assert guide["reply_review"]["quick_verdict"] == "ask-context"
    assert "話題がずれている可能性があります" in guide["reply_review"]["topic_warning_label"]
    assert "文脈は公開時期" in guide["reply_review"]["topic_warning_label"]
    assert "返信案は価格" in guide["reply_review"]["topic_warning_label"]
    assert guide["next_actions"][0] == guide["reply_review"]["topic_warning_label"]


def test_review_reply_intent_quick_verdict_waits_without_context():
    review = review_reply_intent("前提を確認してから返します。", [], understanding_confirmed=True)

    assert review["quick_verdict"] == "understanding-blocked"
    assert review["quick_verdict_label"].startswith("read-more:")
    assert review["send_capability"] == "disabled"


def test_review_reply_intent_quick_verdict_flags_risky_tone():
    events = parse_visible_text(PASSPORT_FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent("それは最悪です。ふざけないでください。", events, understanding_confirmed=True)

    assert review["quick_verdict"] == "risky"
    assert review["quick_verdict_label"].startswith("risky:")
    assert review["send_capability"] == "disabled"
    assert review["human_gate"]["human_decision_required"] is True
    assert review["human_gate"]["recommended_option"] == "wait"
    assert review["copy_block"]["status"] == "ready"


def test_review_reply_intent_returns_final_candidate_human_gate_and_copy_block():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent("公開時期の前提を確認して返信します。", events, understanding_confirmed=True)

    assert review["final_candidate"] == "公開時期の前提を確認して返信します。"
    assert review["human_gate"]["schema"] == "discord_human_gate.v1"
    assert review["human_gate"]["human_decision_required"] is True
    assert review["human_gate"]["decision"] == "pending"
    assert review["human_gate"]["recommended_option"] == "copy"
    assert review["human_gate"]["outbound_actions"] == "disabled"
    assert review["copy_block"]["schema"] == "discord_copy_block.v1"
    assert review["copy_block"]["status"] == "ready"
    assert review["copy_block"]["text"] == "公開時期の前提を確認して返信します。"
    assert review["copy_block"]["part_count"] == 1
    assert review["copy_block"]["outbound_actions"] == "disabled"


def test_build_discord_send_staging_packet_prepares_reply_without_send():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )

    assert packet["schema"] == "discord_send_staging_packet.v1"
    assert packet["staging_status"] == "ready_to_fill"
    assert packet["blockers"] == []
    assert packet["browser_action"]["capability"] == "chrome_extension_fill_only"
    assert "stop_before_send_button" in packet["browser_action"]["allowed_actions"]
    assert "click_send_button" in packet["browser_action"]["forbidden_actions"]
    assert not set(packet["browser_action"]["allowed_actions"]) & set(packet["browser_action"]["forbidden_actions"])
    assert packet["stage_guard"] == {
        "schema": "discord_fill_only_guard.v1",
        "max_runner_action": "fill_draft_only",
        "external_action": "none_until_human_send",
        "latest_target_snapshot_required": True,
        "latest_target_snapshot_check": "required_before_user_action",
        "stop_condition": "stop_before_send_button",
        "human_send_required": True,
    }
    assert packet["send_capability"] == "disabled"
    assert packet["outbound_actions"] == "disabled"
    assert packet["target_url_output"] == "omitted"
    serialized = json.dumps(packet, ensure_ascii=False)
    assert "123456789012345678" not in serialized


def test_build_discord_send_staging_packet_blocks_missing_reply_target():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678",
        understanding_confirmed=True,
    )

    assert packet["staging_status"] == "blocked"
    assert "reply_target_message_url_required" in packet["blockers"]
    assert packet["human_gate"]["recommended_option"] == "fix-blockers"


def test_build_discord_send_staging_packet_blocks_unverified_mention():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="mention",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )

    assert packet["staging_status"] == "blocked"
    assert "mention_label_required" in packet["blockers"]


def test_verify_chrome_extension_fill_only_dry_run_allows_unique_reply_ui():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )

    report = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )

    assert report["schema"] == "chrome_extension_fill_only_dry_run.v1"
    assert report["dry_run_status"] == "ready_to_fill"
    assert report["fill_permitted"] is True
    assert report["required_stop"] == "stop_before_send_button"
    assert report["outbound_actions"] == "disabled"
    assert "click_send_button" in report["forbidden_actions"]


def test_verify_chrome_extension_fill_only_dry_run_blocks_without_latest_snapshot():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )

    report = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )

    assert report["dry_run_status"] == "blocked"
    assert report["fill_permitted"] is False
    assert "latest_target_snapshot_not_confirmed" in report["blockers"]
    assert report["observed"]["latest_target_snapshot_confirmed"] is False


def test_verify_chrome_extension_fill_only_dry_run_blocks_ambiguous_reply_ui():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )

    report = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=2,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )

    assert report["dry_run_status"] == "blocked"
    assert report["fill_permitted"] is False
    assert "reply_ui_not_unique" in report["blockers"]
    assert report["allowed_next_action"] == "fix_blockers"


def test_build_discord_post_send_closeout_packet_closes_without_raw_discord_values():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )
    dry_run = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )

    closeout = build_discord_post_send_closeout_packet(
        staging_packet=packet,
        dry_run_report=dry_run,
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="human_edited_and_reviewed",
        unread_check_status="none_unread",
        observed_message_id="423456789012345678",
        observed_url="https://discord.com/channels/123456789012345678/223456789012345678/423456789012345678",
    )
    serialized = json.dumps(closeout, ensure_ascii=False)

    assert closeout["schema"] == "discord_post_send_closeout_packet.v1"
    assert closeout["closeout_status"] == "closed"
    assert closeout["blockers"] == []
    assert closeout["recommended_next_state"] == "done"
    assert closeout["unread_check_status"] == "none_unread"
    assert closeout["unread_signal_count"] == 0
    assert closeout["observed_message_id_output"] == "omitted"
    assert closeout["observed_url_output"] == "omitted"
    assert closeout["raw_discord_text_output"] == "omitted"
    assert closeout["text_returned"] is False
    assert closeout["send_capability"] == "disabled"
    assert closeout["outbound_actions"] == "disabled"
    assert "423456789012345678" not in serialized
    assert "discord.com/channels" not in serialized


def test_build_discord_post_send_closeout_packet_blocks_missing_human_observation():
    closeout = build_discord_post_send_closeout_packet(
        human_sent_observed=False,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
        unread_check_status="none_unread",
    )

    assert closeout["closeout_status"] == "blocked"
    assert "human_send_not_observed" in closeout["blockers"]
    assert closeout["recommended_next_state"] == "verify_visible_message"


def test_build_discord_post_send_closeout_packet_blocks_unread_items():
    closeout = build_discord_post_send_closeout_packet(
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
        unread_check_status="has_unread",
        unread_signal_count=2,
    )

    assert closeout["closeout_status"] == "blocked"
    assert "unread_items_remaining" in closeout["blockers"]
    assert closeout["recommended_next_state"] == "review_unread_items"
    assert closeout["unread_check_status"] == "has_unread"
    assert closeout["unread_signal_count"] == 2


def test_build_discord_post_send_closeout_packet_blocks_missing_unread_check():
    closeout = build_discord_post_send_closeout_packet(
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
    )

    assert closeout["closeout_status"] == "blocked"
    assert "unread_not_checked" in closeout["blockers"]
    assert closeout["recommended_next_state"] == "check_unread_items"


def test_build_discord_send_operation_status_summarizes_existing_logs():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )
    dry_run = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )
    closeout = build_discord_post_send_closeout_packet(
        staging_packet=packet,
        dry_run_report=dry_run,
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
        unread_check_status="none_unread",
        observed_message_id="423456789012345678",
        observed_url="https://discord.com/channels/123456789012345678/223456789012345678/423456789012345678",
    )

    status = build_discord_send_operation_status(
        staging_packet=packet,
        dry_run_report=dry_run,
        closeout_report=closeout,
        target_label="test-channel",
        rollback_plan_reviewed=True,
        production_runbook_fixed=True,
    )
    serialized = json.dumps(status, ensure_ascii=False)

    assert status["schema"] == "discord_send_operation_status.v1"
    assert status["ok"] is True
    assert status["state"] == "ready_for_production_human_send"
    assert status["summary"]["ready_count"] == 6
    assert status["target"]["label"] == "test-channel"
    assert status["safety_boundary"]["discord_send_executed_by_this_tool"] is False
    assert "discord.com/channels" not in serialized
    assert "423456789012345678" not in serialized


def test_build_discord_send_operation_status_shows_next_actions_when_logs_missing():
    status = build_discord_send_operation_status()

    assert status["ok"] is False
    assert status["state"] == "attention_required"
    assert status["summary"]["ready_count"] == 0
    blockers = {check["blocker"] for check in status["checks"] if check.get("blocker")}
    assert "target_label_missing" in blockers
    assert "dry_run_not_ready" in blockers
    assert "test_send_closeout_missing" in blockers


def test_review_reply_intent_blocks_draft_before_understanding_confirmation():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent("公開時期の前提を確認して返信します。", events)

    assert review["message"] == "文脈理解の確認待ちです。下書きと copy block は生成しません。"
    assert review["understanding_gate"]["schema"] == "discord_understanding_gate.v1"
    assert review["understanding_gate"]["status"] == "blocked"
    assert review["quick_verdict"] == "understanding-blocked"
    assert review["human_gate"]["recommended_option"] == "read-more"
    assert review["human_gate"]["options"] == ["understanding-ok", "read-more", "wrong-thread", "missing-rules", "stop"]
    assert review["final_candidate"] == ""
    assert review["copy_block"]["status"] == "blocked"
    assert review["copy_block"]["text"] == ""
    assert review["copy_block"]["parts"] == []
    assert review["send_capability"] == "disabled"


def test_reply_context_gate_requires_root_target_and_ten_prior_messages():
    ready = build_reply_context_gate(
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=10,
    )

    assert ready["schema"] == "discord_reply_context_gate.v1"
    assert ready["state"] == "ready"
    assert ready["reply_generation_allowed"] is True
    assert ready["minimum_prior_messages"] == 10


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"thread_root_present": False}, "thread_root_missing"),
        ({"reply_target_present": False}, "reply_target_missing"),
        ({"prior_message_count": 9}, "minimum_prior_messages_missing"),
    ],
)
def test_reply_context_gate_fails_closed_when_required_context_is_missing(overrides, reason):
    args = {
        "thread_root_present": True,
        "reply_target_present": True,
        "prior_message_count": 10,
    }
    args.update(overrides)

    gate = build_reply_context_gate(**args)

    assert gate["state"] in {"blocked", "expand_required"}
    assert gate["reply_generation_allowed"] is False
    assert reason in gate["blockers"]
    assert gate["outbound_actions"] == "disabled"


def test_reply_context_gate_allows_short_thread_only_after_history_end_is_confirmed():
    gate = build_reply_context_gate(
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=4,
        history_exhausted=True,
    )

    assert gate["state"] == "ready_short_thread"
    assert gate["reply_generation_allowed"] is True
    assert gate["history_exhausted"] is True


def test_reply_context_gate_expands_by_ten_for_unresolved_references():
    gate = build_reply_context_gate(
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=10,
        unresolved_reference_count=1,
        fetched_message_count=11,
        max_context_messages=30,
    )

    assert gate["state"] == "expand_required"
    assert gate["next_fetch_limit"] == 21
    assert "unresolved_references" in gate["blockers"]


def test_reply_context_gate_stops_at_limit_with_human_readable_reason():
    gate = build_reply_context_gate(
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=29,
        unresolved_reference_count=1,
        fetched_message_count=30,
        max_context_messages=30,
    )

    assert gate["state"] == "blocked"
    assert gate["reason_code"] == "reply_context_limit_reached"
    assert "上限" in gate["message"]
    assert gate["reply_generation_allowed"] is False


def test_reply_context_gate_from_structured_messages_preserves_relationships():
    messages = [{"text": "root", "is_thread_root": True}]
    messages.extend({"text": f"prior-{index}"} for index in range(9))
    messages.append({"text": "target", "is_reply_target": True})

    gate = build_reply_context_gate_from_messages(messages)

    assert gate["state"] == "ready"
    assert gate["prior_message_count"] == 10
    assert gate["structured_message_count"] == 11
    assert gate["relationship_metadata_preserved"] is True


def test_reply_context_gate_from_flat_messages_fails_closed():
    gate = build_reply_context_gate_from_messages([{"text": "本文"}] * 11)

    assert gate["reply_generation_allowed"] is False
    assert "thread_root_missing" in gate["blockers"]
    assert "reply_target_missing" in gate["blockers"]


def test_review_reply_intent_enforced_gate_blocks_even_after_understanding_confirmation():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    gate = build_reply_context_gate(
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=9,
    )

    review = review_reply_intent(
        "前提を確認してから返します。",
        events,
        understanding_confirmed=True,
        reply_context_gate=gate,
    )

    assert review["quick_verdict"] == "reply-context-blocked"
    assert review["final_candidate"] == ""
    assert review["copy_block"]["status"] == "blocked"
    assert review["reply_context_gate"]["reply_generation_allowed"] is False


def test_cli_reply_context_plan_is_metadata_only_and_ready(capsys):
    result = cli_main(
        [
            "reply-context-plan",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count",
            "10",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["state"] == "ready"
    assert payload["raw_text_returned"] is False
    assert payload["outbound_actions"] == "disabled"


def test_cli_review_draft_fails_closed_without_reply_context_flags(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    append_event(parse_visible_text(FIXTURE.read_text(encoding="utf-8"))[0], store)

    result = cli_main(
        [
            "--store",
            str(store),
            "review-draft",
            "--draft",
            "前提を確認してから返します。",
            "--understanding-confirmed",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["quick_verdict"] == "reply-context-blocked"
    assert payload["copy_block"]["status"] == "blocked"
    assert payload["final_candidate"] == ""


def test_copy_block_splits_once_and_blocks_three_or_more_parts():
    split = build_copy_block("あ" * 2100, max_chars=2000)
    blocked = build_copy_block("あ" * 4100, max_chars=2000)

    assert split["status"] == "split"
    assert split["split_required"] is True
    assert split["part_count"] == 2
    assert all(len(part) <= 2000 for part in split["parts"])
    assert blocked["status"] == "blocked"
    assert blocked["parts"] == []
    assert blocked["part_count"] == 3
    assert "3分割以上" in blocked["stop_reason"]
    assert blocked["outbound_actions"] == "disabled"


def test_human_gate_recommends_wait_read_more_edit_or_copy():
    assert build_human_gate(quick_verdict="wait", ok_to_reply="likely_ok", copy_block_status="ready")["recommended_option"] == "wait"
    assert build_human_gate(quick_verdict="risky", ok_to_reply="likely_ok", copy_block_status="ready")["recommended_option"] == "wait"
    assert build_human_gate(quick_verdict="ask-context", ok_to_reply="ask_first", copy_block_status="ready")["recommended_option"] == "read-more"
    assert build_human_gate(quick_verdict="go", ok_to_reply="likely_ok", copy_block_status="blocked")["recommended_option"] == "edit"
    assert build_human_gate(quick_verdict="go", ok_to_reply="likely_ok", copy_block_status="ready")["recommended_option"] == "copy"


def test_build_review_artifact_markdown_is_public_safe():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent(
        "member-a には https://example.com を貼らず、公開時期の前提を確認して返信します。",
        events,
        understanding_confirmed=True,
    )

    artifact = build_review_artifact_markdown(
        "member-a には https://example.com を貼らず、公開時期の前提を確認して返信します。",
        review,
    )

    assert "# Discord review artifact" in artifact
    assert "do_not_post" in artifact
    assert "## 5. human gate" in artifact
    assert "human_decision_required: true" in artifact
    assert "recommended_option: copy" in artifact
    assert "## 6. final candidate" in artifact
    assert "## 7. copy block" in artifact
    assert "status: ready" in artifact
    assert "raw_discord_text_output: omitted" in artifact
    assert "participant_names_output: omitted" in artifact
    assert "outbound_actions: disabled" in artifact
    assert "Can you clarify" not in artifact
    assert "member-a" not in artifact
    assert "https://example.com" not in artifact
    assert "[url omitted]" in artifact


def test_build_review_artifact_redacts_private_values():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent("前提を確認してから返します。", events, understanding_confirmed=True)
    artifact = build_review_artifact_markdown(
        "Webhook: https://discord.com/api/webhooks/123456789012345678/token at C:\\Users\\example\\secret",
        review,
    )

    assert "webhooks/123456789012345678" not in artifact
    assert "C:\\Users\\example" not in artifact
    assert "[discord webhook omitted]" in artifact
    assert "[local path omitted]" in artifact


def test_context_passport_from_text_summarizes_thread_context():
    passport = context_passport_from_text(PASSPORT_FIXTURE.read_text(encoding="utf-8"))

    assert passport["language"] == "ja"
    assert passport["message"] == "スレッド文脈カードを作成しました。"
    assert passport["parsed"] == 4
    assert "相談" in passport["thread_purpose"]
    assert "公開時期" in passport["thread_purpose"]
    assert "このチャンネルは公開前の企画相談用です" in passport["conversation_flow"]
    assert passport["rule_notes"]
    assert "未確認の断定は禁止" in passport["rule_notes"][0]
    assert passport["people_temperature"] == "serious"
    assert passport["context_ready"] is True
    assert passport["send_capability"] == "disabled"
    assert "断定せず、前提確認から入る。" in passport["natural_entry_angles"]


def test_context_passport_uses_explicit_server_channel_thread_context():
    passport = context_passport_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        server_context=SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        channel_context=CHANNEL_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        thread_context=THREAD_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
    )

    assert passport["external_context_used"] is True
    assert passport["context_sources"] == ["server_context", "channel_context", "thread_context"]
    assert "明示文脈を併用しています。" == passport["external_context_used_label"]
    assert "サーバー文脈" in passport["context_sources_label"]
    assert "チャンネル文脈" in passport["context_sources_label"]
    assert "スレッド文脈" in passport["context_sources_label"]
    assert "相談" in passport["thread_purpose"]
    assert "対象読者" in passport["thread_purpose"]
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])
    assert any("サーバー・チャンネル・スレッドの明示文脈" in action for action in passport["natural_entry_angles"])


def test_context_library_saves_audits_and_reuses_context(tmp_path):
    context_store = tmp_path / "context-library.json"

    saved = upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
    )
    listed = list_context_documents(context_store)
    audit = audit_context_store(context_store)
    loaded = get_context_document("server", "nexus", path=context_store)

    assert saved["message"] == "文脈庫を更新しました。"
    assert saved["created"] is True
    assert saved["entry"]["key"] == "nexus"
    assert "text" not in saved["entry"]
    assert listed["count"] == 1
    assert listed["entries"][0]["summary"].startswith("サーバールール:")
    assert "text" not in listed["entries"][0]
    assert audit["safe_for_tunnel"] is True
    assert "個人情報の共有は禁止" in loaded

    passport = context_passport_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        server_context=loaded,
    )
    assert passport["external_context_used"] is True
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])


def test_context_library_resolves_public_safe_labels(tmp_path):
    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community"],
    )
    upsert_context_document(
        "channel",
        "planning",
        CHANNEL_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community/safe-planning"],
    )
    upsert_context_document(
        "thread",
        "launch-thread",
        THREAD_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["safe-planning:thread"],
    )

    bindings = resolve_context_bindings(
        guild_label="example-community",
        channel_label="#safe-planning",
        path=context_store,
    )

    assert bindings["server"]["key"] == "nexus"
    assert bindings["channel"]["key"] == "planning"
    assert bindings["thread"]["key"] == "launch-thread"


def test_context_library_audit_flags_private_identifiers(tmp_path):
    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "unsafe",
        "Webhook: https://discord.com/api/webhooks/123456789012345678/token",
        path=context_store,
    )

    audit = audit_context_store(context_store)

    assert audit["safe_for_tunnel"] is False
    assert audit["issue_count"] >= 1
    assert "discord_webhook_url" in {issue["kind"] for issue in audit["issues"]}


def test_review_state_saves_safe_metadata_without_draft_or_path(tmp_path):
    store = tmp_path / "review-registry.json"
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent("member-a に公開時期の前提を確認して返信します。", events, understanding_confirmed=True)

    saved = upsert_review_state("C:\\Users\\example\\thread member-a", review, path=store)
    entries = load_review_registry(store)

    assert saved["message"] == "review registry を更新しました。"
    assert saved["path_output"] == "omitted"
    assert saved["entry"]["schema"] == "discord_review_state.v1"
    assert saved["entry"]["thread_key"] == "[local path omitted] safe-member"
    assert saved["entry"]["gate_decision"] == "pending"
    assert saved["entry"]["recommended_option"] == "copy"
    assert saved["entry"]["copy_block_status"] == "ready"
    assert saved["entry"]["outbound_actions"] == "disabled"
    assert len(entries) == 1
    serialized = json.dumps(entries, ensure_ascii=False)
    assert "公開時期の前提" not in serialized
    assert "member-a" not in serialized
    assert "C:\\Users\\example" not in serialized


def test_handoff_packet_uses_review_state_without_private_text(tmp_path):
    review_store = tmp_path / "review-registry.json"
    event_store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=event_store, channel_label="safe-general")
    review = review_reply_intent("公開時期の前提を確認して返信します。", load_events(event_store), understanding_confirmed=True)
    upsert_review_state("safe-thread", review, path=review_store, read_scope=["visible_text", "artifact"])

    packet = build_handoff_packet(
        thread_key="safe-thread",
        review_state=get_review_state("safe-thread", path=review_store),
        status=status_dashboard(event_store),
    )

    assert packet["schema"] == "discord_handoff_packet.v1"
    assert packet["current_state"]["review_state_available"] is True
    assert packet["current_state"]["context_available"] is True
    assert packet["current_state"]["gate_decision"] == "pending"
    assert packet["current_state"]["copy_block_status"] == "ready"
    assert packet["read_scope"] == ["visible_text", "artifact"]
    assert "Discord send/reaction/edit/delete disabled" in packet["stopline"]
    assert packet["safety_boundary"]["outbound_actions"] == "disabled"
    serialized = json.dumps(packet, ensure_ascii=False)
    assert "Can you clarify" not in serialized
    assert "member-a" not in serialized
    assert str(tmp_path) not in serialized


def test_send_message_is_disabled():
    with pytest.raises(DisabledCapability):
        send_message("hello")


def test_user_facing_docs_declare_japanese_default():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    checklist = (ROOT / "PUBLIC_RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "基本言語は日本語" in readme
    assert "Language Boundary" in checklist
    assert "CLI / API のユーザー向けメッセージが日本語" in checklist


def test_package_version_tracks_current_release():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    version = metadata["project"]["version"]

    assert version
    assert f"## {version} -" in changelog
    assert "## Unreleased" in changelog
    assert load_bump_version_module().check_version_consistency(ROOT) == []


def test_bump_version_computes_next_semver():
    bump_version = load_bump_version_module()

    assert bump_version.bump_semver("0.10.0", "patch") == "0.10.1"
    assert bump_version.bump_semver("0.10.0", "minor") == "0.11.0"
    assert bump_version.bump_semver("0.10.0", "major") == "1.0.0"


def test_bump_version_updates_pyproject_and_changelog(tmp_path):
    bump_version = load_bump_version_module()
    pyproject = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    pyproject.write_text('[project]\nname = "demo"\nversion = "0.10.0"\n', encoding="utf-8")
    changelog.write_text(
        "# Changelog\n\n## Unreleased\n\n- 新しい公開機能。\n\n## 0.10.0 - 2026-06-19\n\n- 以前の機能。\n",
        encoding="utf-8",
    )

    bump_version.write_project_version(pyproject, "0.10.1")
    bump_version.update_changelog(changelog, "0.10.1", "2026-06-22")

    assert bump_version.read_project_version(pyproject) == "0.10.1"
    updated = changelog.read_text(encoding="utf-8")
    assert "## Unreleased\n\n- 次の変更をここに記録します。" in updated
    assert "## 0.10.1 - 2026-06-22\n\n- 新しい公開機能。" in updated
    assert "## 0.10.0 - 2026-06-19" in updated


def test_bump_version_can_require_current_git_tag(monkeypatch):
    bump_version = load_bump_version_module()

    monkeypatch.setattr(bump_version, "read_project_version", lambda path: "0.10.0")
    monkeypatch.setattr(bump_version, "release_versions_from_changelog", lambda path: ["0.10.0"])
    monkeypatch.setattr(bump_version, "latest_git_tag_version", lambda root: "0.10.0")
    monkeypatch.setattr(
        bump_version.Path,
        "read_text",
        lambda self, encoding=None: "# Changelog\n\n## Unreleased\n\n## 0.10.0 - 2026-06-19\n",
    )
    monkeypatch.setattr(bump_version, "git_tag_exists", lambda root, version: False)

    issues = bump_version.check_version_consistency(ROOT, require_current_tag=True)

    assert "current version 0.10.0 に対応する git tag v0.10.0 がありません。" in issues


def test_bump_version_creates_release_tag(monkeypatch):
    bump_version = load_bump_version_module()
    calls = []

    monkeypatch.setattr(bump_version, "git_tag_exists", lambda root, version: False)

    def fake_run(command, **kwargs):
        calls.append(command)

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    monkeypatch.setattr(bump_version.subprocess, "run", fake_run)

    bump_version.create_git_tag(ROOT, "0.10.1")

    assert calls == [["git", "tag", "v0.10.1"]]


def test_user_facing_runtime_messages_are_japanese():
    gap = review_reply_intent("", [], understanding_confirmed=True)

    assert gap["language"] == "ja"
    assert gap["missing_knowledge"] == ["共有前提", "返信対象"]
    assert gap["ok_to_reply_label"] == "先に文脈理解を確認してください。"
    assert gap["alignment_label"] == "文脈理解の確認前です。"
    assert gap["missing_knowledge_label"] == "理解確認gateで停止中です。"
    assert gap["suggested_correction"] == "直近の文脈はまだ取り込まれていません。"

    with pytest.raises(DisabledCapability, match="Discord への送信機能"):
        send_message("hello")


def test_cli_help_uses_japanese_user_facing_text():
    help_text = build_parser().format_help()

    assert "可視会話テキスト" in help_text
    assert "直近文脈" in help_text
    assert "audit-store" in help_text
    assert "ops-view" in help_text
    assert "status-dashboard" in help_text
    assert "report-latest" in help_text
    assert "import-clipboard" in help_text
    assert "guide-reply" in help_text
    assert "context-passport" in help_text
    assert "triage-mode" in help_text
    assert "catchup-mode" in help_text
    assert "join-thread-mode" in help_text
    assert "boundary-mode" in help_text
    assert "watch-passport" in help_text
    assert "使い方:" in help_text
    assert "オプション:" in help_text
    assert "show this help message and exit" not in help_text
    assert "positional arguments:" not in help_text


def test_cli_status_dashboard_outputs_safe_json(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(
        [
            "--store",
            str(store),
            "status-dashboard",
            "--json",
            "--github-state",
            "merged",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_bridge_status_dashboard.v1"' in output
    assert '"state": "merged"' in output
    assert '"outbound_actions": "disabled"' in output
    assert "Can you clarify" not in output
    assert "member-a" not in output
    assert str(tmp_path) not in output


def test_cli_report_latest_outputs_safe_json(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    snapshot_visible_text(
        text="member-a: private launch wording should not leak",
        url="https://discord.com/channels/1/2/3",
        source="chrome_extension_dom",
        path=snapshot_store,
    )

    result = cli_main(
        [
            "report-latest",
            "--snapshot-store",
            str(snapshot_store),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["schema"] == "discord_bridge_latest_snapshot_report.v1"
    assert payload["raw_text_returned"] is False
    assert payload["source_summary"]["source_kind"] == "Chrome DOM"
    assert payload["source_summary"]["report_acquisition_context"]["mode"] == "existing_saved_snapshot"
    assert payload["source_summary"]["report_acquisition_context"]["live_browser_access"] is False
    assert payload["source_summary"]["report_acquisition_context"]["new_capture"] is False
    assert str(snapshot_store) not in output
    assert "private launch wording" not in output
    assert "member-a" not in output


def test_cli_snapshot_discord_url_text_saves_snapshot_without_echoing_raw_text(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    visible_text = tmp_path / "visible.txt"
    visible_text.write_text("member-a: private post-send wording should stay local", encoding="utf-8")

    result = cli_main(
        [
            "snapshot-discord-url-text",
            "--url",
            "https://discord.com/channels/1/2/3",
            "--input",
            str(visible_text),
            "--snapshot-store",
            str(snapshot_store),
            "--source",
            "computer_use_accessibility_tree",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "saved: true" in output
    assert "raw_text_returned: false" in output
    assert "path_output: omitted" in output
    assert "private post-send wording" not in output
    assert "member-a" not in output
    assert "discord.com/channels" not in output

    records = load_jsonl_records(snapshot_store)
    assert len(records) == 1
    assert records[0]["text"] == "member-a: private post-send wording should stay local"
    assert records[0]["source"] == "computer_use_accessibility_tree"


def test_cli_snapshot_discord_url_text_json_is_metadata_only(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    visible_text = tmp_path / "visible.txt"
    visible_text.write_text("member-b: raw saved text is local only", encoding="utf-8")

    result = cli_main(
        [
            "snapshot-discord-url-text",
            "--url",
            "https://discord.com/channels/4/5/6",
            "--input",
            str(visible_text),
            "--snapshot-store",
            str(snapshot_store),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["visible_text_saved"] is True
    assert payload["private_local_only"] is True
    assert payload["external_share_allowed"] is False
    assert payload["outbound_actions"] == "disabled"
    assert "raw saved text" not in output
    assert "member-b" not in output
    assert "discord.com/channels" not in output


def test_plan_full_thread_capture_blocks_visible_dom_only(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    url = "https://discord.com/channels/4/5/6"
    snapshot_visible_text(
        text="member-c: visible range only stays private",
        url=url,
        source="chrome_visible_message_nodes",
        path=snapshot_store,
    )

    plan = plan_full_thread_capture(
        url=url,
        snapshot_store=snapshot_store,
        visible_dom_available=True,
    )

    assert plan["schema"] == "discord_full_thread_capture_plan.v1"
    assert plan["ok"] is False
    assert plan["state"] == "blocked_missing_full_thread_route"
    assert plan["coverage_now"]["saved_snapshot_count"] == 1
    assert plan["coverage_now"]["visible_dom_is_full_thread_proof"] is False
    assert "rest_backfill_not_configured" in plan["blockers"]
    assert plan["raw_text_returned"] is False


def test_cli_thread_capture_plan_is_metadata_only(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    visible_text = tmp_path / "visible.txt"
    visible_text.write_text("member-d: private thread text should stay local", encoding="utf-8")
    url = "https://discord.com/channels/7/8/9"
    snapshot_visible_text(
        text=visible_text.read_text(encoding="utf-8"),
        url=url,
        path=snapshot_store,
        source="chrome_visible_message_nodes",
    )

    result = cli_main(
        [
            "thread-capture-plan",
            "--url",
            url,
            "--snapshot-store",
            str(snapshot_store),
            "--visible-dom-available",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 2
    assert payload["state"] == "blocked_missing_full_thread_route"
    assert payload["route_allocation"]["visible_dom"]["required_for_full_thread"] is False
    assert payload["route_allocation"]["rest_backfill"]["required_for_full_thread"] is True
    assert payload["raw_text_returned"] is False
    assert "private thread text" not in output
    assert "member-d" not in output
    assert url not in output
    assert str(snapshot_store) not in output


def test_cli_thread_capture_plan_detects_rest_env_without_claiming_full_capture(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DISCORD_" + "BOT_TOKEN", "synthetic-secret")

    result = cli_main(
        [
            "thread-capture-plan",
            "--url",
            "https://discord.com/channels/7/8/9",
            "--snapshot-store",
            str(tmp_path / "missing.ndjson"),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["state"] == "ready_for_full_capture"
    assert payload["route_allocation"]["rest_backfill"]["configured"] is True
    assert payload["coverage_now"]["full_thread_confirmed"] is False
    assert "synthetic-secret" not in output


def test_cache_first_intake_builds_private_book_from_saved_snapshot(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    book_output = tmp_path / "books" / "target.md"
    url = "https://discord.com/channels/7/8/9"
    snapshot_visible_text(
        text="member-e: private saved snapshot should become a local book",
        url=url,
        path=snapshot_store,
        source="chrome_visible_message_nodes",
    )

    payload = build_cache_first_intake(
        url=url,
        snapshot_store=snapshot_store,
        cache_root=tmp_path / "missing-raw-cache-root",
        book_output=book_output,
    )

    assert payload["ok"] is True
    assert payload["state"] == "book_created"
    assert payload["snapshot"]["source_selected"] == "saved_snapshot"
    assert payload["book"]["created"] is True
    assert payload["book"]["status"] == "partial_visible_snapshot_book"
    assert payload["raw_text_returned"] is False
    assert book_output.exists()
    assert "private saved snapshot" in book_output.read_text(encoding="utf-8")


def test_cli_cache_first_intake_is_metadata_only(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    book_output = tmp_path / "books" / "target.md"
    cache_root = tmp_path / "cache"
    url = "https://discord.com/channels/7/8/10"
    snapshot_visible_text(
        text="member-f: raw local book text should not print",
        url=url,
        path=snapshot_store,
        source="chrome_visible_message_nodes",
    )

    result = cli_main(
        [
            "cache-first-intake",
            "--url",
            url,
            "--snapshot-store",
            str(snapshot_store),
            "--cache-root",
            str(cache_root),
            "--book-output",
            str(book_output),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["state"] == "book_created"
    assert payload["book"]["created"] is True
    assert payload["path_output"] == "omitted"
    assert "raw local book text" not in output
    assert "member-f" not in output
    assert url not in output
    assert str(book_output) not in output
    assert str(snapshot_store) not in output
    assert book_output.exists()


def test_write_rest_backfill_capture_saves_private_raw_and_metadata_manifest(tmp_path):
    raw_output = tmp_path / "raw" / "rest.ndjson"
    manifest_output = tmp_path / "manifests" / "rest.json"
    url = "https://discord.com/channels/7/8/9"
    messages = [
        {
            "id": "123456789012345678",
            "content": "member-g private REST text should stay in private raw artifact",
            "attachments": [{"filename": "screen.png", "url": "https://cdn.example.invalid/private.png"}],
        }
    ]

    payload = write_rest_backfill_capture(
        url=url,
        messages=messages,
        raw_output=raw_output,
        manifest_output=manifest_output,
        full_thread_confirmed=True,
    )

    assert payload["schema"] == "discord_rest_backfill_capture_manifest.v1"
    assert payload["ok"] is True
    assert payload["coverage"]["message_count"] == 1
    assert payload["coverage"]["attachment_candidate_count"] == 1
    assert payload["coverage"]["full_thread_confirmed"] is True
    assert payload["raw_text_returned"] is False
    assert payload["artifact"]["raw_output"] == "omitted"
    assert raw_output.exists()
    assert manifest_output.exists()
    assert "private REST text" in raw_output.read_text(encoding="utf-8")
    manifest_text = manifest_output.read_text(encoding="utf-8")
    assert "private REST text" not in manifest_text
    assert "123456789012345678" not in manifest_text
    assert url not in json.dumps(payload, ensure_ascii=False)
    assert str(raw_output) not in json.dumps(payload, ensure_ascii=False)


def test_write_rest_backfill_capture_keeps_partial_until_full_confirmed(tmp_path):
    payload = write_rest_backfill_capture(
        url="https://discord.com/channels/7/8/9",
        messages=[{"content": "private partial text"}],
        raw_output=tmp_path / "raw.ndjson",
        manifest_output=tmp_path / "manifest.json",
        full_thread_confirmed=False,
    )

    assert payload["state"] == "partial_capture_ready"
    assert payload["coverage"]["full_thread_confirmed"] is False


def test_rest_backfill_config_safety_blocks_user_token_cookie_and_chrome_profile():
    unsafe = (
        "Author" + "ization: " + "Bear" + "er mfa.syntheticUserToken "
        "Cookie: session=secret "
        r"D:\ExampleProfile\Chrome\User Data\Default\Local Storage"
    )

    result = rest_backfill_config_safety(unsafe)

    assert result["ok"] is False
    assert "authorization_header_in_config" in result["blockers"]
    assert "cookie_reference_in_config" in result["blockers"]
    assert "chrome_profile_or_storage_reference_in_config" in result["blockers"]
    assert result["value_output"] == "omitted"
    assert "syntheticUserToken" not in json.dumps(result, ensure_ascii=False)


def test_cli_url_intake_fast_path_refreshes_before_saved_snapshot_decision(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    visible_text = tmp_path / "visible.txt"
    visible_text.write_text("member-c: latest reply context stays private", encoding="utf-8")
    url = "https://discord.com/channels/7/8/9"

    result = cli_main(
        [
            "url-intake-fast-path",
            "--url",
            url,
            "--snapshot-store",
            str(snapshot_store),
            "--input",
            str(visible_text),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["refresh_attempted_before_decision"] is True
    assert payload["refresh_snapshot"]["saved"] is True
    assert payload["refresh_snapshot"]["raw_text_returned"] is False
    assert payload["decision"] == "snapshot_metadata_ready"
    assert payload["operations"]["new_capture"] is True
    assert payload["operations"]["browser_access"] == "refresh_source_command_or_input"
    assert "latest reply context" not in output
    assert "member-c" not in output
    assert url not in output

    records = load_jsonl_records(snapshot_store)
    assert len(records) == 1
    assert records[0]["text"] == "member-c: latest reply context stays private"
    assert records[0]["source"] == "discord_url_visible_text_refresh"


def test_cli_url_intake_fast_path_refreshes_stale_snapshot_and_compares_raw_cache(tmp_path, capsys):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    raw_cache = tmp_path / "raw-cache.ndjson"
    visible_text = tmp_path / "visible.txt"
    url = "https://discord.com/channels/7/8/10"
    key = target_key_for_url(url)
    stale_record = {
        "url": url,
        "target_key": key,
        "text": "member-d: old cache text stays private",
        "captured_at": "2026-07-01T00:00:00+00:00",
        "content_hash": "old-cache-hash",
        "outbound_actions": "disabled",
    }
    snapshot_store.write_text(json.dumps(stale_record, ensure_ascii=False) + "\n", encoding="utf-8")
    raw_cache.write_text(json.dumps(stale_record, ensure_ascii=False) + "\n", encoding="utf-8")
    visible_text.write_text("member-d: latest downloaded text stays private", encoding="utf-8")

    result = cli_main(
        [
            "url-intake-fast-path",
            "--url",
            url,
            "--snapshot-store",
            str(snapshot_store),
            "--raw-cache",
            str(raw_cache),
            "--input",
            str(visible_text),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["initial_recency"]["status"] == "stale"
    assert payload["refresh_needed_before_decision"] is True
    assert payload["refresh_attempted_before_decision"] is True
    assert payload["final_recency"]["status"] == "recent"
    assert payload["cache_comparison"]["raw_cache_checked"] is True
    assert payload["cache_comparison"]["snapshot_match_count"] == 2
    assert payload["cache_comparison"]["raw_cache_match_count"] == 1
    assert payload["cache_comparison"]["content_hash_match"] is False
    assert "old cache text" not in output
    assert "latest downloaded text" not in output
    assert "member-d" not in output
    assert url not in output

    records = load_jsonl_records(snapshot_store)
    assert len(records) == 2
    assert records[-1]["text"] == "member-d: latest downloaded text stays private"


def test_cli_review_draft_writes_markdown_artifact_without_path_or_raw_context(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    artifact_path = tmp_path / "review.md"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(
        [
            "--store",
            str(store),
            "review-draft",
            "--draft",
            "member-a に公開時期の前提を確認します。",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
            "--artifact-path",
            str(artifact_path),
        ]
    )
    output = capsys.readouterr().out
    artifact = artifact_path.read_text(encoding="utf-8")

    assert result == 0
    assert artifact_path.exists()
    assert '"schema": "discord_review_artifact_markdown.v1"' in output
    assert '"path_output": "omitted"' in output
    assert str(artifact_path) not in output
    assert "Can you clarify" not in output
    assert "member-a" not in output
    assert "Can you clarify" not in artifact
    assert "member-a" not in artifact
    assert "safe-member に公開時期の前提を確認します。" in artifact
    assert "outbound_actions: disabled" in artifact


def test_cli_review_draft_json_omits_raw_context_without_artifact(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(
        [
            "--store",
            str(store),
            "review-draft",
            "--draft",
            "公開時期の前提を確認します。",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"final_candidate": "公開時期の前提を確認します。"' in output
    assert '"schema": "discord_human_gate.v1"' in output
    assert '"schema": "discord_copy_block.v1"' in output
    assert '"likely_counterparty_meaning": "omitted"' in output
    assert '"suggested_correction": "omitted"' in output
    assert "Can you clarify" not in output
    assert "member-a" not in output
    assert str(tmp_path) not in output


def test_cli_review_draft_without_understanding_confirmation_blocks_copy_block(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(
        [
            "--store",
            str(store),
            "review-draft",
            "--draft",
            "公開時期の前提を確認します。",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert '"schema": "discord_understanding_gate.v1"' in output
    assert '"status": "blocked"' in output
    assert '"quick_verdict": "understanding-blocked"' in output
    assert '"final_candidate": ""' in output
    assert '"text": ""' in output
    assert '"recommended_option": "copy"' not in output
    assert str(tmp_path) not in output


def test_cli_review_draft_saves_review_state_and_handoff_reads_it(tmp_path, capsys):
    event_store = tmp_path / "events.ndjson"
    review_store = tmp_path / "review-registry.json"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=event_store, channel_label="safe-general")

    result = cli_main(
        [
            "--store",
            str(event_store),
            "--review-store",
            str(review_store),
            "review-draft",
            "--thread-key",
            "safe-thread",
            "--save-review-state",
            "--draft",
            "公開時期の前提を確認して返信します。",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"review_state"' in output
    assert '"path_output": "omitted"' in output
    assert str(review_store) not in output
    assert "Can you clarify" not in output
    assert "member-a" not in output

    result = cli_main(
        [
            "--store",
            str(event_store),
            "--review-store",
            str(review_store),
            "handoff-packet",
            "--thread-key",
            "safe-thread",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_handoff_packet.v1"' in output
    assert '"review_state_available": true' in output
    assert '"context_available": true' in output
    assert '"outbound_actions": "disabled"' in output
    assert "Can you clarify" not in output
    assert "member-a" not in output
    assert str(tmp_path) not in output


def test_ops_check_secret_scan_allows_windows_style_fixture_paths(monkeypatch):
    ops_check = load_script_module("ops_check", ROOT / "scripts" / "ops_check.py")
    output = "\n".join(
        [
            r".\PUBLIC_RELEASE_CHECKLIST.md:69:rg -n fake",
            r".\tests\test_core.py:115:https://discord.com/api/webhooks/123456789012345678/token",
        ]
    )

    def fake_run_command(name, command, *, env=None):
        return ops_check.CheckResult(name, True, 0.01, command, output)

    monkeypatch.setattr(ops_check, "run_command", fake_run_command)
    result = ops_check.run_secret_scan()

    assert result.ok is True
    assert "想定外の一致" not in result.output


def test_report_latest_smoke_verifies_metadata_only_contract():
    report_latest_smoke = load_script_module("report_latest_smoke", ROOT / "scripts" / "report_latest_smoke.py")

    report = report_latest_smoke.build_report()

    assert report["ok"] is True
    assert report["failure_stage"] is None
    assert report["raw_text_returned"] is False
    assert report["live_browser_access"] is False
    assert report["new_capture"] is False


def test_report_latest_schema_check_verifies_public_contract():
    load_script_module("report_latest_smoke", ROOT / "scripts" / "report_latest_smoke.py")
    report_latest_schema_check = load_script_module(
        "report_latest_schema_check",
        ROOT / "scripts" / "report_latest_schema_check.py",
    )

    report = report_latest_schema_check.build_report()

    assert report["ok"] is True
    assert report["failure_stage"] is None
    assert report["validated_schema_count"] == 4


def test_ops_check_includes_report_latest_smoke():
    ops_check = load_script_module("ops_check", ROOT / "scripts" / "ops_check.py")
    args = ops_check.parse_args([])

    checks = ops_check.build_checks(args)

    assert "report-latest smoke" in checks
    assert "report-latest schema" in checks
    assert "文脈運用モード smoke" in checks
    assert "discord-url-measure smoke" in checks


def test_ops_check_fast_profile_uses_small_development_gate():
    ops_check = load_script_module("ops_check", ROOT / "scripts" / "ops_check.py")
    args = ops_check.parse_args(["--profile", "fast"])

    checks = ops_check.build_checks(args)

    assert set(checks) == {
        "compile",
        "差分チェック",
        "秘密情報スキャン",
        "boundary logic",
        "url-intake-fast-path smoke",
        "discord-url-measure smoke",
        "ローカルスモーク",
    }
    assert "テスト" not in checks
    assert "status dashboard" not in checks


def test_ops_check_release_profile_enables_github_guard():
    ops_check = load_script_module("ops_check", ROOT / "scripts" / "ops_check.py")
    args = ops_check.parse_args(["--profile", "release"])

    checks = ops_check.build_checks(args)

    assert "GitHub account確認" in checks


def test_cli_guide_reply_outputs_human_readable_guide(capsys):
    result = cli_main(
        [
            "guide-reply",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "Discord 返信ガイドを作成しました。" in output
    assert "解析件数: 3" in output
    assert "返信してよさそうです。" in output
    assert "このツールから Discord へ送信しません。" in output


def test_cli_guide_reply_reads_clipboard_command(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    result = cli_main(
        [
            "guide-reply",
            "--from-clipboard",
            "--clipboard-command",
            "pbpaste",
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"message": "Discord 返信ガイドを作成しました。"' in output
    assert '"send_capability": "disabled"' in output


def test_cli_guide_reply_reads_source_command(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_main(
        [
            "guide-reply",
            "--source-command",
            "discord-visible-text",
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["discord-visible-text"]
    assert calls[0][1]["capture_output"] is True
    assert '"message": "Discord 返信ガイドを作成しました。"' in output
    assert '"send_capability": "disabled"' in output


@pytest.mark.parametrize(
    ("command", "mode"),
    [
        ("triage-mode", "triage"),
        ("catchup-mode", "catchup"),
        ("join-thread-mode", "join-thread"),
        ("boundary-mode", "boundary"),
    ],
)
def test_cli_context_modes_output_safe_json(command, mode, capsys):
    snowflake = "1" * 18
    discord_url = "https://discord.com/" + f"channels/{snowflake}/{'2' * 18}"
    local_path = "/" + "Users" + "/example/private.txt"
    result = cli_main(
        [
            command,
            "--input",
            str(RICH_COPY_FIXTURE),
            "--focus",
            f"公開時期 {discord_url} {local_path}",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_context_operating_mode.v1"' in output
    assert f'"mode": "{mode}"' in output
    assert '"raw_text_returned": false' in output
    assert '"send_capability": "disabled"' in output
    assert "member-a" not in output
    assert "公開時期の話ですよね" not in output
    assert "discord.com/channels" not in output
    assert snowflake not in output
    assert local_path not in output


def test_cli_source_command_error_redacts_sensitive_stderr(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failed with https://discord.com/api/webhooks/123456789012345678/token"

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    with pytest.raises(SystemExit) as exc:
        cli_module.read_command_text("discord-visible-text")

    message = str(exc.value)
    assert "安全監査により詳細を省略しました。" in message
    assert "webhooks/123456789012345678" not in message
    assert "discord-visible-text" not in message


def test_cli_source_command_error_reports_safe_reason(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    with pytest.raises(SystemExit) as exc:
        cli_module.read_command_text("discord-visible-text")

    assert "exit_code=1 reason=permission" in str(exc.value)


def test_cli_source_command_error_reports_safe_stdout_reason(monkeypatch):
    class Completed:
        returncode = 1
        stdout = "Discord 可視テキストの取得に失敗しました: not_found"
        stderr = ""

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    with pytest.raises(SystemExit) as exc:
        cli_module.read_command_text("discord-visible-text")

    message = str(exc.value)
    assert "exit_code=1 reason=not_found" in message
    assert "Discord 可視テキストの取得に失敗しました" not in message
    assert "discord-visible-text" not in message


def test_cli_context_passport_outputs_human_readable_context(capsys):
    result = cli_main(
        [
            "context-passport",
            "--input",
            str(PASSPORT_FIXTURE),
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "スレッド文脈カードを作成しました。" in output
    assert "解析件数: 4" in output
    assert "ルール注意:" in output
    assert "相談・注意・困りごと寄りです。" in output
    assert "このツールから Discord へ送信しません。" in output


def test_cli_context_passport_accepts_explicit_context_files(capsys):
    result = cli_main(
        [
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--server-context",
            str(SERVER_CONTEXT_FIXTURE),
            "--channel-context",
            str(CHANNEL_CONTEXT_FIXTURE),
            "--thread-context",
            str(THREAD_CONTEXT_FIXTURE),
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "明示文脈を併用しています。" in output
    assert "文脈ソース: サーバー文脈、チャンネル文脈、スレッド文脈" in output
    assert "個人情報の共有は禁止" in output


def test_cli_context_library_persists_and_passport_uses_keys(tmp_path, capsys):
    context_store = tmp_path / "context-library.json"

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-upsert",
            "--kind",
            "server",
            "--key",
            "nexus",
            "--input",
            str(SERVER_CONTEXT_FIXTURE),
            "--source",
            "fixture",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert '"message": "文脈庫を更新しました。"' in output

    result = cli_main(["--context-store", str(context_store), "audit-context-store"])
    output = capsys.readouterr().out
    assert result == 0
    assert '"safe_for_tunnel": true' in output

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--server-context-key",
            "nexus",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "明示文脈を併用しています。" in output
    assert "個人情報の共有は禁止" in output


def test_cli_context_passport_auto_binds_safe_labels(tmp_path, capsys):
    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community"],
    )
    upsert_context_document(
        "channel",
        "planning",
        CHANNEL_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community/safe-planning"],
    )

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--guild",
            "example-community",
            "--channel",
            "safe-planning",
            "--auto-context-bindings",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "safe label から文脈庫を補助参照しました。" in output
    assert "サーバー文脈" in output
    assert "チャンネル文脈" in output
    assert "個人情報の共有は禁止" in output


def test_cli_context_passport_explicit_key_wins_over_auto_binding(tmp_path, capsys):
    context_store = tmp_path / "context-library.json"
    safe_auto_text = "自動側のサーバールール: 価格の相談用です。"
    explicit_text = "明示側のサーバールール: ルールとして未確認の断定は禁止です。"
    upsert_context_document(
        "server",
        "auto",
        safe_auto_text,
        path=context_store,
        labels=["example-community"],
    )
    upsert_context_document(
        "server",
        "explicit",
        explicit_text,
        path=context_store,
    )

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--guild",
            "example-community",
            "--channel",
            "safe-planning",
            "--auto-context-bindings",
            "--server-context-key",
            "explicit",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "明示側のサーバールール" in output
    assert "自動側のサーバールール" not in output


def test_cli_ops_view_outputs_safe_operations_summary(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(["--store", str(store), "ops-view"])
    output = capsys.readouterr().out

    assert result == 0
    assert "運用ログ表示を作成しました。" in output
    assert "safe label: safe-general" in output
    assert "件数: 3" in output
    assert "gate verdict: pass" in output
    assert "このツールから Discord へ送信しません。" in output
    assert "Can you clarify" not in output


def test_cli_context_passport_reads_source_command(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = PASSPORT_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_main(
        [
            "context-passport",
            "--source-command",
            "discord-visible-text",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["discord-visible-text"]
    assert '"message": "スレッド文脈カードを作成しました。"' in output
    assert '"people_temperature": "serious"' in output
    assert '"send_capability": "disabled"' in output


def test_cli_watch_guide_polls_source_command_and_outputs_updates(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    fixture = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
    outputs = [fixture, fixture, "member-d: 価格ではなく公開時期の前提です"]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(outputs.pop(0))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.time, "sleep", lambda interval: None)

    result = cli_main(
        [
            "watch-guide",
            "--source-command",
            "discord-visible-text",
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--understanding-confirmed",
            "--max-polls",
            "3",
            "--interval",
            "0",
            "--source-timeout",
            "4",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert len(calls) == 3
    assert calls[0][1]["timeout"] == 4
    assert '"event": "guide_update"' in output
    assert '"message": "Discord 返信ガイドを更新しました。"' in output
    assert '"polls": 3' in output
    assert '"changed": 2' in output
    assert '"send_capability": "disabled"' in output
    assert "返信ガイド監視を終了しました。" in output


def test_cli_watch_passport_polls_source_command_and_outputs_updates(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    fixture = PASSPORT_FIXTURE.read_text(encoding="utf-8")
    outputs = [fixture, fixture, "member-d: 公開時期の前提を確認してから相談しましょう"]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(outputs.pop(0))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.time, "sleep", lambda interval: None)

    result = cli_main(
        [
            "watch-passport",
            "--source-command",
            "discord-visible-text",
            "--max-polls",
            "3",
            "--interval",
            "0",
            "--source-timeout",
            "4",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert len(calls) == 3
    assert calls[0][0] == ["discord-visible-text"]
    assert calls[0][1]["timeout"] == 4
    assert '"event": "passport_update"' in output
    assert '"message": "スレッド文脈カードを更新しました。"' in output
    assert '"polls": 3' in output
    assert '"changed": 2' in output
    assert '"send_capability": "disabled"' in output
    assert "文脈カード監視を終了しました。" in output


def test_cli_watch_passport_allows_env_prefixed_source_command(capsys, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "outer")

    class Completed:
        returncode = 0
        stderr = ""
        stdout = PASSPORT_FIXTURE.read_text(encoding="utf-8")

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_main(
        [
            "watch-passport",
            "--source-command",
            "PYTHONPATH=src discord-visible-text",
            "--max-polls",
            "1",
            "--interval",
            "0",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["discord-visible-text"]
    assert calls[0][1]["env"]["PYTHONPATH"] == "src"
    assert os.environ["PYTHONPATH"] == "outer"
    assert '"event": "passport_update"' in output


def test_cli_watch_passport_uses_context_library_keys(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")

    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
    )

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "watch-passport",
            "--source-command",
            "discord-visible-text",
            "--server-context-key",
            "nexus",
            "--max-polls",
            "1",
            "--interval",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "文脈カード更新 #1" in output
    assert "個人情報の共有は禁止" in output
    assert "このツールから Discord へ送信しません。" in output


def test_cli_watch_passport_auto_binds_safe_labels(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")

    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community"],
    )

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "watch-passport",
            "--source-command",
            "discord-visible-text",
            "--guild",
            "example-community",
            "--channel",
            "safe-planning",
            "--auto-context-bindings",
            "--max-polls",
            "1",
            "--interval",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "文脈カード更新 #1" in output
    assert "safe label から文脈庫を補助参照しました。" in output
    assert "個人情報の共有は禁止" in output


def test_cli_review_draft_alias_uses_stored_context(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    result = cli_main(
        [
            "--store",
            str(store),
            "review-draft",
            "--draft",
            "Before I reply, I will ask for the premise.",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"message": "返信前レビューが完了しました。"' in output
    assert '"send_capability": "disabled"' in output


def test_cli_stage_discord_send_outputs_fill_only_packet(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    result = cli_main(
        [
            "--store",
            str(store),
            "stage-discord-send",
            "--draft",
            "公開時期の前提を確認して返信します。",
            "--mode",
            "reply",
            "--target-url",
            "https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
            "--understanding-confirmed",
            "--thread-root-present",
            "--reply-target-present",
            "--prior-message-count", "2",
            "--history-exhausted",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_send_staging_packet.v1"' in output
    assert '"staging_status": "ready_to_fill"' in output
    assert '"target_url_output": "omitted"' in output
    assert "123456789012345678" not in output
    assert '"stage_guard": {' in output
    assert '"external_action": "none_until_human_send"' in output
    assert '"human_send_required": true' in output
    assert '"outbound_actions": "disabled"' in output


def test_cli_verify_chrome_fill_dry_run_blocks_ambiguous_reply_ui(tmp_path, capsys):
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )
    packet_path = tmp_path / "staging-packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")

    result = cli_main(
        [
            "verify-chrome-fill-dry-run",
            "--staging-packet",
            str(packet_path),
            "--socket-preflight",
            "--target-url-verified",
            "--socket-after-navigation",
            "--reply-ui-candidates",
            "2",
            "--draft-matches-copy-block",
            "--socket-pre-send",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert '"schema": "chrome_extension_fill_only_dry_run.v1"' in output
    assert '"dry_run_status": "blocked"' in output
    assert '"reply_ui_not_unique"' in output
    assert '"outbound_actions": "disabled"' in output


def test_cli_closeout_discord_send_outputs_metadata_only_packet(capsys):
    result = cli_main(
        [
            "closeout-discord-send",
            "--human-sent-observed",
            "--human-reviewed",
            "--observed-text-status",
            "human-edited-and-reviewed",
            "--unread-check-status",
            "none-unread",
            "--observed-message-id",
            "423456789012345678",
            "--observed-url",
            "https://discord.com/channels/123456789012345678/223456789012345678/423456789012345678",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_post_send_closeout_packet.v1"' in output
    assert '"closeout_status": "closed"' in output
    assert '"observed_message_id_output": "omitted"' in output
    assert '"observed_url_output": "omitted"' in output
    assert '"unread_check_status": "none_unread"' in output
    assert '"text_returned": false' in output
    assert "423456789012345678" not in output
    assert "discord.com/channels" not in output


def test_cli_closeout_discord_send_blocks_unchecked_text(capsys):
    result = cli_main(
        [
            "closeout-discord-send",
            "--human-sent-observed",
            "--human-reviewed",
            "--observed-text-status",
            "not-checked",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert '"closeout_status": "blocked"' in output
    assert '"observed_text_not_checked"' in output


def test_cli_closeout_discord_send_blocks_unread_items(capsys):
    result = cli_main(
        [
            "closeout-discord-send",
            "--human-sent-observed",
            "--human-reviewed",
            "--observed-text-status",
            "matches-copy-block",
            "--unread-check-status",
            "has-unread",
            "--unread-signal-count",
            "1",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert '"closeout_status": "blocked"' in output
    assert '"unread_items_remaining"' in output
    assert '"recommended_next_state": "review_unread_items"' in output


def test_cli_send_operation_status_reads_existing_gate_logs(tmp_path, capsys):
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )
    dry_run = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )
    closeout = build_discord_post_send_closeout_packet(
        staging_packet=packet,
        dry_run_report=dry_run,
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
        unread_check_status="none_unread",
    )
    staging_path = tmp_path / "staging.json"
    dry_run_path = tmp_path / "dry-run.json"
    closeout_path = tmp_path / "closeout.json"
    staging_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    dry_run_path.write_text(json.dumps(dry_run, ensure_ascii=False), encoding="utf-8")
    closeout_path.write_text(json.dumps(closeout, ensure_ascii=False), encoding="utf-8")

    result = cli_main(
        [
            "send-operation-status",
            "--staging-packet",
            str(staging_path),
            "--dry-run-report",
            str(dry_run_path),
            "--closeout-report",
            str(closeout_path),
            "--target-label",
            "test-channel",
            "--rollback-plan-reviewed",
            "--production-runbook-fixed",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_send_operation_status.v1"' in output
    assert '"ok": true' in output
    assert '"ready_count": 6' in output
    assert '"discord_send_executed_by_this_tool": false' in output


def test_cli_send_operation_status_closes_post_send_retrospective_without_claiming_pre_gates(tmp_path, capsys):
    closeout = build_discord_post_send_closeout_packet(
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
        unread_check_status="none_unread",
    )
    closeout_path = tmp_path / "closeout.json"
    closeout_path.write_text(json.dumps(closeout, ensure_ascii=False), encoding="utf-8")

    result = cli_main(
        [
            "send-operation-status",
            "--closeout-report",
            str(closeout_path),
            "--target-label",
            "posted-reply",
            "--target-environment",
            "production",
            "--rollback-plan-reviewed",
            "--production-runbook-fixed",
            "--post-send-retrospective",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["mode"] == "post_send_retrospective"
    assert payload["state"] == "post_send_retrospective_closed"
    assert payload["retrospective_gaps"] == {
        "staging_packet_status": "not_provided",
        "dry_run_report_status": "not_provided",
        "pre_send_gate_claimed": False,
        "pre_send_gate_note": "事後closeoutです。過去送信について stage/dry-run/test gate を通過済みとは扱いません。",
        "future_formal_flow_required": True,
    }
    assert payload["safety_boundary"]["discord_send_executed_by_this_tool"] is False


def test_cli_send_operation_status_closes_post_send_retrospective_even_with_valid_pre_gates_does_not_claim_pre_gates(tmp_path, capsys):
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    packet = build_discord_send_staging_packet(
        "公開時期の前提を確認して返信します。",
        events,
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )
    dry_run = verify_chrome_extension_fill_only_dry_run(
        packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )
    closeout = build_discord_post_send_closeout_packet(
        staging_packet=packet,
        dry_run_report=dry_run,
        human_sent_observed=True,
        human_reviewed=True,
        observed_text_status="matches_copy_block",
        unread_check_status="none_unread",
    )
    staging_path = tmp_path / "staging.json"
    dry_run_path = tmp_path / "dry-run.json"
    closeout_path = tmp_path / "closeout.json"
    staging_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    dry_run_path.write_text(json.dumps(dry_run, ensure_ascii=False), encoding="utf-8")
    closeout_path.write_text(json.dumps(closeout, ensure_ascii=False), encoding="utf-8")

    result = cli_main(
        [
            "send-operation-status",
            "--staging-packet",
            str(staging_path),
            "--dry-run-report",
            str(dry_run_path),
            "--closeout-report",
            str(closeout_path),
            "--target-label",
            "test-channel",
            "--rollback-plan-reviewed",
            "--production-runbook-fixed",
            "--post-send-retrospective",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["mode"] == "post_send_retrospective"
    assert payload["retrospective_gaps"] == {
        "staging_packet_status": "provided",
        "dry_run_report_status": "provided",
        "pre_send_gate_claimed": False,
        "pre_send_gate_note": "事後closeoutです。過去送信について stage/dry-run/test gate を通過済みとは扱いません。",
        "future_formal_flow_required": True,
    }


def test_visible_text_adapter_reads_fixture_and_normalizes_output(capsys):
    adapter = load_script_module("visible_text_adapter_for_test", ROOT / "scripts" / "read_visible_discord_text.py")

    result = adapter.main(["--input", str(RICH_COPY_FIXTURE)])
    output = capsys.readouterr().out

    assert result == 0
    assert "member-a" in output
    assert "前提を確認してから返事したいです。" in output
    assert output.endswith("\n")


def test_visible_text_adapter_blocks_sensitive_output(tmp_path, capsys):
    adapter = load_script_module("visible_text_adapter_sensitive_for_test", ROOT / "scripts" / "read_visible_discord_text.py")
    unsafe = tmp_path / "unsafe.txt"
    unsafe.write_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", encoding="utf-8")

    result = adapter.main(["--input", str(unsafe)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "安全監査に失敗" in captured.err
    assert "discord_webhook_url" in captured.err


def test_visible_text_adapter_allow_unsafe_requires_env(tmp_path, capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_allow_unsafe_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    monkeypatch.delenv("DCB_ALLOW_UNSAFE_OUTPUT", raising=False)
    unsafe = tmp_path / "unsafe.txt"
    unsafe.write_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", encoding="utf-8")

    result = adapter.main(["--input", str(unsafe), "--allow-unsafe"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "discord_webhook_url" in captured.err
    assert "webhooks/123456789012345678" not in captured.err


def test_visible_text_adapter_blocks_expanded_sensitive_output(tmp_path, capsys):
    adapter = load_script_module(
        "visible_text_adapter_expanded_sensitive_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    unsafe_lines = [
        ("discord_snowflake_id", "member-a: synthetic id 123456789012345678"),
        ("local_absolute_path", "member-b: synthetic path /private/tmp/example-profile"),
        ("authorization_header", "member-c: Authorization: synthetic-secret-value"),
        ("bearer_token_like", "member-d: Bearer syntheticBearerValue123"),
    ]
    unsafe = tmp_path / "expanded-unsafe.txt"
    unsafe.write_text("\n".join(line for _, line in unsafe_lines), encoding="utf-8")

    result = adapter.main(["--input", str(unsafe)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "安全監査に失敗" in captured.err
    for issue, line in unsafe_lines:
        assert issue in captured.err
        assert line not in captured.err


def test_visible_text_adapter_can_read_source_command(capsys, monkeypatch):
    adapter = load_script_module("visible_text_adapter_command_for_test", ROOT / "scripts" / "read_visible_discord_text.py")

    class Completed:
        returncode = 0
        stdout = "member-a: 画面に見えている本文です\n"
        stderr = ""

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--source-command", "discord-visible-text"])
    output = capsys.readouterr().out

    assert result == 0
    assert output == "member-a: 画面に見えている本文です\n"


def test_screenshot_ocr_runner_reads_existing_image_with_private_ocr_command(tmp_path, capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")
    calls = []

    class Completed:
        returncode = 0
        stdout = "member-a: OCRで読めた本文です\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool {image}", "--timeout", "4"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["ocr-tool", str(image)]
    assert calls[0][1]["timeout"] == 4
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_runs_capture_then_ocr(capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_capture_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ocr-tool":
            return Completed("member-a: OCRで読めた本文です\n")
        return Completed("")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(
        [
            "--screenshot-command",
            "capture-tool {image}",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == "capture-tool"
    assert calls[1][0] == "ocr-tool"
    assert calls[0][1].endswith("capture.png")
    assert calls[1][1].endswith("capture.png")
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_builds_macos_screencapture_region(capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_region_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ocr-tool":
            return Completed("member-a: OCRで読めた本文です\n")
        return Completed("")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(
        [
            "--screencapture-region",
            "10,20,300,400",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][:3] == ["screencapture", "-x", "-R10,20,300,400"]
    assert calls[1][0] == "ocr-tool"
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_builds_named_capture_profile(capsys, monkeypatch):
    runner = load_script_module(
        "screenshot_ocr_runner_named_profile_for_test",
        ROOT / "scripts" / "read_screenshot_ocr_text.py",
    )
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ocr-tool":
            return Completed("member-a: OCRで読めた本文です\n")
        return Completed("")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,300,400",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][:3] == ["screencapture", "-x", "-R10,20,300,400"]
    assert calls[1][0] == "ocr-tool"
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_rejects_invalid_capture_region(capsys):
    runner = load_script_module("screenshot_ocr_runner_bad_region_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")

    result = runner.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,0,400",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "invalid_capture_region" in captured.err


def test_screenshot_ocr_runner_blocks_sensitive_ocr_output(tmp_path, capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_sensitive_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")

    class Completed:
        returncode = 0
        stdout = "member-a: https://discord.com/api/webhooks/123456789012345678/token\n"
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: Completed())

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool {image}"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "discord_webhook_url" in captured.err
    assert "webhooks/123456789012345678" not in captured.err


def test_screenshot_ocr_runner_allow_unsafe_requires_env(tmp_path, capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_allow_unsafe_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    monkeypatch.delenv("DCB_ALLOW_UNSAFE_OUTPUT", raising=False)
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")

    class Completed:
        returncode = 0
        stdout = "member-a: https://discord.com/api/webhooks/123456789012345678/token\n"
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: Completed())

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool {image}", "--allow-unsafe"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "discord_webhook_url" in captured.err
    assert "webhooks/123456789012345678" not in captured.err


def test_screenshot_ocr_runner_redacts_sensitive_command_error(capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_error_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failed near /private/tmp/discord-profile"

    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: Completed())

    result = runner.main(["--screenshot-command", "capture-tool {image}", "--ocr-command", "ocr-tool {image}"])
    captured = capsys.readouterr()

    assert result == 1
    assert "安全監査により詳細を省略しました。" in captured.err
    assert "/private/tmp/discord-profile" not in captured.err


def test_screenshot_ocr_runner_requires_image_placeholder(tmp_path, capsys):
    runner = load_script_module("screenshot_ocr_runner_placeholder_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool"])
    captured = capsys.readouterr()

    assert result == 1
    assert "placeholder_missing" in captured.err


def test_screenshot_ocr_runner_blocks_full_screen_screencapture(capsys):
    runner = load_script_module("screenshot_ocr_runner_fullscreen_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")

    result = runner.main(
        [
            "--screenshot-command",
            "screencapture -x {image}",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "full_screen_capture_blocked" in captured.err


def test_live_ops_smoke_omits_message_text(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"

    monkeypatch.setattr(
        live_smoke,
        "read_command_text",
        lambda command, empty_message, timeout=None: FIXTURE.read_text(encoding="utf-8"),
    )

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--store",
            str(store),
            "--channel",
            "safe-general",
            "--reset",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "live ops smoke が完了しました。" in output
    assert "safe label: safe-general" in output
    assert "parsed: 3" in output
    assert "appended: 3" in output
    assert "message text: omitted" in output
    assert "Can you clarify" not in output
    assert "text_snippet" not in output


def test_probe_visible_source_reports_without_message_text(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_for_test", ROOT / "scripts" / "probe_visible_source.py")
    calls = []

    class Completed:
        returncode = 0
        stdout = "member-a: 画面に見えている本文です\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(
        [
            "--ocr-command",
            "cat {image}",
            "--source-command",
            "python3 scripts/read_screenshot_ocr_text.py --image tests/fixtures/discord_rich_copy.txt --ocr-command 'cat {image}'",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "visible source probe pack が完了しました。" in output
    assert "- ocr_fixture: pass" in output
    assert "- live_ops_smoke: pass" in output
    assert "message text: omitted" in output
    assert "画面に見えている本文" not in output
    assert any("scripts/read_screenshot_ocr_text.py" in command for command in calls)


def test_probe_visible_source_can_build_ocr_capture_region_source(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_region_for_test", ROOT / "scripts" / "probe_visible_source.py")
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,300,400",
            "--ocr-language",
            "jpn+eng",
            "--json",
        ]
    )
    payload = capsys.readouterr().out

    assert result == 0
    live_smoke_call = next(command for command in calls if "scripts/live_ops_smoke.py" in command)
    source_index = live_smoke_call.index("--source-command") + 1
    source_command = live_smoke_call[source_index]
    assert "--capture-profile macos-screencapture-region --capture-region 10,20,300,400" in source_command
    assert "tesseract {image} stdout -l jpn+eng" in source_command
    assert '"text_output": "omitted"' in payload
    assert '"capture_profile"' in payload
    assert '"image_output": "temporary"' in payload


def test_probe_visible_source_reports_live_smoke_metrics(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_metrics_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        if "scripts/live_ops_smoke.py" in command:
            return Completed(
                '{"parsed": 7, "source_ready": true, "gate_verdict": "pass", '
                '"event_count": 7, "text_output": "omitted", "outbound": "disabled"}'
            )
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--source-command", "visible-source", "--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"parsed": 7' in payload
    assert '"source_ready": true' in payload
    assert '"gate_verdict": "pass"' in payload
    assert '"text_output": "omitted"' in payload


def test_probe_visible_source_rejects_invalid_ocr_language():
    probe = load_script_module("probe_visible_source_bad_language_for_test", ROOT / "scripts" / "probe_visible_source.py")

    with pytest.raises(ValueError, match="ocr language"):
        probe.build_ocr_capture_source_command(
            region="10,20,300,400",
            language="eng --psm 6",
            timeout=20,
        )


def test_probe_visible_source_requires_capture_profile_for_capture_region(capsys):
    probe = load_script_module("probe_visible_source_region_requires_profile_for_test", ROOT / "scripts" / "probe_visible_source.py")

    result = probe.main(["--capture-region", "10,20,300,400", "--json"])
    payload = capsys.readouterr().out

    assert result == 2
    assert '"reason": "capture_profile_required"' in payload


def test_probe_visible_source_reports_invalid_ocr_language_without_traceback(capsys):
    probe = load_script_module("probe_visible_source_bad_language_cli_for_test", ROOT / "scripts" / "probe_visible_source.py")

    result = probe.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,300,400",
            "--ocr-language",
            "eng --psm 6",
            "--json",
        ]
    )
    payload = capsys.readouterr().out

    assert result == 2
    assert '"reason": "invalid_ocr_language"' in payload


def test_probe_visible_source_json_marks_dependency_warning(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_json_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        stdout = ""
        stderr = ""

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        if command[:2] == ["tesseract", "--version"]:
            return Completed(127)
        return Completed(0)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"overall": "warn"' in payload
    assert '"name": "tesseract"' in payload
    assert '"status": "warn"' in payload
    assert '"text_output": "omitted"' in payload


def test_probe_visible_source_missing_optional_dependency_is_warning(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_missing_dependency_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        if command[:2] == ["tesseract", "--version"]:
            raise FileNotFoundError("missing")
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"overall": "warn"' in payload
    assert '"reason": "not_found"' in payload
    assert '"name": "tesseract"' in payload


def test_probe_visible_source_marks_accessibility_probe_failure_as_warning(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_ax_warn_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        stdout = ""
        stderr = "Application is not running"

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        if any("read_visible_discord_text.py" in part for part in command):
            return Completed(1)
        return Completed(0)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--ax-probe", "--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"overall": "warn"' in payload
    assert '"name": "macos_accessibility_probe"' in payload
    assert '"status": "warn"' in payload


def test_live_ops_smoke_passes_source_timeout(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_timeout_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"
    calls = []

    def fake_read_command_text(command, *, empty_message, timeout=None):
        calls.append({"command": command, "empty_message": empty_message, "timeout": timeout})
        return FIXTURE.read_text(encoding="utf-8")

    monkeypatch.setattr(live_smoke, "read_command_text", fake_read_command_text)

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--source-timeout",
            "3.5",
            "--store",
            str(store),
            "--reset",
        ]
    )

    assert result == 0
    assert calls[0]["timeout"] == 3.5
    assert "message text: omitted" in capsys.readouterr().out


def test_live_ops_smoke_reports_structured_source_failure(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_source_failure_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"

    def fake_read_command_text(command, *, empty_message, timeout=None):
        raise SystemExit("local command の実行に失敗しました: exit_code=1 reason=not_found")

    monkeypatch.setattr(live_smoke, "read_command_text", fake_read_command_text)

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--store",
            str(store),
            "--reset",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["source_ready"] is False
    assert payload["gate_verdict"] == "source_not_ready"
    assert payload["failure_stage"] == "dependency_missing"
    assert payload["source_stage"] == "window_not_found"
    assert payload["reason"] == "not_found"
    assert payload["text_output"] == "omitted"
    assert payload["outbound_actions"] == "disabled"
    assert not store.exists()


def test_live_ops_smoke_reports_timeout_reason(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_timeout_failure_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"

    def fake_read_command_text(command, *, empty_message, timeout=None):
        raise SystemExit("local command が 20 秒で完了しませんでした。")

    monkeypatch.setattr(live_smoke, "read_command_text", fake_read_command_text)

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--store",
            str(store),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["failure_stage"] == "source_command_timeout"
    assert payload["source_stage"] == "source_command_timeout"
    assert payload["reason"] == "timeout"
    assert payload["text_output"] == "omitted"


def test_live_ops_smoke_fails_when_source_parses_no_messages(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_min_parsed_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"

    monkeypatch.setattr(
        live_smoke,
        "read_command_text",
        lambda command, empty_message, timeout=None: "\n\n",
    )

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--store",
            str(store),
            "--reset",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert "parsed: 0" in output
    assert "min parsed: 1" in output
    assert "Discord 可視本文をまだ解析できていません。" in output
    assert "gate verdict: source_not_ready" in output


def test_visible_text_adapter_builds_macos_accessibility_script():
    adapter = load_script_module("visible_text_adapter_macos_for_test", ROOT / "scripts" / "read_visible_discord_text.py")

    script = adapter.build_macos_accessibility_script("Discord")

    assert 'process "Discord"' in script
    assert 'attribute "AXFocusedUIElement"' in script
    assert "focused UI element" not in script
    assert "set targetWindow to front window" in script
    assert "entire contents of targetWindow" in script
    assert "appendText" in script


def test_visible_text_adapter_builds_focused_only_macos_script():
    adapter = load_script_module(
        "visible_text_adapter_macos_focused_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script("Discord", focused_only=True)

    assert "set focusedOnly to true" in script
    assert "focused element text not found" in script
    assert "entire contents of targetWindow" in script


def test_visible_text_adapter_builds_no_focus_macos_script():
    adapter = load_script_module(
        "visible_text_adapter_macos_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script("Discord", focus_app=False)

    assert "set shouldFocusApp to false" in script
    assert "set frontmost to true" in script
    assert "if shouldFocusApp then" in script
    assert "set targetWindow to window 1" in script
    assert "focused-only requires app focus" in script


def test_visible_text_adapter_passes_timeout_to_source_command(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_timeout_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    class Completed:
        returncode = 0
        stdout = "member-a: 画面に見えている本文です\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    result = adapter.main(["--source-command", "discord-visible-text", "--timeout", "4"])

    assert result == 0
    assert calls[0][1]["timeout"] == 4
    assert "画面に見えている本文です" in capsys.readouterr().out


def test_visible_text_adapter_builds_macos_window_list_script():
    adapter = load_script_module(
        "visible_text_adapter_macos_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_window_list_script('Discord "Canary"')

    assert 'process "Discord \\"Canary\\""' in script
    assert "repeat with candidateWindow in windows" in script
    assert "candidateName" in script
    assert "windowIndex" in script


def test_visible_text_adapter_lists_macos_windows(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_list_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    class Completed:
        returncode = 0
        stdout = "1\tDiscord\n2\tNexus AI - Discord\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    result = adapter.main(["--list-macos-windows", "--window-name-contains", "Nexus", "--timeout", "4"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][1]["timeout"] == 4
    assert "Discord window candidates:" in output
    assert "- 1: title_present=true title_length=7 hint_match=false" in output
    assert "- 2: title_present=true title_length=18 hint_match=true" in output
    assert "Nexus AI - Discord" not in output


def test_visible_text_adapter_preflights_empty_macos_window_list(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_preflight_empty_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "")

    with pytest.raises(RuntimeError, match="Discord window candidates not found"):
        adapter.preflight_macos_window_selection("Discord", timeout=3)


def test_visible_text_adapter_preflights_macos_window_index(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_preflight_index_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n")

    with pytest.raises(RuntimeError, match="window index not found before scan: 2; candidate_count=1"):
        adapter.preflight_macos_window_selection("Discord", window_index=2, timeout=3)


def test_visible_text_adapter_can_show_macos_window_names_with_opt_in(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_show_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    class Completed:
        returncode = 0
        stdout = "1\tDiscord\n2\tNexus AI - Discord\n"
        stderr = ""

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--list-macos-windows", "--show-window-names"])
    output = capsys.readouterr().out

    assert result == 0
    assert "- 1: Discord" in output
    assert "- 2: Nexus AI - Discord" in output


def test_visible_text_adapter_blocks_sensitive_macos_window_names_when_shown(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_sensitive_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    class Completed:
        returncode = 0
        stdout = "1\tDiscord 123456789012345678\n"
        stderr = ""

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--list-macos-windows", "--show-window-names"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "安全監査に失敗" in captured.err
    assert "discord_snowflake_id" in captured.err


def test_visible_text_adapter_can_select_macos_window_by_name_hint():
    adapter = load_script_module(
        "visible_text_adapter_macos_window_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script('Discord "Canary"', "nexus-ai")

    assert 'process "Discord \\"Canary\\""' in script
    assert 'set windowNameHint to "nexus-ai"' in script
    assert "repeat with candidateWindow in windows" in script
    assert "if candidateName contains windowNameHint" in script
    assert "window not found" in script
    assert 'process "Discord "Canary""' not in script


def test_visible_text_adapter_can_select_macos_window_by_index():
    adapter = load_script_module(
        "visible_text_adapter_macos_window_index_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script("Discord", window_index=2)

    assert "set targetWindowIndex to 2" in script
    assert "window index not found" in script
    assert "set targetWindow to window targetWindowIndex" in script


def test_visible_text_adapter_auto_fallback_tries_focused_before_full(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        calls.append(
            {
                "process_name": process_name,
                "window_name_contains": window_name_contains,
                "window_index": window_index,
                "timeout": timeout,
                "focused_only": focused_only,
                "focus_app": focus_app,
            }
        )
        if focused_only:
            return ""
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    text = adapter.read_macos_accessibility_auto("Discord", window_index=2, timeout=3)

    assert text == "member-a: 画面に見えている本文です\n"
    assert calls == [
        {
            "process_name": "Discord",
            "window_name_contains": "",
            "window_index": 2,
            "timeout": 3,
            "focused_only": True,
            "focus_app": True,
        },
        {
            "process_name": "Discord",
            "window_name_contains": "",
            "window_index": 2,
            "timeout": 3,
            "focused_only": False,
            "focus_app": True,
        },
    ]


def test_visible_text_adapter_auto_fallback_reports_attempts(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_error_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        if focused_only:
            raise RuntimeError("focused element text not found")
        return ""

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    with pytest.raises(RuntimeError, match="selected focused: focused element text not found"):
        adapter.read_macos_accessibility_auto("Discord", timeout=3)


def test_visible_text_adapter_auto_fallback_can_skip_focus(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        calls.append({"focused_only": focused_only, "focus_app": focus_app, "window_index": window_index})
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    text = adapter.read_macos_accessibility_auto("Discord", window_index=2, timeout=3, focus_app=False)

    assert text == "member-a: 画面に見えている本文です\n"
    assert calls == [{"focused_only": False, "focus_app": False, "window_index": 2}]


def test_visible_text_adapter_no_focus_does_not_fallback_to_front_window(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_no_focus_no_front_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        calls.append({"focused_only": focused_only, "focus_app": focus_app, "window_index": window_index})
        return ""

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    with pytest.raises(RuntimeError, match="selected full: empty"):
        adapter.read_macos_accessibility_auto("Discord", window_index=2, timeout=3, focus_app=False)

    assert calls == [{"focused_only": False, "focus_app": False, "window_index": 2}]


def test_visible_text_adapter_cli_uses_macos_auto_without_focus_by_default(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_cli_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append((process_name, window_name_contains, window_index, timeout, focus_app))
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "preflight_macos_window_selection", lambda *args, **kwargs: [(2, "Discord")])
    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--macos-accessibility-auto", "--window-index", "2", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [("Discord", "", 2, 3.0, False)]
    assert output == "member-a: 画面に見えている本文です\n"


def test_visible_text_adapter_cli_can_focus_macos_auto_with_explicit_opt_in(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_cli_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append((process_name, window_name_contains, window_index, timeout, focus_app))
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "preflight_macos_window_selection", lambda *args, **kwargs: [(2, "Discord")])
    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--macos-accessibility-auto", "--focus-app", "--window-index", "2", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [("Discord", "", 2, 3.0, True)]
    assert output == "member-a: 画面に見えている本文です\n"


def test_visible_text_adapter_no_focus_flag_remains_backward_compatible(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_cli_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append((process_name, window_name_contains, window_index, timeout, focus_app))
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "preflight_macos_window_selection", lambda *args, **kwargs: [(2, "Discord")])
    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--macos-accessibility-auto", "--no-focus", "--window-index", "2", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [("Discord", "", 2, 3.0, False)]
    assert output == "member-a: 画面に見えている本文です\n"


def test_visible_text_adapter_probe_reports_safe_route_without_body(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_probe_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n2\tNexus AI\n")

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append({"window_index": window_index, "focus_app": focus_app})
        if window_index == 1:
            return ""
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--probe-macos-accessibility", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [
        {"window_index": 1, "focus_app": False},
        {"window_index": 2, "focus_app": False},
    ]
    assert "Discord Accessibility route probe:" in output
    assert "focus_app: false" in output
    assert "- 1: status=empty chars=0 lines=0" in output
    assert "- 2: status=ready" in output
    assert "画面に見えている本文" not in output
    assert "Nexus AI" not in output


def test_visible_text_adapter_probe_blocks_unsafe_route_without_body(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_probe_unsafe_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n")
    monkeypatch.setattr(
        adapter,
        "read_macos_accessibility_auto",
        lambda *args, **kwargs: "member-a: https://discord.com/api/webhooks/123456789012345678/token\n",
    )

    result = adapter.main(["--probe-macos-accessibility", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 2
    assert "status=unsafe" in output
    assert "issues=discord_webhook_url" in output
    assert "webhooks/123456789012345678" not in output


def test_visible_text_adapter_probe_redacts_sensitive_error_reason(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_probe_redact_error_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n")

    def fake_auto(*args, **kwargs):
        raise RuntimeError("failed near https://discord.com/api/webhooks/123456789012345678/token")

    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--probe-macos-accessibility", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 2
    assert "reason=安全監査により詳細を省略しました。" in output
    assert "webhooks/123456789012345678" not in output


def test_visible_text_adapter_source_command_error_redacts_sensitive_stderr(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_source_command_error_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failed near https://discord.com/api/webhooks/123456789012345678/token"

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--source-command", "private-adapter"])
    captured = capsys.readouterr()

    assert result == 1
    assert "安全監査により詳細を省略しました。" in captured.err
    assert "webhooks/123456789012345678" not in captured.err
    assert "private-adapter" not in captured.err


def test_gh_guard_parses_github_remote_owner():
    gh_guard = load_script_module("gh_guard_for_test", ROOT / "scripts" / "gh_guard.py")

    assert gh_guard.parse_github_owner("https://github.com/nexus-ai-2045/discord-context-bridge.git") == "nexus-ai-2045"
    assert gh_guard.parse_github_owner("git@github.com:nexus-ai-2045/discord-context-bridge.git") == "nexus-ai-2045"
    assert gh_guard.parse_github_owner("https://example.com/nexus-ai-2045/discord-context-bridge.git") is None


def test_gh_guard_subprocess_output_uses_utf8_replace(monkeypatch):
    gh_guard = load_script_module("gh_guard_run_encoding_for_test", ROOT / "scripts" / "gh_guard.py")
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return Completed()

    monkeypatch.setattr(gh_guard.subprocess, "run", fake_run)

    gh_guard.run(["gh", "auth", "status"])

    assert calls[0]["encoding"] == "utf-8"
    assert calls[0]["errors"] == "replace"


def test_gh_guard_parses_active_account_from_auth_status():
    gh_guard = load_script_module("gh_guard_auth_status_for_test", ROOT / "scripts" / "gh_guard.py")
    output = """
github.com
  ✓ Logged in to github.com account nexus-ai-2045 (keyring)
  - Active account: true
  - Git operations protocol: https

  ✓ Logged in to github.com account old-account (keyring)
  - Active account: false
"""

    assert gh_guard.parse_active_account(output) == "nexus-ai-2045"


def test_gh_guard_parses_active_account_from_failed_auth_status():
    gh_guard = load_script_module("gh_guard_failed_auth_status_for_test", ROOT / "scripts" / "gh_guard.py")
    output = """
github.com
  X Failed to log in to github.com account nexus-ai-2045 (default)
  - Active account: true
  - The token in default is invalid.

  X Failed to log in to github.com account old-account (default)
  - Active account: false
"""

    assert gh_guard.parse_active_account(output) == "nexus-ai-2045"


def test_gh_guard_reports_account_drift_and_switch(monkeypatch):
    gh_guard = load_script_module("gh_guard_switch_for_test", ROOT / "scripts" / "gh_guard.py")
    accounts = ["old-account", "nexus-ai-2045"]
    switched = []

    monkeypatch.setattr(gh_guard, "get_remote_url", lambda remote: "https://github.com/nexus-ai-2045/discord-context-bridge.git")
    monkeypatch.setattr(gh_guard, "get_active_gh_account", lambda: (accounts.pop(0), True, ""))
    monkeypatch.setattr(gh_guard, "switch_gh_account", lambda owner: switched.append(owner))
    monkeypatch.setattr(
        gh_guard,
        "get_git_config",
        lambda key: {
            "user.name": "nexus-ai-2045",
            "user.email": "273569186+nexus-ai-2045@users.noreply.github.com",
        }.get(key, ""),
    )

    report = gh_guard.build_report("origin", switch=True)

    assert report["ok"] is True
    assert report["expected_owner"] == "nexus-ai-2045"
    assert report["expected_repository"] == "discord-context-bridge"
    assert report["actual_repository"] == "discord-context-bridge"
    assert report["active_account_before"] == "old-account"
    assert report["active_account_after"] == "nexus-ai-2045"
    assert report["auth_status_ok_after"] is True
    assert report["git_author_matches"] is True
    assert report["switched"] is True
    assert switched == ["nexus-ai-2045"]


def test_gh_guard_rejects_wrong_repository_and_unexpected_identity(monkeypatch):
    gh_guard = load_script_module("gh_guard_wrong_repo_for_test", ROOT / "scripts" / "gh_guard.py")

    monkeypatch.setattr(gh_guard, "get_remote_url", lambda remote: "https://github.com/nexus-ai-2045/nexus_ai.git")
    monkeypatch.setattr(gh_guard, "get_active_gh_account", lambda: ("unexpected-account", True, ""))
    monkeypatch.setattr(
        gh_guard,
        "get_git_config",
        lambda key: {"user.name": "unexpected-user", "user.email": "unexpected@example.com"}.get(key, ""),
    )

    report = gh_guard.build_report("origin", switch=False)

    assert report["ok"] is False
    assert report["actual_repository"] == "nexus_ai"
    assert report["account_matches"] is False
    assert report["repository_matches"] is False
    assert report["git_author_matches"] is False


def test_gh_guard_accepts_current_nexus_ai_author(monkeypatch):
    gh_guard = load_script_module("gh_guard_current_author_for_test", ROOT / "scripts" / "gh_guard.py")

    monkeypatch.setattr(gh_guard, "get_remote_url", lambda remote: "https://github.com/nexus-ai-2045/discord-context-bridge.git")
    monkeypatch.setattr(gh_guard, "get_active_gh_account", lambda: ("nexus-ai-2045", True, ""))
    monkeypatch.setattr(
        gh_guard,
        "get_git_config",
        lambda key: {
            "user.name": "nexus_ai",
            "user.email": "nexus.ai.2045@gmail.com",
        }.get(key, ""),
    )

    report = gh_guard.build_report("origin", switch=False)

    assert report["ok"] is True
    assert report["git_author_matches"] is True


def test_gh_guard_rejects_configured_forbidden_identity_without_echoing_value(monkeypatch):
    gh_guard = load_script_module("gh_guard_forbidden_identity_for_test", ROOT / "scripts" / "gh_guard.py")

    monkeypatch.setattr(gh_guard, "get_remote_url", lambda remote: "https://github.com/nexus-ai-2045/discord-context-bridge.git")
    monkeypatch.setattr(gh_guard, "get_active_gh_account", lambda: ("nexus-ai-2045", True, ""))
    monkeypatch.setattr(
        gh_guard,
        "get_git_config",
        lambda key: {
            "user.name": "nexus-ai-2045",
            "user.email": "273569186+nexus-ai-2045@users.noreply.github.com",
        }.get(key, ""),
    )
    monkeypatch.setattr(
        gh_guard,
        "collect_history_text",
        lambda refs: "abc123\nAuthor: old-operator <old-operator@example.com>\nCo-authored-by: old-operator <old-operator@example.com>\n",
    )

    report = gh_guard.build_report(
        "origin",
        switch=False,
        forbidden_identities=["old-operator@example.com"],
        history_refs=["HEAD"],
    )

    assert report["ok"] is False
    assert report["forbidden_identity_count"] == 1
    assert report["forbidden_identity_match_count"] == 1
    assert report["forbidden_identity_matches"] == [
        {"source": "git_history", "identity_label": "configured-forbidden-identity"}
    ]
    assert "old-operator@example.com" not in json.dumps(report, ensure_ascii=False)


def test_gh_guard_parses_forbidden_identities_from_args_and_env():
    gh_guard = load_script_module("gh_guard_forbidden_parse_for_test", ROOT / "scripts" / "gh_guard.py")

    assert gh_guard.parse_forbidden_identities(
        ["old-operator; legacy-user"],
        "someone@example.com\nlegacy-user",
    ) == ["legacy-user", "old-operator", "someone@example.com"]


def test_pr_language_gate_rejects_english_default_template():
    gate = load_script_module("check_pr_language_for_test", ROOT / "scripts" / "check_pr_language.py")
    title = "[codex] Harden Discord bridge store safety"
    body = "## Summary\n- change\n\n## Validation\n- tests\n\n## Notes\n- none\n"

    issues = gate.validate_pr_language(title, body)

    assert "title_has_no_japanese" in issues
    assert "missing_heading:## 概要" in issues
    assert "english_default_heading:## Summary" in issues


def test_pr_language_gate_requires_human_japanese_approval_marker():
    gate = load_script_module("check_pr_language_approval_for_test", ROOT / "scripts" / "check_pr_language.py")
    title = "[codex] Discord bridge の公開前ガードを強化"
    body = "## 概要\n- 変更内容です。\n\n## 検証\n- テスト済みです。\n\n## 境界\n- 送信操作は対象外です。\n\n## 日本語レビュー\n- 確認待ちです。\n\njapanese_pr_ok: no\n"

    issues = gate.validate_pr_language(title, body)

    assert "missing_human_japanese_approval:japanese_pr_ok_yes" in issues


def test_pr_language_gate_accepts_japanese_pr_metadata():
    gate = load_script_module("check_pr_language_ok_for_test", ROOT / "scripts" / "check_pr_language.py")
    title = "[codex] Discord bridge の公開前ガードを強化"
    body = "## 概要\n- 変更内容です。\n\n## 検証\n- テスト済みです。\n\n## 境界\n- 送信操作は対象外です。\n\n## 日本語レビュー\n- 日本語レビュー承認済み。\n\njapanese_pr_ok: yes\n"

    assert gate.validate_pr_language(title, body) == []


def test_pr_language_gate_accepts_json_flag_for_cli_consistency():
    gate = load_script_module("check_pr_language_json_arg_for_test", ROOT / "scripts" / "check_pr_language.py")

    args = gate.build_parser().parse_args(["--title", "日本語PR", "--body-file", "body.md", "--json"])

    assert args.json is True


def test_pr_language_gate_allows_dependabot_dependency_updates():
    gate = load_script_module("check_pr_language_dependabot_for_test", ROOT / "scripts" / "check_pr_language.py")
    title = "Bump the github-actions group with 2 updates"
    body = "Bumps the github-actions group with 2 updates. This PR was generated by Dependabot."

    assert gate.validate_pr_language(
        title,
        body,
        author_login="dependabot[bot]",
        head_ref_name="dependabot/github_actions/github-actions-123",
    ) == []


def test_pr_language_gate_does_not_allow_human_english_pr_as_dependabot_update():
    gate = load_script_module("check_pr_language_human_english_for_test", ROOT / "scripts" / "check_pr_language.py")
    title = "Bump the github-actions group with 2 updates"
    body = "Bumps the github-actions group with 2 updates. This PR was generated by Dependabot."

    issues = gate.validate_pr_language(title, body, author_login="human-user", head_ref_name="feature/deps")

    assert "title_has_no_japanese" in issues
    assert "missing_human_japanese_approval:japanese_pr_ok_yes" in issues


def test_cli_audit_store_returns_nonzero_when_unsafe(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    append_event(
        DiscordEvent(
            observed_at="2026-01-01T00:00:00+00:00",
            source="test",
            guild_label="example-community",
            channel_label="general",
            author_label="member-a",
            text_snippet="https://discord.com/api/webhooks/123456789012345678/token",
        ),
        store,
    )

    result = cli_main(["--store", str(store), "audit-store"])
    output = capsys.readouterr().out

    assert result == 2
    assert '"safe_for_tunnel": false' in output


def test_cli_import_clipboard_reads_local_clipboard_command(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    store = tmp_path / "events.ndjson"

    result = cli_main(
        [
            "--store",
            str(store),
            "import-clipboard",
            "--clipboard-command",
            "pbpaste",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["pbpaste"]
    assert calls[0][1]["capture_output"] is True
    assert '"parsed": 3' in output
    assert len(load_events(store)) == 3


def test_cli_watch_clipboard_imports_only_changed_text(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    fixture = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
    outputs = [fixture, fixture, "member-d: 追加の前提です"]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(outputs.pop(0))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.time, "sleep", lambda interval: None)
    store = tmp_path / "events.ndjson"

    result = cli_main(
        [
            "--store",
            str(store),
            "watch-clipboard",
            "--clipboard-command",
            "pbpaste",
            "--max-polls",
            "3",
            "--interval",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert len(calls) == 3
    assert '"polls": 3' in output
    assert '"changed": 2' in output
    assert "clipboard の変化を取り込みました。" in output
    assert "clipboard 取り込み" in output
    assert "clipboard 監視を終了しました。" in output
    assert "clipboard 監視完了" in output
    assert len(load_events(store)) == 4


def test_mcp_dependency_error_is_japanese(monkeypatch):
    def missing_fastmcp():
        raise RuntimeError(mcp_server.MCP_DEPENDENCY_HELP)

    monkeypatch.setattr(mcp_server, "_load_fastmcp", missing_fastmcp)

    with pytest.raises(RuntimeError, match="MCP server を使うには"):
        mcp_server.build_server()


def test_mcp_server_registers_context_tools(monkeypatch, tmp_path):
    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings
            self.tools = {}

        def tool(self):
            def register(func):
                self.tools[func.__name__] = func
                return func

            return register

        def run(self):
            raise AssertionError("unit test should not start stdio server")

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    server = mcp_server.build_server(
        store=tmp_path / "events.ndjson",
        context_store=tmp_path / "context-library.json",
        snapshot_store=tmp_path / "text-snapshots.ndjson",
    )

    assert server.name == "discord-context-bridge"
    assert server.settings["host"] == "127.0.0.1"
    assert server.settings["port"] == 8000
    assert server.settings["streamable_http_path"] == "/mcp"
    assert sorted(server.tools) == [
        "audit_context_library_before_tunnel",
        "audit_event_store_before_tunnel",
        "chew_discord_context_from_visible_text",
        "closeout_discord_send_after_human_action",
        "get_context_operating_mode_from_visible_text",
        "get_context_passport_from_discord_url",
        "get_context_passport_from_visible_text",
        "get_fast_briefing",
        "guide_reply_from_visible_text",
        "import_visible_discord_text",
        "list_context_library_entries",
        "plan_discord_url_read",
        "plan_reply_context_before_draft",
        "review_reply_before_send",
        "snapshot_chrome_extension_visible_text",
        "snapshot_discord_url_text",
        "stage_discord_send_before_human_action",
        "upsert_context_library_entry",
        "verify_chrome_extension_fill_only_before_action",
    ]

    imported = server.tools["import_visible_discord_text"]("member-a: 前提を確認したいです", dry_run=True)
    audit = server.tools["audit_event_store_before_tunnel"]()
    saved_context = server.tools["upsert_context_library_entry"](
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        source="fixture",
    )
    context_list = server.tools["list_context_library_entries"]()
    context_audit = server.tools["audit_context_library_before_tunnel"]()
    review = server.tools["review_reply_before_send"]("前提を確認してから返信します")
    guide = server.tools["guide_reply_from_visible_text"](
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "公開時期の前提を確認してから返信します。",
        understanding_confirmed=True,
    )
    passport = server.tools["get_context_passport_from_visible_text"](
        PASSPORT_FIXTURE.read_text(encoding="utf-8"),
        server_context_key="nexus",
    )
    plan = server.tools["plan_discord_url_read"]("https://discord.com/channels/1/2/3")
    snapshot = server.tools["snapshot_chrome_extension_visible_text"](
        messages=[{"author": "member-a", "text": "見えている本文"}],
        url="https://discord.com/channels/1/2/3",
    )
    url_passport = server.tools["get_context_passport_from_discord_url"](
        "https://discord.com/channels/1/2/3",
        text=PASSPORT_FIXTURE.read_text(encoding="utf-8"),
    )
    staging_packet = build_discord_send_staging_packet(
        "公開時期の前提を確認してから返信します。",
        parse_visible_text(FIXTURE.read_text(encoding="utf-8")),
        mode="reply",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678/323456789012345678",
        understanding_confirmed=True,
    )
    mcp_blocked_dry_run = server.tools["verify_chrome_extension_fill_only_before_action"](
        staging_packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )
    mcp_ready_dry_run = server.tools["verify_chrome_extension_fill_only_before_action"](
        staging_packet,
        socket_preflight=True,
        target_url_verified=True,
        socket_after_navigation=True,
        latest_target_snapshot_confirmed=True,
        reply_ui_candidates=1,
        draft_matches_copy_block=True,
        socket_pre_send=True,
    )

    assert imported["language"] == "ja"
    assert imported["dry_run"] is True
    assert audit["safe_for_tunnel"] is True
    assert saved_context["created"] is True
    assert context_list["count"] == 1
    assert context_audit["safe_for_tunnel"] is True
    assert review["language"] == "ja"
    assert review["quick_verdict"] == "reply-context-blocked"
    assert guide["message"] == "Discord 返信ガイドを作成しました。"
    assert passport["message"] == "スレッド文脈カードを作成しました。"
    assert passport["people_temperature"] == "serious"
    assert passport["external_context_used"] is True
    assert plan["ok_to_open"] is True
    assert snapshot["source"] == "chrome_extension_dom"
    assert snapshot["visible_text_saved"] is True
    assert snapshot["reply_context_gate"]["reply_generation_allowed"] is False
    assert "thread_root_missing" in snapshot["reply_context_gate"]["blockers"]
    assert url_passport["snapshot"]["visible_text_saved"] is True
    assert url_passport["reply_context_gate"]["reply_generation_allowed"] is False
    assert "latest_target_snapshot_not_confirmed" in mcp_blocked_dry_run["blockers"]
    assert mcp_ready_dry_run["dry_run_status"] == "ready_to_fill"
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])


def test_http_mcp_entrypoint_uses_streamable_http(monkeypatch, tmp_path):
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

        def run(self, **kwargs):
            calls.append((self, kwargs))

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    result = mcp_server.main_http(
        [
            "--store",
            str(tmp_path / "events.ndjson"),
            "--host",
            "0.0.0.0",
            "--port",
            "8787",
            "--path",
            "/mcp",
        ]
    )

    assert result == 0
    server, run_kwargs = calls[0]
    assert server.settings["host"] == "0.0.0.0"
    assert server.settings["port"] == 8787
    assert server.settings["streamable_http_path"] == "/mcp"
    assert run_kwargs == {"transport": "streamable-http", "mount_path": "/mcp"}
    assert "streamable HTTP MCP server" in mcp_server.build_http_parser().format_help()


def test_http_mcp_require_safe_store_blocks_unsafe_store(monkeypatch, tmp_path):
    store = tmp_path / "events.ndjson"
    append_event(
        DiscordEvent(
            observed_at="2026-01-01T00:00:00+00:00",
            source="test",
            guild_label="example-community",
            channel_label="general",
            author_label="member-a",
            text_snippet="https://discord.com/api/webhooks/123456789012345678/token",
        ),
        store,
    )
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    with pytest.raises(SystemExit, match="保存データの監査に失敗しました"):
        mcp_server.main_http(
            [
                "--store",
                str(store),
                "--require-safe-store",
            ],
            run=lambda server: calls.append(server),
        )

    assert calls == []


def test_http_mcp_require_safe_store_allows_safe_store(monkeypatch, tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text("member-a: 前提を確認してから返信します", path=store)
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    result = mcp_server.main_http(
        [
            "--store",
            str(store),
            "--require-safe-store",
        ],
        run=lambda server: calls.append(server),
    )

    assert result == 0
    assert len(calls) == 1
    assert "--require-safe-store" in mcp_server.build_http_parser().format_help()
