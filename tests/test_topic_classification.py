from __future__ import annotations

import json

import pytest

from discord_context_bridge.topic_classification import (
    MODEL_ROUTE,
    RESULT_SCHEMA,
    build_topic_classification_packet,
    build_topic_human_review_packet,
    import_topic_classification_result,
)


def _snapshot(path, count=1):
    with path.open("w", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(json.dumps({
                "schema": "discord_context_bridge_text_snapshot_observation.v1",
                "target_key": f"safe-target-{index}",
                "stream_id": f"safe-target-{index}",
                "stream_sequence": index + 1,
                "captured_at": f"2026-08-26T00:{index:02d}:00+09:00",
                "source": "visible_text",
                "content_hash": f"hash-{index}",
                "text": f"member-a: 題分類の原文 {index}",
                "private_local_only": True,
                "outbound_actions": "disabled",
            }, ensure_ascii=False) + "\n")


def _registry(path):
    path.write_text(json.dumps({
        "schema": "dcb.topic_assignment_registry.v1",
        "private_local_only": True,
        "topics": [{"topic_id": "knowledge-wiki", "label": "Knowledge Wiki"}],
        "assignments": [],
    }, ensure_ascii=False), encoding="utf-8")


def test_packet_import_review_are_private_idempotent_and_proposal_only(tmp_path):
    snapshots = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "topics.json"
    packet_path = tmp_path / "packet.json"
    result_path = tmp_path / "result.json"
    ledger = tmp_path / "proposals.ndjson"
    review = tmp_path / "review.md"
    _snapshot(snapshots)
    _registry(registry)

    built = build_topic_classification_packet(
        snapshot_store=snapshots, topic_registry=registry, output_path=packet_path
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert built["candidate_count"] == 1
    assert "題分類の原文" not in json.dumps(built, ensure_ascii=False)
    assert packet["external_send_approved"] is False
    result_path.write_text(json.dumps({
        "schema": RESULT_SCHEMA,
        "packet_id": packet["packet_id"],
        "model": MODEL_ROUTE,
        "items": [{
            "observation_id": packet["items"][0]["observation_id"],
            "topics": [{
                "existing_topic_id": "knowledge-wiki",
                "confidence": 0.9,
                "reason": "題分類に関する会話",
            }],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    first = import_topic_classification_result(
        packet_path=packet_path, result_path=result_path, proposal_ledger=ledger
    )
    second = import_topic_classification_result(
        packet_path=packet_path, result_path=result_path, proposal_ledger=ledger
    )
    assert first["appended_proposal_count"] == 1
    assert second["appended_proposal_count"] == 0
    assert first["reviewed_registry_changed"] is False
    review_result = build_topic_human_review_packet(
        packet_path=packet_path, proposal_ledger=ledger, output_path=review
    )
    assert review_result["proposal_count"] == 1
    assert "題分類の原文" in review.read_text(encoding="utf-8")
    assert json.loads(registry.read_text(encoding="utf-8"))["assignments"] == []


def test_result_validation_fails_closed_and_bad_ledger_is_rejected(tmp_path):
    snapshots, registry = tmp_path / "s.ndjson", tmp_path / "topics.json"
    packet_path, result_path, ledger = tmp_path / "p.json", tmp_path / "r.json", tmp_path / "l.ndjson"
    _snapshot(snapshots)
    _registry(registry)
    build_topic_classification_packet(snapshot_store=snapshots, topic_registry=registry, output_path=packet_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    result_path.write_text(json.dumps({
        "schema": RESULT_SCHEMA, "packet_id": packet["packet_id"], "model": MODEL_ROUTE,
        "items": [{"observation_id": packet["items"][0]["observation_id"], "topics": [
            {"existing_topic_id": "unknown", "confidence": 1, "reason": "x"}
        ]}],
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        import_topic_classification_result(packet_path=packet_path, result_path=result_path, proposal_ledger=ledger)
    ledger.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        build_topic_classification_packet(snapshot_store=snapshots, topic_registry=registry, output_path=tmp_path / "p2.json", proposal_ledger=ledger)


def test_sixty_item_packet_is_stable_and_missing_source_is_explicit(tmp_path):
    snapshots, registry = tmp_path / "s.ndjson", tmp_path / "topics.json"
    _snapshot(snapshots, 60)
    _registry(registry)
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    a = build_topic_classification_packet(snapshot_store=snapshots, topic_registry=registry, output_path=first, max_items=60)
    b = build_topic_classification_packet(snapshot_store=snapshots, topic_registry=registry, output_path=second, max_items=60)
    assert a["candidate_count"] == 60
    assert a["packet_id"] == b["packet_id"]
    assert first.read_bytes() == second.read_bytes()
    packet = json.loads(first.read_text(encoding="utf-8"))
    result = tmp_path / "result.json"
    ledger = tmp_path / "proposals.ndjson"
    result.write_text(json.dumps({
        "schema": RESULT_SCHEMA, "packet_id": packet["packet_id"], "model": MODEL_ROUTE,
        "items": [
            {"observation_id": item["observation_id"], "topics": [], "abstain_reason": "要人間判断"}
            for item in packet["items"]
        ],
    }, ensure_ascii=False), encoding="utf-8")
    imported = import_topic_classification_result(packet_path=first, result_path=result, proposal_ledger=ledger)
    repeated = import_topic_classification_result(packet_path=first, result_path=result, proposal_ledger=ledger)
    assert imported["validated_item_count"] == 60
    assert imported["abstained_count"] == 60
    assert repeated["appended_proposal_count"] == 0
    assert sum(1 for line in ledger.read_text(encoding="utf-8").splitlines() if line) == 60
    missing = build_topic_classification_packet(snapshot_store=tmp_path / "missing", topic_registry=registry, output_path=tmp_path / "none")
    assert missing["ok"] is False
    assert missing["reason"] == "snapshot_store_missing"
    assert not (tmp_path / "none").exists()
