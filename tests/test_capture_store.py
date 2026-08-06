from __future__ import annotations

import json

import pytest

from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
    EventConflictError,
    SequenceConflictError,
)


def _run(*, state: str = "received", sequence: int = 0) -> dict[str, object]:
    return {
        "schema": "dcb-full-capture-orchestrator.v1",
        "capture_id": "capture-safe-a",
        "target_digest": "target-safe-a",
        "state": state,
        "checkpoints": [{"sequence": sequence}] if sequence else [],
    }


def _event(event_id: str, *, sequence: int, state: str) -> dict[str, object]:
    return {
        "schema": "dcb-capture-event.v1",
        "event_id": event_id,
        "capture_id": "capture-safe-a",
        "sequence": sequence,
        "state": state,
    }


def test_checkpoint_round_trip_and_atomic_replace(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)

    first = store.save_checkpoint(_run(), expected_sequence=0)
    second = store.save_checkpoint(
        _run(state="route_preflight", sequence=1),
        expected_sequence=0,
    )

    assert first["checkpoints"] == []
    assert second["checkpoints"][-1]["sequence"] == 1
    assert store.load_checkpoint("capture-safe-a") == second
    assert list(tmp_path.rglob("*.tmp")) == []


def test_append_event_is_idempotent_by_event_id(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    event = _event("event-1", sequence=1, state="route_preflight")

    first = store.append_event(event, expected_sequence=0)
    duplicate = store.append_event(event, expected_sequence=0)

    assert first["appended"] is True
    assert duplicate["appended"] is False
    assert duplicate["duplicate"] is True
    assert store.load_events("capture-safe-a") == [event]


def test_duplicate_event_id_with_different_payload_is_rejected(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    store.append_event(
        _event("event-1", sequence=1, state="route_preflight"),
        expected_sequence=0,
    )

    with pytest.raises(EventConflictError):
        store.append_event(
            _event("event-1", sequence=1, state="blocked_closed"),
            expected_sequence=1,
        )


def test_expected_sequence_is_compare_and_swap(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    store.append_event(
        _event("event-1", sequence=1, state="route_preflight"),
        expected_sequence=0,
    )

    with pytest.raises(SequenceConflictError):
        store.append_event(
            _event("event-2", sequence=2, state="traversing_to_oldest"),
            expected_sequence=0,
        )

    assert [item["event_id"] for item in store.load_events("capture-safe-a")] == [
        "event-1"
    ]


def test_corrupt_checkpoint_fails_closed_without_using_partial_state(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    path = store.checkpoint_path("capture-safe-a")
    path.parent.mkdir(parents=True)
    path.write_text('{"capture_id": "capture-safe-a"', encoding="utf-8")

    with pytest.raises(CheckpointCorruptError):
        store.load_checkpoint("capture-safe-a")


def test_checkpoint_with_untrusted_state_or_tag_fails_closed(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    payload = _run()
    payload["state"] = "private-channel-name"
    payload["operational_tags"] = ["private-channel-name"]
    path = store.checkpoint_path("capture-safe-a")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointCorruptError):
        store.load_checkpoint("capture-safe-a")

    payload["state"] = "received"
    payload["operational_tags"] = []
    payload["blocker"] = "private-discord-url"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CheckpointCorruptError):
        store.load_checkpoint("capture-safe-a")


def test_corrupt_ledger_fails_closed_before_append(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    path = store.ledger_path("capture-safe-a")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_event("event-1", sequence=1, state="received")) + "\n{",
        encoding="utf-8",
    )

    with pytest.raises(CheckpointCorruptError):
        store.append_event(
            _event("event-2", sequence=2, state="route_preflight"),
            expected_sequence=1,
        )
