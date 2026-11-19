from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from discord_context_bridge.capture.store import CheckpointCorruptError
from discord_context_bridge.core import canonical_event_hash, load_content_snapshot_records, stable_text_hash
from discord_context_bridge.ingest import ingest_capture, message_metadata_hash, normalize_message_metadata
from discord_context_bridge.message_query import MessageQueryError, conversation_store_paths, query_messages
from discord_context_bridge.site_adapter_runtime import validate_artifact


OWNER = "600000000000000001"
OTHER = "600000000000000002"
MESSAGE = "700000000000000001"
URL = "https://discord.com/channels/100000000000000001/200000000000000001"


def metadata(author_id=OWNER, **changes):
    return {"author_id": author_id, "attachments": [], "deleted": False, "edited_at": None, **changes}


def capture(text="原文", *, message_id=MESSAGE, meta=None, source_url=URL):
    return {
        "schema": "dcb.raw_capture.v1", "site": "discord", "adapter_id": "discord.v1", "adapter_version": "1",
        "source_url": source_url, "captured_at": "2026-10-30T00:00:00+00:00", "capture_method": "gateway_live_event", "read_scope": "gateway_event",
        "messages": [{"ordinal": 0, "message_id": message_id, "visible_timestamp": "2026-10-30T00:00:00+00:00", "author_label": "試験利用者", "body_text": text, "attachments": [], "extraction_confidence": "high", "message_metadata": meta if meta is not None else metadata()}],
        "drift": {"detected": False, "items": []}, "safety": {"outbound_actions": "disabled", "raw_contains_private_text": True, "external_share_allowed": False},
    }


def save(tmp_path, payload):
    return ingest_capture(payload, snapshot_store=tmp_path / "snapshots.ndjson", registry_store=tmp_path / "targets.ndjson", apply=True)


def query(tmp_path, **changes):
    return query_messages(snapshot_store=tmp_path / "snapshots.ndjson", source_url=URL, participant_ids=[OWNER], **changes)


def test_native_ingest_edit_tombstone_query_retains_original_observations(tmp_path):
    first = capture("最初の原文", meta=metadata(attachments=[{"id": "800000000000000001", "filename": "資料.pdf", "size": 42, "content_type": "application/pdf"}]))
    validate_artifact(first, "dcb_raw_capture.v1.schema.json")
    assert save(tmp_path, first)["ok"]
    assert save(tmp_path, first)["duplicates"] == 1
    edited = capture("編集した原文", meta=metadata(edited_at="2026-10-30T00:01:00Z"))
    assert save(tmp_path, edited)["ok"]
    result = query(tmp_path, query="編集")
    assert len(result["messages"]) == 1
    assert result["messages"][0]["text"] == "編集した原文"
    assert result["messages"][0]["author_id"] == OWNER
    assert result["messages"][0]["edited_at"] == "2026-10-30T00:01:00Z"
    assert save(tmp_path, capture("", meta=metadata(author_id=None, deleted=True)))["ok"]
    assert query(tmp_path)["messages"] == []
    assert query(tmp_path)["omitted"]["deleted"] == 1
    assert query(tmp_path)["omitted"]["participant_denied"] == 0
    assert save(tmp_path, edited)["ok"]
    assert query(tmp_path)["messages"] == []
    rows = load_content_snapshot_records(tmp_path / "snapshots.ndjson")
    assert len(rows) == 5
    assert rows[0]["text"] == "最初の原文"
    assert rows[0]["message_metadata"]["attachments"][0]["filename"] == "資料.pdf"
    for row in rows:
        assert row["event_hash"] == canonical_event_hash(row)
        assert row["content_hash"] == stable_text_hash(row["text"])
        assert row["metadata_hash"] == message_metadata_hash(row["message_metadata"])
    assert rows[0]["metadata_changed"]
    assert not rows[1]["metadata_changed"]
    assert rows[0]["acquisition_context"]["source_kind"] == "Discord Gateway"
    assert rows[0]["acquisition_context"]["record_origin"] == "discord_gateway_message_event"


def test_metadata_only_change_keeps_text_hash_and_is_observed(tmp_path):
    save(tmp_path, capture())
    save(tmp_path, capture(meta=metadata(attachments=[{"id": "800000000000000001", "filename": "追加.txt"}])))
    rows = load_content_snapshot_records(tmp_path / "snapshots.ndjson")
    assert rows[0]["content_hash"] == rows[1]["content_hash"]
    assert rows[1]["duplicate_content"]
    assert not rows[1]["changed"]
    assert rows[1]["metadata_changed"]
    assert query(tmp_path)["messages"][0]["attachments"][0]["filename"] == "追加.txt"


@pytest.mark.parametrize("text", ["Authorization:", "Authorization: Bearer " + "T" * 32, "Bearer " + "B" * 32])
def test_string_redaction_does_not_corrupt_native_metadata_json(tmp_path, text):
    meta = metadata(attachments=[{"id": "800000000000000001", "filename": text}], embeds=[{"title": text, "description": text, "fields": [{"name": text, "value": text}]}], component_labels=[text])
    assert save(tmp_path, capture(text, meta=meta))["ok"]
    row = load_content_snapshot_records(tmp_path / "snapshots.ndjson")[0]
    assert json.loads(json.dumps(row, ensure_ascii=False)) == row
    assert row["message_metadata"]["author_id"] == OWNER
    assert row["metadata_hash"] == message_metadata_hash(row["message_metadata"])
    result = query(tmp_path)
    assert len(result["messages"]) == 1
    serialized = json.dumps(result, ensure_ascii=False)
    assert "T" * 32 not in serialized and "B" * 32 not in serialized


def test_empty_text_embed_and_component_post_remains_typed_reference_data(tmp_path):
    meta = metadata(embeds=[{"title": "今日の候補", "fields": [{"name": "作業", "value": "応募する"}]}], component_labels=["この内容で確定"])
    assert save(tmp_path, capture("", meta=meta))["ok"]
    result = query(tmp_path, query="応募する")
    assert result["messages"][0]["text"] == ""
    assert result["messages"][0]["embeds"] == meta["embeds"]
    assert result["messages"][0]["component_labels"] == meta["component_labels"]
    assert query(tmp_path, query="確定")["messages"]
    assert result["history_complete"] is False and result["partial"] is True


def test_query_never_falls_back_to_other_target_author_or_legacy_metadata(tmp_path):
    save(tmp_path, capture("本人の文"))
    save(tmp_path, capture("別人の秘密", message_id="700000000000000002", meta=metadata(author_id=OTHER)))
    save(tmp_path, capture("別部屋の秘密", message_id="700000000000000003", source_url=URL.replace("200000000000000001", "200000000000000002")))
    legacy = capture("作者未確認の旧記録", message_id="700000000000000004")
    legacy["capture_method"] = "structured_dom"
    del legacy["messages"][0]["message_metadata"]
    assert save(tmp_path, legacy)["ok"]
    result = query(tmp_path)
    assert [message["text"] for message in result["messages"]] == ["本人の文"]
    assert result["omitted"]["participant_denied"] == 1
    assert result["omitted"]["unknown_metadata"] == 1


def test_latest_unknown_metadata_does_not_revive_older_identified_version(tmp_path):
    save(tmp_path, capture("旧版"))
    legacy = capture("作者未確認の新版")
    legacy["capture_method"] = "structured_dom"
    del legacy["messages"][0]["message_metadata"]
    save(tmp_path, legacy)
    assert query(tmp_path)["messages"] == []


@pytest.mark.parametrize("mutation", [
    lambda value: value.update({"author_id": 123}),
    lambda value: value.update({"author_id": "not-an-id"}),
    lambda value: value.update({"deleted": "true"}),
    lambda value: value.update({"edited_at": "2026-10-30T00:00:00"}),
    lambda value: value.update({"edited_at": "not-a-date"}),
    lambda value: value.update({"unknown": "field"}),
    lambda value: value.update({"attachments": [{}]}),
    lambda value: value.update({"attachments": [{"id": "800000000000000001", "filename": "file", "size": True}]}),
    lambda value: value.update({"attachments": [{"id": "800000000000000001", "filename": "file", "url": "file:///secret"}]}),
    lambda value: value.update({"attachments": [{"id": "800000000000000001", "filename": "file", "url": "https://user:password@example.invalid/"}]}),
    lambda value: value.update({"attachments": [{"id": "800000000000000001", "filename": "file"}] * 2}),
    lambda value: value.update({"attachments": [{"id": "800000000000000001", "filename": "file"}] * 101}),
    lambda value: value.update({"embeds": [{"url": "https://example.invalid/"}]}),
    lambda value: value.update({"embeds": [{"description": "x" * 8192}] * 10}),
    lambda value: value.update({"component_labels": ["x" * 257]}),
    lambda value: value.update({"attachments": [{"id": "800000000000000001", "filename": "\ud800"}]}),
])
def test_metadata_type_and_budget_rejection_happens_before_store_write(tmp_path, mutation):
    meta = metadata()
    mutation(meta)
    assert save(tmp_path, capture(meta=meta))["ok"] is False
    assert not (tmp_path / "snapshots.ndjson").exists()


def test_metadata_total_batch_budget_is_enforced(tmp_path):
    payload = capture(meta=metadata(embeds=[{"description": "x" * 8192}] * 7))
    payload["messages"] = [{**deepcopy(payload["messages"][0]), "message_id": str(700000000000000100 + index), "ordinal": index} for index in range(20)]
    result = save(tmp_path, payload)
    assert result["ok"] is False
    assert result["reason"] == "message_metadata_batch_limit_exceeded"
    assert not (tmp_path / "snapshots.ndjson").exists()


def test_gateway_metadata_is_required_in_schema_and_ingest(tmp_path):
    payload = capture("")
    del payload["messages"][0]["message_metadata"]
    with pytest.raises(ValidationError):
        validate_artifact(payload, "dcb_raw_capture.v1.schema.json")
    assert save(tmp_path, payload)["ok"] is False


def test_query_output_count_and_serialized_size_are_bounded(tmp_path):
    for index in range(5):
        save(tmp_path, capture("公開可能な合成文" * 40, message_id=str(700000000000000100 + index)))
    assert len(query(tmp_path, limit=2)["messages"]) == 2
    bounded = query(tmp_path, max_chars=900)
    assert len(json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))) <= 900
    assert bounded["omitted"]["budget"] > 0


@pytest.mark.parametrize("arguments", [
    {"participant_ids": []}, {"participant_ids": ["unknown"]}, {"participant_ids": OWNER},
    {"query": "x" * 2001}, {"query": "\x00"}, {"limit": True}, {"limit": 101}, {"max_chars": 511}, {"max_chars": 20001},
])
def test_query_rejects_unknown_identity_and_unbounded_inputs(tmp_path, arguments):
    with pytest.raises(MessageQueryError):
        query_messages(snapshot_store=tmp_path / "snapshots.ndjson", source_url=URL, **{"participant_ids": [OWNER], **arguments})


def test_corrupt_canonical_chain_and_metadata_digest_never_fall_back(tmp_path):
    save(tmp_path, capture())
    path = tmp_path / "snapshots.ndjson"
    row = load_content_snapshot_records(path)[0]
    row["text"] = "改ざん"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(CheckpointCorruptError):
        query(tmp_path)
    row["text"] = "原文"
    row["message_metadata"]["author_id"] = OTHER
    row["event_hash"] = canonical_event_hash(row)
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(MessageQueryError, match="metadata_hash_invalid"):
        query(tmp_path)


def test_store_paths_use_the_actual_channel_or_thread_and_do_not_create_directories(tmp_path):
    root = tmp_path / "private-snapshots"
    snapshot, registry = conversation_store_paths(root, URL)
    assert snapshot == root / "discord/servers/100000000000000001/channels/200000000000000001/text-snapshots.ndjson"
    assert registry == snapshot.parent / "targets.ndjson"
    assert not root.exists()
    for invalid in [URL + "/700000000000000001", URL + "?x=1", URL + "/", URL.replace("discord.com", "canary.discord.com"), "https://discord.com/channels/100000000000000001/300000000000000001/threads/200000000000000001"]:
        with pytest.raises(MessageQueryError):
            conversation_store_paths(root, invalid)


def test_source_name_does_not_label_arbitrary_adapter_as_gateway(tmp_path):
    payload = capture()
    payload["capture_method"] = "user_gateway_live_event"
    save(tmp_path, payload)
    row = load_content_snapshot_records(tmp_path / "snapshots.ndjson")[0]
    assert row["acquisition_context"]["source_kind"] == "unknown"


def test_latest_message_cursor_uses_all_native_target_observations_before_query_filters(tmp_path):
    save(tmp_path, capture("検索に一致する文"))
    save(tmp_path, capture("別参加者", message_id="700000000000000009", meta=metadata(author_id=OTHER)))
    save(tmp_path, capture("", message_id="700000000000000010", meta=metadata(author_id=None, deleted=True)))
    save(tmp_path, capture("別target", message_id="700000000000000099", source_url=URL.replace("200000000000000001", "200000000000000002")))
    legacy = capture("旧形式", message_id="700000000000000999")
    legacy["capture_method"] = "structured_dom"
    del legacy["messages"][0]["message_metadata"]
    save(tmp_path, legacy)
    result = query(tmp_path, query="検索", limit=1, max_chars=900)
    assert len(result["messages"]) == 1
    assert result["latest_message_id"] == "700000000000000010"
    assert query(tmp_path, query="存在しない語")["latest_message_id"] == "700000000000000010"
    assert query(tmp_path, query="存在しない語")["messages"] == []


def test_cursor_omits_legacy_and_malformed_native_ids(tmp_path):
    assert query(tmp_path)["latest_message_id"] is None
    save(tmp_path, capture())
    path = tmp_path / "snapshots.ndjson"
    row = load_content_snapshot_records(path)[0]
    row["message_id"] = "malformed-id"
    row["event_hash"] = canonical_event_hash(row)
    path.write_text(json.dumps(row) + "\n")
    result = query(tmp_path)
    assert result["latest_message_id"] is None
    assert result["messages"] == []


def test_later_ingested_old_page_does_not_push_old_messages_ahead_of_recent_conversation(tmp_path):
    newer = capture("新しい投稿", message_id="700000000000000009")
    newer["messages"][0]["visible_timestamp"] = "2026-10-30T09:00:00+09:00"
    newer["captured_at"] = "2026-10-30T00:00:01+00:00"
    older = capture("後から取得した古い投稿", message_id="700000000000000001")
    older["messages"][0]["visible_timestamp"] = "2026-10-29T09:00:00+09:00"
    older["captured_at"] = "2026-10-30T00:00:02+00:00"
    save(tmp_path, newer)
    save(tmp_path, older)
    result = query(tmp_path)
    assert [message["message_id"] for message in result["messages"]] == ["700000000000000009", "700000000000000001"]
    assert query(tmp_path, limit=1)["messages"][0]["text"] == "新しい投稿"
    assert result["messages"][0]["visible_timestamp"] == "2026-10-30T09:00:00+09:00"
    assert result["messages"][0]["observed_at"] == "2026-10-30T00:00:01+00:00"


def test_native_timestamp_is_redacted_without_changing_legacy_contract(tmp_path):
    token = "T" * 32
    payload = capture()
    payload["messages"][0]["visible_timestamp"] = "Author" + "ization: Bearer " + token
    assert save(tmp_path, payload)["ok"]
    assert token not in (tmp_path / "snapshots.ndjson").read_text()
    assert token not in json.dumps(query(tmp_path))


def test_query_redacts_legacy_native_timestamp_on_readback(tmp_path):
    save(tmp_path, capture())
    path = tmp_path / "snapshots.ndjson"
    record = load_content_snapshot_records(path)[0]
    token = "T" * 32
    record["visible_timestamp"] = "Author" + "ization: Bearer " + token
    record["event_hash"] = canonical_event_hash(record)
    path.write_text(json.dumps(record) + "\n")
    assert token not in json.dumps(query(tmp_path))


@pytest.mark.parametrize("source", ["gateway_live_event", "GATEWAY_LIVE_EVENT", "Gateway_Live_Event"])
def test_gateway_metadata_requirement_matches_source_kind_normalization(tmp_path, source):
    payload = capture()
    payload["capture_method"] = source
    del payload["messages"][0]["message_metadata"]
    result = save(tmp_path, payload)
    assert result["ok"] is False
    assert result["reason"] == "gateway_message_metadata_required"


def test_rest_history_has_distinct_provenance_from_live_gateway(tmp_path):
    payload = capture()
    payload["capture_method"] = "rest_backfill"
    assert save(tmp_path, payload)["ok"]
    context = load_content_snapshot_records(tmp_path / "snapshots.ndjson")[0]["acquisition_context"]
    assert context["source_kind"] == "Discord REST"
    assert context["record_origin"] == "discord_rest_message_history"


def test_other_conversation_url_mentions_never_change_messages_cursor_or_tombstones(tmp_path):
    save(tmp_path, capture("対象の本文"))
    other_url = URL.replace("200000000000000001", "200000000000000002")
    save(tmp_path, capture(URL, message_id="700000000000000099", source_url=other_url))
    save(tmp_path, capture(URL, meta=metadata(deleted=True), source_url=other_url))
    result = query(tmp_path)
    assert [message["text"] for message in result["messages"]] == ["対象の本文"]
    assert result["latest_message_id"] == MESSAGE
    assert result["omitted"]["deleted"] == 0


@pytest.mark.parametrize("changes", [
    {"url": None}, {"target_key": None}, {"url": ""}, {"target_key": ""},
    {"url": URL.replace("200000000000000001", "200000000000000002")},
    {"target_key": "different-target"},
    {"source_url": URL.replace("200000000000000001", "200000000000000002")},
])
def test_query_requires_consistent_explicit_conversation_identity(tmp_path, changes):
    save(tmp_path, capture(URL))
    path = tmp_path / "snapshots.ndjson"
    record = load_content_snapshot_records(path)[0]
    record.update(changes)
    if changes.get("target_key"):
        record["stream_id"] = changes["target_key"]
    record["event_hash"] = canonical_event_hash(record)
    path.write_text(json.dumps(record) + "\n")
    if "target_key" in changes and not changes["target_key"]:
        with pytest.raises(CheckpointCorruptError):
            query(tmp_path)
        return
    result = query(tmp_path)
    assert result["messages"] == []
    assert result["latest_message_id"] is None
    assert result["omitted"]["deleted"] == 0


def test_whitespace_attachment_filename_is_rejected_before_write(tmp_path):
    meta = metadata(attachments=[{"id": "800000000000000001", "filename": " \t\n "}])
    assert save(tmp_path, capture(meta=meta))["ok"] is False
    assert not (tmp_path / "snapshots.ndjson").exists()


@pytest.mark.parametrize("url_field", ["url", "proxy_url"])
@pytest.mark.parametrize("url_kind", ["webhook", "token_hostname"])
def test_redacted_attachment_urls_remain_readable_and_preserve_deletion_barrier(tmp_path, url_field, url_kind):
    secret_url = "https://discord.com/api/" + "webhooks/800000000000000001/" + "T" * 40
    if url_kind == "token_hostname":
        token_hostname = ".".join(["A" * 24, "B" * 6, "C" * 27])
        secret_url = "https://" + token_hostname + "/file"
    attachments = [{"id": "800000000000000001", "filename": " 資料.txt ", url_field: secret_url}]
    assert save(tmp_path, capture(meta=metadata(attachments=attachments)))["ok"]
    row = load_content_snapshot_records(tmp_path / "snapshots.ndjson")[0]
    stored = row["message_metadata"]
    assert normalize_message_metadata(stored) == stored
    assert stored["attachments"][0]["filename"] == "資料.txt"
    assert url_field not in stored["attachments"][0]
    assert secret_url not in (tmp_path / "snapshots.ndjson").read_text()
    assert len(query(tmp_path)["messages"]) == 1
    assert save(tmp_path, capture("", meta=metadata(attachments=attachments, deleted=True)))["ok"]
    assert query(tmp_path)["messages"] == []
    assert query(tmp_path)["omitted"]["deleted"] == 1
    assert save(tmp_path, capture("再取得した旧本文"))["ok"]
    assert query(tmp_path)["messages"] == []
