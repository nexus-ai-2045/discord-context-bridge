from discord_context_bridge.ingest import ingest_capture
from discord_context_bridge.core import load_text_snapshots
from discord_context_bridge.target_registry import load_target_registry, resolve_target


RAW_CAPTURE = {
    "schema": "dcb.raw_capture.v1",
    "site": "discord",
    "adapter_id": "discord.v1",
    "adapter_version": "1",
    "source_url": "https://discord.com/channels/1/2/3",
    "captured_at": "2026-01-01T00:00:00+00:00",
    "capture_method": "structured_dom",
    "read_scope": "visible_viewport",
    "messages": [
        {
            "ordinal": 0,
            "visible_timestamp": "12:00",
            "author_label": "Alice",
            "body_text": "hello there",
            "attachments": [],
            "extraction_confidence": "high",
        },
        {
            "ordinal": 1,
            "visible_timestamp": "12:01",
            "author_label": "Bob",
            "body_text": "hi Alice",
            "attachments": [],
            "extraction_confidence": "high",
        },
    ],
}

VISIBLE_MESSAGE_RECORD = {
    "schema": "dcb.visible_message_record.v1",
    "url": "https://discord.com/channels/9/8/7",
    "title": "general",
    "source": "chrome_dom_visible_range",
    "message": {
        "message_id": "m-1",
        "author_label": "Carol",
        "visible_timestamp": "13:00",
        "body_text": "single message observation",
    },
}

INCREMENTAL_MESSAGE = {
    "schema": "dcb.incremental_visible_message.v1",
    "url": "https://discord.com/channels/9/8/7",
    "title": "general",
    "source": "chrome_dom_visible_range",
    "delta_kind": "new_message",
    "message": {
        "message_id": "m-2",
        "author_label": "Dave",
        "visible_timestamp": "13:05",
        "body_text": "an incremental message",
    },
}


def test_dry_run_does_not_write_stores(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(RAW_CAPTURE, snapshot_store=snapshot_store, registry_store=registry_store, apply=False)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["events_appended"] == 0
    assert result["events_pending"] == 2
    assert not snapshot_store.exists()
    assert not registry_store.exists()


def test_apply_writes_message_observation_events(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(RAW_CAPTURE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["events_appended"] == 2
    assert result["duplicates"] == 0

    records = load_text_snapshots(snapshot_store)
    assert len(records) == 2
    first, second = records
    assert first["schema"] == "discord_context_bridge_text_snapshot_observation.v1"
    assert first["event_type"] == "message_observation"
    assert first["target_key"] == result["target_key"]
    assert first["stream_sequence"] == 1
    assert second["stream_sequence"] == 2
    assert second["previous_event_hash"] == first["event_hash"]
    assert first["text"] == "hello there"
    assert first["author_label"] == "Alice"
    assert first["duplicate_content"] is False

    registry_entries = load_target_registry(registry_store)
    assert len(registry_entries) == 1
    assert registry_entries[0]["source"] == "capture"
    resolved = resolve_target(result["target_key"], registry_store)
    assert resolved["url"] == "https://discord.com/channels/1/2/3"


def test_visible_message_record_ingests_single_message(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        VISIBLE_MESSAGE_RECORD, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    assert result["events_appended"] == 1
    records = load_text_snapshots(snapshot_store)
    assert records[0]["message_id"] == "m-1"
    assert records[0]["text"] == "single message observation"


def test_incremental_visible_message_ingests_single_message(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        INCREMENTAL_MESSAGE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    assert result["events_appended"] == 1
    records = load_text_snapshots(snapshot_store)
    assert records[0]["message_id"] == "m-2"


def test_duplicate_message_id_is_flagged_but_still_appended(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    ingest_capture(VISIBLE_MESSAGE_RECORD, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    result = ingest_capture(
        VISIBLE_MESSAGE_RECORD, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    assert result["events_appended"] == 1
    assert result["duplicates"] == 1

    records = load_text_snapshots(snapshot_store)
    assert len(records) == 2
    assert records[1]["duplicate_content"] is True
    assert records[1]["changed"] is False


def test_duplicate_content_without_message_id_uses_content_hash(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {
        "schema": "dcb.raw_capture.v1",
        "source_url": "https://discord.com/channels/1/1/1",
        "messages": [
            {"author_label": "Alice", "visible_timestamp": "1:00", "body_text": "same text"},
        ],
    }

    ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["duplicates"] == 1
    records = load_text_snapshots(snapshot_store)
    assert records[1]["duplicate_content"] is True


def test_unsupported_schema_returns_adapter_not_implemented(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        {"schema": "some.unknown.v1"}, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is False
    assert result["reason"] == "adapter_not_implemented"
    assert not snapshot_store.exists()
    assert not registry_store.exists()


def test_malformed_raw_capture_returns_validation_error_without_writing(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        {"schema": "dcb.raw_capture.v1", "messages": "not-a-list"},
        snapshot_store=snapshot_store,
        registry_store=registry_store,
        apply=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "raw_capture_messages_required"
    assert not snapshot_store.exists()


def test_stdout_shaped_result_excludes_raw_text(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(RAW_CAPTURE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert set(result) >= {"ok", "adapter", "events_appended", "duplicates", "target_key"}
    assert "text" not in result
    assert "hello there" not in str(result)


def test_secrets_are_redacted_before_storage(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    token = "mfa" + "." + "abcdefghijklmnopqrstuvwx1234567890abcd"
    payload = {
        "schema": "dcb.visible_message_record.v1",
        "url": "https://discord.com/channels/1/1/1",
        "message": {
            "message_id": "m-secret",
            "author_label": "Alice",
            "visible_timestamp": "1:00",
            "body_text": "here is a token: " + token,
        },
    }

    ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    stored_text = snapshot_store.read_text(encoding="utf-8")
    assert token not in stored_text
    assert "[discord token omitted]" in stored_text
