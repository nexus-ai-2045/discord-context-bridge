from __future__ import annotations

import json

from discord_context_bridge.core import canonical_event_hash, load_text_snapshots
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
