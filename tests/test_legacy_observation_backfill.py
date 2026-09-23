from __future__ import annotations

import json

from discord_context_bridge.core import canonical_event_hash, load_text_snapshots, stable_text_hash
from discord_context_bridge.ingest import ingest_capture
from scripts.ingest_capture import build_result


def _row(event_id: str, target: str, text: str, *, original_event_id: str = "") -> dict:
    context = {"record_origin": "legacy_fixture"}
    if original_event_id:
        context["original_event_id"] = original_event_id
    return {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "event_id": event_id,
        "event_type": "discord.visible_text.snapshot_observed",
        "target_key": target,
        "captured_at": "2026-01-01T00:00:00Z",
        "observed_at": "2026-01-01T00:00:01Z",
        "source": "fixture",
        "url": "https://example.invalid/private",
        "title": "fixture title",
        "text": text,
        "acquisition_context": context,
    }


def test_legacy_backfill_splits_targets_and_preserves_fields(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    rows = [_row("old-a", "target-a", "alpha"), _row("old-b", "target-b", "beta")]

    result = ingest_capture(rows, snapshot_store=store, registry_store=registry, apply=True)

    assert result["events_appended"] == 2
    assert result["targets_processed"] == 2
    saved = load_text_snapshots(store)
    assert [(row["captured_at"], row["observed_at"], row["text"]) for row in saved] == [
        (rows[0]["captured_at"], rows[0]["observed_at"], "alpha"),
        (rows[1]["captured_at"], rows[1]["observed_at"], "beta"),
    ]
    assert {row["acquisition_context"]["legacy_observation_event_id"] for row in saved} == {
        "old-a", "old-b"
    }
    assert all(row["event_hash"] == canonical_event_hash(row) for row in saved)


def test_legacy_backfill_is_idempotent_with_reasoned_skips(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    rows = [_row("old-a", "target-a", "alpha"), _row("old-b", "target-a", "beta")]
    assert ingest_capture(rows, snapshot_store=store, registry_store=registry, apply=True)["events_appended"] == 2

    replay = ingest_capture(rows, snapshot_store=store, registry_store=registry, apply=True)

    assert replay["events_appended"] == 0
    assert replay["skipped"] == 2
    assert replay["skip_reasons"] == {"legacy_event_already_imported": 2}
    assert len(load_text_snapshots(store)) == 2


def test_legacy_backfill_dry_run_does_not_write_and_reconciles_count(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    rows = [_row("old-a", "target-a", "alpha"), _row("old-b", "target-a", "beta")]

    result = ingest_capture(rows, snapshot_store=store, registry_store=registry, apply=False)

    assert result["input_events"] == result["events_pending"] + result["skipped"] == 2
    assert not store.exists()
    assert not registry.exists()


def test_legacy_backfill_preserves_preexisting_original_event_id(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    row = _row("legacy-row", "target-a", "alpha", original_event_id="older-store-row")

    ingest_capture([row], snapshot_store=store, registry_store=registry, apply=True)

    context = load_text_snapshots(store)[0]["acquisition_context"]
    assert context["original_event_id"] == "older-store-row"
    assert context["legacy_observation_event_id"] == "legacy-row"


def test_ingest_capture_cli_entry_defaults_to_dry_run_for_legacy_rows(tmp_path):
    input_path = tmp_path / "legacy.ndjson"
    input_path.write_text(
        "\n".join(json.dumps(row) for row in [
            _row("old-a", "target-a", "alpha"),
            _row("old-b", "target-b", "beta"),
        ]) + "\n",
        encoding="utf-8",
    )
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"

    result = build_result(
        input_path=input_path,
        snapshot_store=store,
        registry_store=registry,
        apply=False,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["events_pending"] == 2
    assert not store.exists()
    assert not registry.exists()


def test_legacy_backfill_rejects_inconsistent_stream_within_target(tmp_path):
    first = _row("old-a", "target-a", "alpha")
    second = _row("old-b", "target-a", "beta")
    first["stream_id"] = "target-a"
    second["stream_id"] = "different-target"
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"

    result = ingest_capture(
        [first, second], snapshot_store=store, registry_store=registry, apply=True
    )

    assert result["ok"] is False
    assert result["reason"] == "legacy_target_identity_mismatch"
    assert not store.exists()
    assert not registry.exists()


def test_legacy_backfill_rejects_invalid_acquisition_context_without_writes(tmp_path):
    row = _row("old-a", "target-a", "alpha")
    row["acquisition_context"] = "invalid"
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"

    result = ingest_capture([row], snapshot_store=store, registry_store=registry, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "legacy_acquisition_context_invalid"
    assert not store.exists()
    assert not registry.exists()


# --- codex review (PR #71) 対応 -------------------------------------------------


def _fake_webhook() -> str:
    # secret scanner に実値と誤認されないよう、連結で組み立てる。
    return "https://discord.com/api/web" + "hooks/" + "1" * 18 + "/" + "abc_DEF-123"


def test_legacy_backfill_redacts_sensitive_text_before_storage(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    row = _row("old-secret", "target-a", "see " + _fake_webhook())

    result = ingest_capture([row], snapshot_store=store, registry_store=registry, apply=True)

    assert result["ok"] is True
    saved = load_text_snapshots(store)
    assert _fake_webhook() not in saved[0]["text"]
    assert "[discord webhook omitted]" in saved[0]["text"]
    assert saved[0]["content_hash"] == stable_text_hash(saved[0]["text"])


def test_legacy_replay_with_changed_observation_is_rejected(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    ingest_capture([_row("old-a", "target-a", "alpha")], snapshot_store=store, registry_store=registry, apply=True)
    before = store.read_bytes()

    changed = _row("old-a", "target-a", "alpha changed")
    result = ingest_capture([changed], snapshot_store=store, registry_store=registry, apply=True)

    assert result["ok"] is False
    assert result["reason"] == "legacy_replay_conflict"
    assert store.read_bytes() == before
    dry = ingest_capture([changed], snapshot_store=store, registry_store=registry, apply=False)
    assert dry["ok"] is False
    assert dry["reason"] == "legacy_replay_conflict"


def test_legacy_identical_replay_is_still_skipped(tmp_path):
    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    row = _row("old-a", "target-a", "see " + _fake_webhook())
    ingest_capture([row], snapshot_store=store, registry_store=registry, apply=True)

    again = ingest_capture([row], snapshot_store=store, registry_store=registry, apply=True)

    assert again["ok"] is True
    assert again["events_appended"] == 0
    assert again["skipped"] == 1


def test_url_option_validates_every_legacy_target(tmp_path):
    url = "https://example.invalid/target"
    expected = stable_text_hash(url)
    source = tmp_path / "legacy.ndjson"
    rows = [_row("old-a", expected, "alpha"), _row("old-b", "other-target", "beta")]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    store = tmp_path / "snapshots.ndjson"

    result = build_result(
        input_path=source,
        snapshot_store=store,
        registry_store=tmp_path / "targets.ndjson",
        apply=True,
        url=url,
    )

    assert result["ok"] is False
    assert result["reason"] == "target_key_url_mismatch"
    assert not store.exists()


def test_single_legacy_json_object_is_ingested(tmp_path):
    source = tmp_path / "legacy.json"
    source.write_text(json.dumps(_row("old-a", "target-a", "alpha")), encoding="utf-8")
    store = tmp_path / "snapshots.ndjson"

    result = build_result(
        input_path=source,
        snapshot_store=store,
        registry_store=tmp_path / "targets.ndjson",
        apply=True,
    )

    assert result["ok"] is True
    assert result["events_appended"] == 1


def test_legacy_backfill_enforces_count_and_text_budget(tmp_path, monkeypatch):
    from discord_context_bridge import ingest as ingest_module

    store = tmp_path / "snapshots.ndjson"
    registry = tmp_path / "targets.ndjson"
    monkeypatch.setattr(ingest_module, "MAX_MESSAGES", 1)
    too_many = ingest_capture(
        [_row("old-a", "target-a", "alpha"), _row("old-b", "target-a", "beta")],
        snapshot_store=store,
        registry_store=registry,
        apply=True,
    )
    assert too_many["ok"] is False
    assert too_many["reason"] == "message_limit_exceeded"

    monkeypatch.setattr(ingest_module, "MAX_MESSAGES", 10)
    monkeypatch.setattr(ingest_module, "MAX_BODY_TEXT_CHARS", 3)
    too_long = ingest_capture([_row("old-a", "target-a", "alpha")], snapshot_store=store, registry_store=registry, apply=True)
    assert too_long["ok"] is False
    assert too_long["reason"] == "message_body_limit_exceeded"
    assert not store.exists()
    assert not registry.exists()
