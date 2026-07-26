from discord_context_bridge.ingest import ingest_capture
from discord_context_bridge.core import (
    append_snapshot_like_record,
    canonical_event_hash,
    load_text_snapshots,
    snapshot_visible_text,
    stable_text_hash,
    utc_now,
)
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

# 実 capture artifact のフラットな shape (dcb.visible_message_record.v1)。
# `message` object へのネストは持たず、1 行が 1 メッセージそのもの。
VISIBLE_MESSAGE_RECORD = {
    "schema": "dcb.visible_message_record.v1",
    "target_key": "706e9c00f5c017de",
    "capture_id": "0bd43a4cf7bb02be87ee48ec",
    "ordinal": 0,
    "message_id": "m-1",
    "author_label": "Carol",
    "visible_timestamp": "13:00",
    "body_text": "single message observation",
    "attachments": [],
    "captured_at": "2026-01-01T00:00:00+00:00",
    "outbound_actions": "disabled",
}

# 実 capture artifact のフラットな shape (dcb.incremental_visible_message.v1)。
# `target_key` を持たず `stream_id` のみのケースがある (実データ実測)。
INCREMENTAL_MESSAGE = {
    "schema": "dcb.incremental_visible_message.v1",
    "stream_id": "01a13365dcd97a21-to-latest-4afd4aa0",
    "ordinal": 0,
    "message_id": "m-2",
    "visible_timestamp": "13:05",
    "author_label": "Dave",
    "body_text": "an incremental message",
    "links": [],
    "attachments": [],
    "captured_at": "2026-01-01T00:00:00+00:00",
    "outbound_actions": "disabled",
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
    assert second["previous_content_hash"] == first["content_hash"]
    assert first["text"] == "hello there"
    assert first["author_label"] == "Alice"
    assert first["duplicate_content"] is False
    assert first["duplicate_message_id"] is False

    registry_entries = load_target_registry(registry_store)
    assert len(registry_entries) == 1
    assert registry_entries[0]["source"] == "capture"
    resolved = resolve_target(result["target_key"], registry_store)
    assert resolved["url"] == "https://discord.com/channels/1/2/3"


def test_event_envelope_matches_core_snapshot_visible_text_fields(tmp_path):
    """H1: docs/operating-contract.md:92 の必須フィールド + core.snapshot_visible_text
    と同一 schema を名乗る以上の CloudEvents 系フィールドを揃える。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    ingest_capture(RAW_CAPTURE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    record = load_text_snapshots(snapshot_store)[0]

    for field in (
        "schema",
        "event_id",
        "event_type",
        "stream_id",
        "stream_sequence",
        "expected_previous_stream_sequence",
        "time",
        "content_hash",
        "previous_content_hash",
        "previous_event_hash",
        "event_hash",
        "acquisition_context",
        "specversion",
        "type",
        "subject",
        "datacontenttype",
        "dataschema",
    ):
        assert field in record, f"missing field: {field}"

    assert record["specversion"] == "1.0"
    assert record["subject"] == record["target_key"]
    assert record["time"] == record["captured_at"]
    assert record["dataschema"] == record["schema"]
    assert record["previous_content_hash"] is None


def test_visible_message_record_ingests_single_flat_message(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        VISIBLE_MESSAGE_RECORD, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    assert result["events_appended"] == 1
    assert result["target_key"] == "706e9c00f5c017de"
    records = load_text_snapshots(snapshot_store)
    assert records[0]["message_id"] == "m-1"
    assert records[0]["text"] == "single message observation"

    resolved = resolve_target("706e9c00f5c017de", registry_store)
    assert resolved is not None
    assert resolved["key_scheme"] == "url_hash_16"


def test_incremental_visible_message_ingests_single_flat_message(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        INCREMENTAL_MESSAGE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    assert result["events_appended"] == 1
    records = load_text_snapshots(snapshot_store)
    assert records[0]["message_id"] == "m-2"


def test_incremental_message_with_only_stream_id_derives_target_from_stream(tmp_path):
    """P1: stream_id を持つが target_key/url/title が無い実 capture artifact 実測ケース。
    本文ハッシュへフォールバックすると本文が変わるたびに別 target へ分裂していた
    (codex review #8)。stream_id から安定した target を導出する。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        INCREMENTAL_MESSAGE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    assert result["target_key"] == stable_text_hash(INCREMENTAL_MESSAGE["stream_id"])
    resolved = resolve_target(result["target_key"], registry_store)
    assert resolved is not None
    assert resolved["key_scheme"] == "stream_id_hash_16"


def test_stream_id_target_is_stable_across_batches_with_different_content(tmp_path):
    """P1 裏返し: 同一 stream_id の別バッチ (本文が違う) でも同じ target に集約される。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    batch_1 = [dict(INCREMENTAL_MESSAGE, message_id="m-1", body_text="first batch text")]
    batch_2 = [dict(INCREMENTAL_MESSAGE, message_id="m-2", body_text="second batch, totally different content")]

    result_1 = ingest_capture(batch_1, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    result_2 = ingest_capture(batch_2, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result_1["ok"] is True
    assert result_2["ok"] is True
    assert result_1["target_key"] == result_2["target_key"]


def test_ndjson_batch_rejects_mixed_stream_id(tmp_path):
    """P1: stream_id が行ごとに違う場合も混在として reject する。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    rows = [
        dict(INCREMENTAL_MESSAGE, message_id="m-a", stream_id="stream-a"),
        dict(INCREMENTAL_MESSAGE, message_id="m-b", stream_id="stream-b"),
    ]

    result = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "ndjson_batch_mixed_target"
    assert not snapshot_store.exists()


def test_ndjson_batch_rows_share_one_target(tmp_path):
    """R2/R1: NDJSON 複数行 (list[dict]) を 1 バッチとして 1 つの target へ ingest する。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    rows = [
        {
            "schema": "dcb.incremental_visible_message.v1",
            "stream_id": "01a13365dcd97a21-to-latest-4afd4aa0",
            "ordinal": index,
            "message_id": f"m-{index}",
            "visible_timestamp": f"13:0{index}",
            "author_label": "system-event",
            "body_text": f"message body {index}",
            "links": [],
            "attachments": [],
            "captured_at": "2026-01-01T00:00:00+00:00",
            "outbound_actions": "disabled",
        }
        for index in range(3)
    ]

    result = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=False)

    assert result["ok"] is True
    assert result["events_pending"] == 3

    applied = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    records = load_text_snapshots(snapshot_store)
    assert applied["events_appended"] == 3
    assert len({record["target_key"] for record in records}) == 1


def test_ndjson_batch_rejects_mixed_target_key(tmp_path):
    """P1: 複数 target_key が混在する NDJSON バッチを、先頭行の identity で全行
    取り込んでしまっていた (codex review #1)。混在を検知して reject する。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    rows = [
        {**VISIBLE_MESSAGE_RECORD, "message_id": "m-a", "target_key": "aaaa000000000001"},
        {**VISIBLE_MESSAGE_RECORD, "message_id": "m-b", "target_key": "bbbb000000000002"},
    ]

    result = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "ndjson_batch_mixed_target"
    assert not snapshot_store.exists()
    assert not registry_store.exists()


def test_ndjson_batch_rejects_mixed_url(tmp_path):
    """P1: target_key が無くても url が行ごとに違う場合も混在として reject する。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    row_a = {k: v for k, v in VISIBLE_MESSAGE_RECORD.items() if k != "target_key"}
    row_b = dict(row_a)
    rows = [
        dict(row_a, message_id="m-a", url="https://discord.com/channels/1/1/1"),
        dict(row_b, message_id="m-b", url="https://discord.com/channels/2/2/2"),
    ]

    result = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "ndjson_batch_mixed_target"
    assert not snapshot_store.exists()


def test_ndjson_batch_requires_consistent_schema(tmp_path):
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    rows = [
        {**INCREMENTAL_MESSAGE, "message_id": "m-a"},
        {**VISIBLE_MESSAGE_RECORD, "message_id": "m-b"},
    ]

    result = ingest_capture(rows, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "ndjson_batch_inconsistent_schema"
    assert not snapshot_store.exists()


def test_edit_of_same_message_id_is_changed_not_a_duplicate(tmp_path):
    """H3(a): 同一 message_id でも content_hash が違えば編集として changed=True になる。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    ingest_capture(VISIBLE_MESSAGE_RECORD, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    edited = dict(VISIBLE_MESSAGE_RECORD, body_text="an edited body text")
    result = ingest_capture(edited, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is True
    assert result["duplicates"] == 0

    records = load_text_snapshots(snapshot_store)
    second = records[1]
    assert second["duplicate_message_id"] is True
    assert second["duplicate_content"] is False
    assert second["changed"] is True


def test_duplicate_message_id_and_content_is_flagged_but_still_appended(tmp_path):
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
    assert records[1]["duplicate_message_id"] is True
    assert records[1]["duplicate_content"] is True
    assert records[1]["changed"] is False


def test_duplicate_content_without_message_id_uses_composite_identity(tmp_path):
    """H3(b): message_id 欠落時は (ordinal, author_label, visible_timestamp) を identity に混ぜる。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {
        "schema": "dcb.raw_capture.v1",
        "source_url": "https://discord.com/channels/1/1/1",
        "messages": [
            {"ordinal": 0, "author_label": "Alice", "visible_timestamp": "1:00", "body_text": "same text"},
        ],
    }

    ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["duplicates"] == 1
    records = load_text_snapshots(snapshot_store)
    assert records[1]["duplicate_content"] is True


def test_distinct_messages_with_identical_text_are_not_false_duplicates(tmp_path):
    """H3(b) 逆側: message_id が無くても ordinal/author/timestamp が違えば別 identity。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {
        "schema": "dcb.raw_capture.v1",
        "source_url": "https://discord.com/channels/1/1/1",
        "messages": [
            {"ordinal": 0, "author_label": "Alice", "visible_timestamp": "1:00", "body_text": "ok"},
            {"ordinal": 1, "author_label": "Bob", "visible_timestamp": "1:05", "body_text": "ok"},
        ],
    }

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["duplicates"] == 0
    records = load_text_snapshots(snapshot_store)
    assert records[1]["duplicate_content"] is False


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


def test_unsupported_schema_value_is_not_reflected_into_adapter_field(tmp_path):
    """C2: `adapter` フィールドは既知 schema 値の時だけ値を出し、それ以外は "unknown"。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    untrusted_schema = "<script>alert(1)</script>.v1"

    result = ingest_capture(
        {"schema": untrusted_schema}, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is False
    assert result["adapter"] == "unknown"
    assert untrusted_schema not in str(result)


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


def test_ingest_rejects_raw_capture_message_count_over_budget(tmp_path):
    """P2: dcb.raw_capture.v1 の schema 上限 (messages maxItems=2000) を ingest でも
    enforce する (codex review #7)。site_adapter_runtime.MAX_MESSAGES を参照する。"""
    from discord_context_bridge.site_adapter_runtime import MAX_MESSAGES

    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {
        "schema": "dcb.raw_capture.v1",
        "source_url": "https://discord.com/channels/1/1/1",
        "messages": [
            {"ordinal": i, "author_label": "A", "visible_timestamp": "t", "body_text": "x"}
            for i in range(MAX_MESSAGES + 1)
        ],
    }

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "message_limit_exceeded"
    assert not snapshot_store.exists()


def test_ingest_rejects_raw_capture_body_text_over_budget(tmp_path):
    """P2: 本文 1,000,000 字上限を ingest でも enforce する。"""
    from discord_context_bridge.site_adapter_runtime import MAX_BODY_TEXT_CHARS

    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {
        "schema": "dcb.raw_capture.v1",
        "source_url": "https://discord.com/channels/1/1/1",
        "messages": [
            {
                "ordinal": 0,
                "author_label": "A",
                "visible_timestamp": "t",
                "body_text": "x" * (MAX_BODY_TEXT_CHARS + 1),
            }
        ],
    }

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "message_body_limit_exceeded"
    assert not snapshot_store.exists()


def test_ingest_rejects_flat_row_body_text_over_budget(tmp_path):
    """P2: flat message schema 側 (実 capture artifact) でも同じ本文上限を enforce する。"""
    from discord_context_bridge.site_adapter_runtime import MAX_BODY_TEXT_CHARS

    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = dict(VISIBLE_MESSAGE_RECORD, body_text="x" * (MAX_BODY_TEXT_CHARS + 1))

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "message_body_limit_exceeded"
    assert not snapshot_store.exists()


def test_empty_messages_is_a_validation_error(tmp_path):
    """M1: 空 messages + url/title 無しで sha256("")[:16] のゴミ target を登録しない。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    result = ingest_capture(
        {"schema": "dcb.raw_capture.v1", "source_url": "https://discord.com/channels/1/1/1", "messages": []},
        snapshot_store=snapshot_store,
        registry_store=registry_store,
        apply=True,
    )

    assert result["ok"] is False
    assert result["reason"] == "messages_empty"
    assert not snapshot_store.exists()
    assert not registry_store.exists()


def test_url_and_title_are_stripped_before_storage(tmp_path):
    """M2: url/title を strip して保存する (core.py:2064-2065 と揃える)。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = dict(VISIBLE_MESSAGE_RECORD, url="  https://discord.com/channels/1/2/3  ", title="  general  ")

    ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    record = load_text_snapshots(snapshot_store)[0]

    assert record["url"] == "https://discord.com/channels/1/2/3"
    assert record["title"] == "general"


def test_fallback_identity_matches_core_normalized_content_derivation(tmp_path):
    """M3: url/title/target_key 無し時の fallback identity は core.py:2026 と同じ導出。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {
        "schema": "dcb.raw_capture.v1",
        "messages": [
            {"ordinal": 0, "author_label": "Alice", "visible_timestamp": "1:00", "body_text": "hello there"},
        ],
    }

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    core_style = snapshot_visible_text(
        messages=[{"author": "Alice", "text": "hello there", "timestamp": "1:00"}],
        path=tmp_path / "core-reference.ndjson",
    )

    assert result["target_key"] == core_style["target_key"]
    resolved = resolve_target(result["target_key"], registry_store)
    assert resolved["key_scheme"] == "content_fallback_16"


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
    payload = dict(VISIBLE_MESSAGE_RECORD, body_text="here is a token: " + token)

    ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    stored_text = snapshot_store.read_text(encoding="utf-8")
    assert token not in stored_text
    assert "[discord token omitted]" in stored_text


def test_captured_at_preserves_payload_timestamp_not_ingest_time(tmp_path):
    """P1: payload の captured_at を ingest 時刻で上書きし capture 時刻を破壊していた
    (codex review #4)。payload 側 captured_at を保持し、ingested_at とは区別する。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    original_captured_at = "2020-01-01T00:00:00+00:00"
    payload = dict(VISIBLE_MESSAGE_RECORD, captured_at=original_captured_at)

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is True
    record = load_text_snapshots(snapshot_store)[0]
    assert record["captured_at"] == original_captured_at
    assert record["observed_at"] == original_captured_at
    assert record["time"] == original_captured_at
    assert record["ingested_at"] != original_captured_at
    assert record["ingested_at"]


def test_captured_at_falls_back_to_now_when_payload_omits_it(tmp_path):
    """captured_at が payload に無い場合は ingest 時刻へフォールバックする (情報欠落なし)。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    payload = {k: v for k, v in VISIBLE_MESSAGE_RECORD.items() if k != "captured_at"}

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is True
    record = load_text_snapshots(snapshot_store)[0]
    assert record["captured_at"]
    assert record["time"] == record["captured_at"]
    assert record["ingested_at"]


def test_raw_capture_top_level_captured_at_is_preserved(tmp_path):
    """dcb.raw_capture.v1 は top-level captured_at を持つ (メッセージ個別ではない)。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    ingest_capture(RAW_CAPTURE, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)
    record = load_text_snapshots(snapshot_store)[0]

    assert record["captured_at"] == RAW_CAPTURE["captured_at"]
    assert record["ingested_at"] != RAW_CAPTURE["captured_at"]


def test_chain_continues_after_legacy_row_without_event_hash(tmp_path):
    """H2: event_hash 無し legacy 行の直後でも previous_event_hash が canonical_event_hash
    フォールバックでチェーン連続する (core.py:2031 と揃える)。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"
    legacy_record = {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "target_key": "706e9c00f5c017de",
        "content_hash": "legacy-hash",
        "captured_at": utc_now(),
        # event_hash はあえて含めない (旧 append_snapshot_like_record 経由の行を模す)
    }
    append_snapshot_like_record(
        snapshot_store, legacy_record, url="", target_key="706e9c00f5c017de"
    )
    expected_previous_hash = canonical_event_hash(
        [record for record in load_text_snapshots(snapshot_store) if record.get("target_key") == "706e9c00f5c017de"][
            0
        ]
    )

    result = ingest_capture(
        VISIBLE_MESSAGE_RECORD, snapshot_store=snapshot_store, registry_store=registry_store, apply=True
    )

    assert result["ok"] is True
    appended = [
        record for record in load_text_snapshots(snapshot_store) if record.get("event_type") == "message_observation"
    ][0]
    assert appended["previous_event_hash"] == expected_previous_hash
    assert appended["previous_event_hash"] != ""


def test_interop_with_core_snapshot_visible_text_stream_sequence(tmp_path):
    """core.snapshot_visible_text で書いた行の続きとして ingest の stream_sequence /
    previous_event_hash が正しく積み上がる。"""
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    registry_store = tmp_path / "targets.ndjson"

    core_style = snapshot_visible_text(
        text="core snapshot line",
        url="https://discord.com/channels/1/2/3",
        path=snapshot_store,
    )

    payload = dict(VISIBLE_MESSAGE_RECORD)
    payload.pop("target_key", None)
    payload["url"] = "https://discord.com/channels/1/2/3"

    result = ingest_capture(payload, snapshot_store=snapshot_store, registry_store=registry_store, apply=True)

    assert result["ok"] is True
    assert result["target_key"] == core_style["target_key"]
    records = [
        record for record in load_text_snapshots(snapshot_store) if record.get("target_key") == core_style["target_key"]
    ]
    assert len(records) == 2
    assert records[1]["stream_sequence"] == 2
    assert records[1]["previous_event_hash"] == records[0]["event_hash"]
    assert records[1]["previous_content_hash"] == records[0]["content_hash"]
