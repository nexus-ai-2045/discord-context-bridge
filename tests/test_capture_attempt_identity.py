from __future__ import annotations

import json
from hashlib import sha256

import pytest

from discord_context_bridge.capture.orchestrator import new_capture_run
from discord_context_bridge.capture.receipts import persist_strict_full_capture_receipt
from discord_context_bridge.capture.service import (
    advance_persisted_capture,
    merge_persisted_capture_window,
    seal_persisted_attachment_inventory,
    start_capture_loop,
)
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CaptureStoreError,
    CheckpointCorruptError,
)
from discord_context_bridge.cli import main
from discord_context_bridge.cli import _reconcile_persisted_capture


def _digest(*parts: str) -> str:
    return sha256("".join(f"{len(part)}:{part}" for part in parts).encode()).hexdigest()


def _gate(capture_id: str) -> dict[str, object]:
    return {
        "schema": "discord_full_capture_completion_gate.v1",
        "status": "full",
        "full_capture_confirmed": True,
        "capture_id": capture_id,
        "boundaries": {
            "oldest_reached": True,
            "latest_reached": True,
            "capture_stable_after_rescan": True,
        },
        "counts": {
            "messages": 2,
            "raw_records": 2,
            "markdown_messages": 2,
            "ledger_messages": 2,
            "attachments_discovered": 0,
            "attachments_saved": 0,
            "attachments_manifested": 0,
        },
        "counts_consistent": True,
        "attachments_consistent": True,
        "unresolved_gap_count": 0,
        "blockers": [],
        "raw_text_returned": False,
        "participant_names_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def _stable_attempt(store: CaptureCheckpointStore, attempt_id: str) -> str:
    capture_id = start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id=attempt_id,
    )["capture_id"]
    for scan_pass in (1, 2):
        merge_persisted_capture_window(
            store,
            capture_id,
            {
                "window_id": f"{attempt_id}-window-{scan_pass}",
                "source": "saved_snapshot",
                "direction": "toward_latest",
                "scan_pass": scan_pass,
                "oldest_reached": True,
                "latest_reached": True,
                "messages": [
                    {"message_id": "message-1", "content_hash": "hash-1"},
                    {"message_id": "message-2", "content_hash": "hash-2"},
                ],
            },
            expected_window_count=scan_pass - 1,
        )
    return capture_id


def test_default_capture_identity_and_projection_shape_remain_legacy_compatible():
    run = new_capture_run("target", "saved_artifacts", "watermark")
    base_id = _digest(
        "dcb-full-capture-orchestrator.v1", "target", "saved_artifacts", "watermark"
    )
    assert run["capture_id"] == new_capture_run(
        "target", "saved_artifacts", "watermark"
    )["capture_id"]
    assert run["capture_id"] == base_id
    assert run["target_digest"] == _digest("target", "target")
    assert run["upper_watermark_digest"] == _digest("watermark", "watermark")
    assert "capture_identity" not in run
    assert "base_capture_id" not in run
    assert "attempt_id" not in run

    attempted = new_capture_run(
        "target", "saved_artifacts", "watermark", attempt_id="try-1"
    )
    assert attempted["capture_id"] == _digest(
        "dcb-capture-attempt.v1", base_id, "try-1"
    )
    assert attempted["capture_identity"] == {
        "schema": "dcb-capture-attempt-identity.v1",
        "base_capture_id": base_id,
        "attempt_id": "try-1",
    }


def test_same_attempt_resumes_but_new_attempt_starts_clean_and_preserves_old_bytes(
    tmp_path,
):
    store = CaptureCheckpointStore(tmp_path)
    first = start_capture_loop(
        store, "private-target", "saved_artifacts", "message-2", attempt_id="try-1"
    )
    capture_id = first["capture_id"]
    advance_persisted_capture(
        store, capture_id, "event-1", "route_ready", expected_sequence=0
    )
    merge_persisted_capture_window(
        store,
        capture_id,
        {
            "window_id": "try-1-window-1",
            "source": "saved_snapshot",
            "direction": "toward_oldest",
            "scan_pass": 1,
            "messages": [
                {"message_id": "message-1", "content_hash": "hash-1"}
            ],
        },
        expected_window_count=0,
    )
    checkpoint_path = store.checkpoint_path(capture_id)
    original_bytes = checkpoint_path.read_bytes()

    resumed = start_capture_loop(
        store, "private-target", "saved_artifacts", "message-2", attempt_id="try-1"
    )
    original_files = {
        path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    second = start_capture_loop(
        store, "private-target", "saved_artifacts", "message-2", attempt_id="try-2"
    )

    assert resumed["capture_id"] == capture_id
    assert resumed["state"] == "traversing_to_oldest"
    assert second["capture_id"] != capture_id
    assert store.checkpoint_path(capture_id).read_bytes() == original_bytes
    assert all(path.read_bytes() == content for path, content in original_files.items())
    assert store.load_checkpoint(second["capture_id"])["checkpoints"] == []
    assert store.load_coverage(second["capture_id"]) is None
    assert store.load_message_ledger(second["capture_id"]) is None


def test_attempt_identity_is_bound_into_checkpoint_and_full_receipt(tmp_path):
    store = CaptureCheckpointStore(tmp_path)
    capture_id = _stable_attempt(store, "receipt-attempt")
    checkpoint = store.load_checkpoint(capture_id)

    receipt = persist_strict_full_capture_receipt(
        store,
        capture_id,
        _gate(capture_id),
        consumer="context_acquisition",
    )

    assert receipt["capture_identity"] == checkpoint["capture_identity"]
    assert store.load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) == receipt


def test_partial_evidence_from_other_attempt_cannot_make_new_attempt_full(tmp_path):
    store = CaptureCheckpointStore(tmp_path)
    old_capture_id = _stable_attempt(store, "old-attempt")
    new_capture_id = start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id="new-attempt",
    )["capture_id"]

    old_coverage = store.load_coverage(old_capture_id)
    assert old_coverage and len(old_coverage["windows"]) == 2
    assert store.load_coverage(new_capture_id) is None
    assert store.load_message_ledger(new_capture_id) is None

    partial = {
        "window_id": "new-attempt-window-1",
        "source": "saved_snapshot",
        "direction": "toward_latest",
        "scan_pass": 1,
        "oldest_reached": True,
        "latest_reached": True,
        "messages": [
            {"message_id": "message-1", "content_hash": "hash-1"},
            {"message_id": "message-2", "content_hash": "hash-2"},
        ],
    }
    merge_persisted_capture_window(
        store, new_capture_id, partial, expected_window_count=0
    )
    new_result = _reconcile_persisted_capture(store, new_capture_id)
    old_result = _reconcile_persisted_capture(store, old_capture_id)
    assert new_result["status"] == "partial"
    assert new_result["full_capture_confirmed"] is False
    assert old_result["status"] == "full"
    assert old_result["full_capture_confirmed"] is True


def test_cross_attempt_receipt_message_ledger_and_seal_are_rejected(tmp_path):
    store = CaptureCheckpointStore(tmp_path)
    old_capture_id = _stable_attempt(store, "source-attempt")
    seal_persisted_attachment_inventory(store, old_capture_id, expected_sequence=0)
    old_receipt = persist_strict_full_capture_receipt(
        store,
        old_capture_id,
        _gate(old_capture_id),
        consumer="context_acquisition",
    )
    new_capture_id = start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id="destination-attempt",
    )["capture_id"]

    store.message_ledger_path(new_capture_id).parent.mkdir(parents=True, exist_ok=True)
    store.message_ledger_path(new_capture_id).write_bytes(
        store.message_ledger_path(old_capture_id).read_bytes()
    )
    store.attachment_save_ledger_path(new_capture_id).parent.mkdir(
        parents=True, exist_ok=True
    )
    store.attachment_save_ledger_path(new_capture_id).write_bytes(
        store.attachment_save_ledger_path(old_capture_id).read_bytes()
    )
    store.full_capture_receipt_path(new_capture_id).parent.mkdir(
        parents=True, exist_ok=True
    )
    store.full_capture_receipt_path(new_capture_id).write_text(
        json.dumps(old_receipt), encoding="utf-8"
    )

    with pytest.raises(CheckpointCorruptError):
        store.load_message_ledger(new_capture_id)
    with pytest.raises(CheckpointCorruptError):
        store.load_attachment_save_ledger(new_capture_id)
    with pytest.raises(CheckpointCorruptError):
        store.load_full_capture_receipt(
            new_capture_id, consumer="context_acquisition"
        )


@pytest.mark.parametrize("field", ["target_key", "upper_watermark"])
def test_full_receipt_rejects_ledger_binding_mismatch(tmp_path, field):
    store = CaptureCheckpointStore(tmp_path)
    capture_id = _stable_attempt(store, f"ledger-{field}")
    ledger = store.load_message_ledger(capture_id)
    assert ledger is not None
    ledger[field] = "different-target" if field == "target_key" else "different-watermark"
    store.save_message_ledger(ledger, expected_sequence=len(ledger["events"]))

    with pytest.raises(CaptureStoreError, match="source identity"):
        persist_strict_full_capture_receipt(
            store,
            capture_id,
            _gate(capture_id),
            consumer="context_acquisition",
        )


def test_attempt_checkpoint_rejects_stripped_tampered_and_unknown_identity(tmp_path):
    store = CaptureCheckpointStore(tmp_path)
    capture_id = start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id="bound-attempt",
    )["capture_id"]
    path = store.checkpoint_path(capture_id)
    original = json.loads(path.read_text(encoding="utf-8"))

    stripped = json.loads(json.dumps(original))
    stripped.pop("capture_identity")
    path.write_text(json.dumps(stripped), encoding="utf-8")
    with pytest.raises((CaptureStoreError, ValueError)):
        start_capture_loop(
            store,
            "private-target",
            "saved_artifacts",
            "message-2",
            attempt_id="bound-attempt",
        )

    for mutate in (
        lambda item: item["capture_identity"].update(attempt_id="other-attempt"),
        lambda item: item["capture_identity"].update(schema="unknown.v1"),
    ):
        changed = json.loads(json.dumps(original))
        mutate(changed)
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(CheckpointCorruptError):
            store.load_checkpoint(capture_id)
    path.write_text(json.dumps(original), encoding="utf-8")


@pytest.mark.parametrize(
    "field,value_source",
    [
        ("target_digest", "other_target"),
        ("upper_watermark_digest", "other_watermark"),
        ("route", "other_route"),
    ],
)
def test_resume_rejects_checkpoint_source_identity_mismatch(
    tmp_path, field, value_source
):
    store = CaptureCheckpointStore(tmp_path)
    start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id="bound-attempt",
    )
    other = new_capture_run(
        "other-target" if value_source == "other_target" else "private-target",
        "chrome_extension" if value_source == "other_route" else "saved_artifacts",
        "other-message" if value_source == "other_watermark" else "message-2",
        attempt_id="bound-attempt",
    )
    expected = new_capture_run(
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id="bound-attempt",
    )
    path = store.checkpoint_path(expected["capture_id"])
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    checkpoint[field] = other[field]
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(CheckpointCorruptError):
        start_capture_loop(
            store,
            "private-target",
            "saved_artifacts",
            "message-2",
            attempt_id="bound-attempt",
        )

    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "strip_capture_identity",
        "change_target_digest",
        "change_watermark_digest",
        "change_route",
    ],
)
def test_checkpoint_save_cannot_change_immutable_run_identity(tmp_path, mutation):
    store = CaptureCheckpointStore(tmp_path)
    capture_id = start_capture_loop(
        store,
        "private-target",
        "saved_artifacts",
        "message-2",
        attempt_id="immutable-attempt",
    )["capture_id"]
    path = store.checkpoint_path(capture_id)
    before = path.read_bytes()
    run = store.load_checkpoint(capture_id)
    assert run is not None
    if mutation == "strip_capture_identity":
        run.pop("capture_identity")
    elif mutation == "change_target_digest":
        run["target_digest"] = "0" * 64
    elif mutation == "change_watermark_digest":
        run["upper_watermark_digest"] = "1" * 64
    elif mutation == "change_route":
        run["route"] = "chrome_extension"

    with pytest.raises(CaptureStoreError):
        store.save_checkpoint(run, expected_sequence=0)

    assert path.read_bytes() == before


def test_attempt_receipt_identity_stripping_is_rejected(tmp_path):
    store = CaptureCheckpointStore(tmp_path)
    capture_id = _stable_attempt(store, "strip-attempt")
    receipt = persist_strict_full_capture_receipt(
        store,
        capture_id,
        _gate(capture_id),
        consumer="context_acquisition",
    )
    path = store.full_capture_receipt_path(capture_id)
    changed = dict(receipt)
    changed.pop("capture_identity")
    path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(CheckpointCorruptError):
        store.load_full_capture_receipt(
            capture_id, consumer="context_acquisition"
        )


def test_attempt_cli_option_is_restricted_to_start(tmp_path, capsys):
    start = main(
        [
            "capture-loop", "start", "--store-root", str(tmp_path),
            "--target-key", "private-target", "--route", "saved_artifacts",
            "--upper-watermark", "message-2", "--attempt-id", "cli-attempt", "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert start == 0
    assert payload["capture_id"]

    status = main(
        [
            "capture-loop", "status", "--store-root", str(tmp_path),
            "--capture-id", payload["capture_id"], "--attempt-id", "wrong-place", "--json",
        ]
    )
    rejected = json.loads(capsys.readouterr().out)
    assert status == 2
    assert rejected["ok"] is False


def test_attempt_id_rejects_unsafe_values():
    for attempt_id in ("", "   ", "../x", "has space", "x" * 129):
        with pytest.raises(ValueError):
            new_capture_run(
                "target", "saved_artifacts", "watermark", attempt_id=attempt_id
            )


@pytest.mark.parametrize('mutation', ['strip', 'binding_deleted', 'checkpoint_deleted', 'foreign_binding', 'source_changed'])
def test_attempt_durable_binding_rejects_one_sided_tamper_on_direct_read(tmp_path, mutation):
    store = CaptureCheckpointStore(tmp_path)
    capture_id = _stable_attempt(store, 'durable-attempt')
    path = store.checkpoint_path(capture_id)
    binding = store.root / 'attempt-identities' / f'{capture_id}.json'
    checkpoint = json.loads(path.read_text())
    if mutation == 'strip':
        checkpoint.pop('capture_identity')
        path.write_text(json.dumps(checkpoint))
    elif mutation == 'binding_deleted':
        if binding.exists():
            binding.unlink()
    elif mutation == 'checkpoint_deleted':
        path.unlink()
    elif mutation == 'foreign_binding':
        other = _stable_attempt(store, 'other-durable-attempt')
        other_binding = store.root / 'attempt-identities' / f'{other}.json'
        if other_binding.exists():
            binding.write_bytes(other_binding.read_bytes())
    else:
        checkpoint['target_digest'] = 'different-target'
        path.write_text(json.dumps(checkpoint))
    with pytest.raises(CheckpointCorruptError):
        store.load_checkpoint(capture_id)
    with pytest.raises(CaptureStoreError):
        advance_persisted_capture(store, capture_id, 'tampered', 'route_ready', expected_sequence=0)
    with pytest.raises(CaptureStoreError):
        persist_strict_full_capture_receipt(store, capture_id, _gate(capture_id), consumer='context_acquisition')
