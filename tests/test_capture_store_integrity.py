from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

requires_symlink_privilege = pytest.mark.skipif(
    os.name == "nt", reason="symlink integrity probe requires Windows developer mode or privilege"
)

from discord_context_bridge.capture import store as store_module
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
    canonical_capture_digest,
)


def _attachment_ledger(capture_id: str) -> dict[str, object]:
    return {
        "schema": "dcb-private-attachment-save-ledger.v1",
        "capture_id": capture_id,
        "records": [],
        "tip_hash": canonical_capture_digest([]),
        "seal": None,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def _message_ledger_without_tip(capture_id: str) -> dict[str, object]:
    return {
        "schema": "dcb-private-message-event-ledger.v1",
        "capture_id": capture_id,
        "target_key": "target-a",
        "upper_watermark": "watermark-a",
        "events": [],
        "outbound_actions": "disabled",
        "private_local_only": True,
    }


def _legacy_full_receipt(capture_id: str) -> dict[str, object]:
    return {
        "schema": "dcb-strict-full-capture-receipt.v1",
        "schema_version": "1.0",
        "capture_id": capture_id,
        "consumer_binding": "context_acquisition",
        "recorded_at": "2026-08-14T00:00:00+00:00",
        "recorded_by": "discord-context-bridge",
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


@requires_symlink_privilege
def test_attachment_ledger_round_trip_rejects_linked_store_directory(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir()
    (store.root / "attachment-save-ledgers").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CheckpointCorruptError, match="link|secure"):
        store.save_attachment_save_ledger(
            _attachment_ledger(capture_id), expected_sequence=0
        )

    assert list(outside.iterdir()) == []


@requires_symlink_privilege
def test_receipt_invalidation_cannot_follow_link_outside_store(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside"
    target = outside / f"{capture_id}.json"
    outside.mkdir()
    target.write_text("keep", encoding="utf-8")
    (store.root / "receipts").mkdir(parents=True)
    (store.root / "receipts" / "full-capture").symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(CheckpointCorruptError, match="link|secure"):
        store.invalidate_full_capture_receipt(capture_id)

    assert target.read_text(encoding="utf-8") == "keep"


def test_sensitive_store_writes_fail_closed_without_safe_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path)
    monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: False)

    with pytest.raises(CheckpointCorruptError, match="unavailable"):
        store.save_attachment_save_ledger(
            _attachment_ledger("capture-safe-a"), expected_sequence=0
        )


@requires_symlink_privilege
def test_managed_object_read_does_not_follow_final_symlink(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private")
    managed = store.root / "attachment-objects" / "capture-safe-a" / "object.bin"
    managed.parent.mkdir(parents=True)
    managed.symlink_to(outside)

    with pytest.raises(CheckpointCorruptError, match="link|unreadable|regular"):
        store.read_managed_object(
            "attachment-objects/capture-safe-a/object.bin", max_bytes=100
        )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_managed_object_read_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    managed = store.root / "attachment-objects" / "capture-safe-a" / "object.bin"
    managed.parent.mkdir(parents=True)
    os.mkfifo(managed)

    with pytest.raises(CheckpointCorruptError, match="regular"):
        store.read_managed_object(
            "attachment-objects/capture-safe-a/object.bin", max_bytes=100
        )


def test_legacy_message_ledger_is_validated_then_enriched_in_memory(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path)
    path = store.message_ledger_path(capture_id)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_message_ledger_without_tip(capture_id)), encoding="utf-8"
    )

    loaded = store.load_message_ledger(capture_id)

    assert loaded is not None
    assert loaded["tip_hash"] == ""
    assert "tip_hash" not in json.loads(path.read_text(encoding="utf-8"))

    store.save_message_ledger(loaded, expected_sequence=0)
    assert json.loads(path.read_text(encoding="utf-8"))["tip_hash"] == ""


def test_legacy_message_ledger_with_invalid_event_is_not_enriched(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path)
    payload = _message_ledger_without_tip(capture_id)
    payload["events"] = [{"sequence": 1}]
    path = store.message_ledger_path(capture_id)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointCorruptError, match="hash chain"):
        store.load_message_ledger(capture_id)


def test_legacy_full_receipt_without_source_binding_is_untrusted_but_regenerable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path)
    store.save_receipt(
        store.full_capture_receipt_path(capture_id),
        _legacy_full_receipt(capture_id),
    )
    monkeypatch.setattr(
        "discord_context_bridge.acquisition_gate.validate_full_capture_receipt",
        lambda payload: {"valid": True},
    )

    assert store.load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) is None

    regenerated = {
        **_legacy_full_receipt(capture_id),
        "source_binding": {
            "checkpoint_digest": canonical_capture_digest(None),
            "message_ledger_digest": canonical_capture_digest(None),
            "coverage_digest": canonical_capture_digest(None),
            "attachment_ledger_digest": canonical_capture_digest(None),
        },
    }
    store.save_receipt(store.full_capture_receipt_path(capture_id), regenerated)
    assert store.load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) == regenerated


def test_legacy_full_receipt_still_requires_valid_gate_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path)
    store.save_receipt(
        store.full_capture_receipt_path(capture_id),
        _legacy_full_receipt(capture_id),
    )
    monkeypatch.setattr(
        "discord_context_bridge.acquisition_gate.validate_full_capture_receipt",
        lambda payload: {"valid": False},
    )

    with pytest.raises(CheckpointCorruptError, match="evidence is invalid"):
        store.load_full_capture_receipt(
            capture_id, consumer="context_acquisition"
        )


def test_full_receipt_with_explicit_null_source_binding_is_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path)
    store.save_receipt(
        store.full_capture_receipt_path(capture_id),
        {**_legacy_full_receipt(capture_id), "source_binding": None},
    )
    monkeypatch.setattr(
        "discord_context_bridge.acquisition_gate.validate_full_capture_receipt",
        lambda payload: {"valid": True},
    )

    with pytest.raises(CheckpointCorruptError, match="source binding is invalid"):
        store.load_full_capture_receipt(
            capture_id, consumer="context_acquisition"
        )
