from __future__ import annotations

import os
from pathlib import Path

import pytest

from discord_context_bridge.capture import store as store_module
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
    SequenceConflictError,
)


def _event(capture_id: str, event_id: str = "event-a") -> dict[str, object]:
    return {
        "schema": "dcb-capture-event.v1",
        "capture_id": capture_id,
        "event_id": event_id,
        "sequence": 1,
        "event": "route_ready",
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def test_event_ledger_append_and_load_round_trip(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    event = _event(capture_id)

    result = store.append_event(event, expected_sequence=0)

    assert result == {
        "capture_id": capture_id,
        "event_id": "event-a",
        "sequence": 1,
        "appended": True,
        "duplicate": False,
    }
    assert store.load_events(capture_id) == [event]


def test_event_ledger_does_not_follow_linked_parent(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside"
    store.root.mkdir()
    outside.mkdir()
    (store.root / "events").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CheckpointCorruptError, match="link|secure|invalid"):
        store.append_event(_event(capture_id), expected_sequence=0)

    assert list(outside.iterdir()) == []


def test_event_ledger_does_not_follow_linked_target(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside.ndjson"
    outside.write_text("keep\n", encoding="utf-8")
    path = store.ledger_path(capture_id)
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(CheckpointCorruptError, match="unreadable|unwritable"):
        store.load_events(capture_id)
    with pytest.raises(CheckpointCorruptError, match="unreadable|unwritable"):
        store.append_event(_event(capture_id), expected_sequence=0)

    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_event_ledger_rejects_nonregular_target(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    path = store.ledger_path(capture_id)
    path.mkdir(parents=True)

    with pytest.raises(CheckpointCorruptError, match="regular|unreadable|unwritable"):
        store.load_events(capture_id)
    with pytest.raises(CheckpointCorruptError, match="regular|unreadable|unwritable"):
        store.append_event(_event(capture_id), expected_sequence=0)


def test_transition_lock_is_exclusive_and_reusable(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")

    with store.transition_lock("capture-safe-a"):
        with pytest.raises(SequenceConflictError, match="already locked"):
            with store.transition_lock("capture-safe-a"):
                pass

    with store.transition_lock("capture-safe-a"):
        pass


def test_transition_lock_does_not_follow_linked_parent(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside"
    store.root.mkdir()
    outside.mkdir()
    (store.root / "locks").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CheckpointCorruptError, match="link|secure|invalid"):
        with store.transition_lock("capture-safe-a"):
            pass

    assert list(outside.iterdir()) == []


def test_transition_lock_rejects_nonregular_target(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    path = store.root / "locks" / "capture-safe-a.lock"
    path.mkdir(parents=True)

    with pytest.raises(CheckpointCorruptError, match="regular|unwritable"):
        with store.transition_lock("capture-safe-a"):
            pass


def test_transition_lock_detects_target_rebinding(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    path = store.root / "locks" / "capture-safe-a.lock"

    with pytest.raises(CheckpointCorruptError, match="binding changed"):
        with store.transition_lock("capture-safe-a"):
            path.unlink()
            path.write_bytes(b"replacement")


def test_tail_persistence_uses_compatible_backend_without_secure_primitives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: False)

    assert store.append_event(
        _event("capture-safe-a"), expected_sequence=0
    )["appended"] is True
    assert len(store.load_events("capture-safe-a")) == 1
    with store.transition_lock("capture-safe-a"):
        assert store.load_events("capture-safe-a")[0]["event_id"] == "event-a"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_event_ledger_load_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    path = store.ledger_path(capture_id)
    path.parent.mkdir(parents=True)
    os.mkfifo(path)

    with pytest.raises(CheckpointCorruptError, match="regular"):
        store.load_events(capture_id)
