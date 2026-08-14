from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from discord_context_bridge.capture import store as store_module
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
)


def _event(capture_id: str) -> dict[str, object]:
    return {
        "schema": "dcb-capture-event.v1",
        "capture_id": capture_id,
        "event_id": "event-a",
        "sequence": 1,
        "event": "route_ready",
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def _coverage(capture_id: str, window_count: int) -> dict[str, object]:
    return {
        "schema": "dcb-virtual-scroll-coverage.v1",
        "capture_id": capture_id,
        "windows": [{"window_id": str(index)} for index in range(window_count)],
        "messages": {},
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def test_event_append_completes_after_a_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    original_write = os.write
    injected = False

    def short_write(descriptor: int, content: bytes) -> int:
        nonlocal injected
        if not injected:
            injected = True
            return original_write(descriptor, content[:5])
        return original_write(descriptor, content)

    monkeypatch.setattr(store_module.os, "write", short_write)

    store.append_event(_event("capture-safe-a"), expected_sequence=0)

    assert store.load_events("capture-safe-a") == [_event("capture-safe-a")]


def test_event_append_rolls_back_when_write_fails_after_a_short_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    original_write = os.write
    calls = 0

    def fail_after_short_write(descriptor: int, content: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, content[:5])
        raise OSError("write failed for test")

    monkeypatch.setattr(store_module.os, "write", fail_after_short_write)

    with pytest.raises(CheckpointCorruptError, match="unwritable"):
        store.append_event(_event("capture-safe-a"), expected_sequence=0)

    assert store.load_events("capture-safe-a") == []
    assert store.ledger_path("capture-safe-a").read_bytes() == b""


def test_event_append_rolls_back_unexpected_concurrent_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    original_fsync = os.fsync
    injected = False

    def append_extra_before_fsync(descriptor: int) -> None:
        nonlocal injected
        if not injected and stat.S_ISREG(os.fstat(descriptor).st_mode):
            injected = True
            os.write(descriptor, b"ATTACK\n")
        original_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", append_extra_before_fsync)

    with pytest.raises(CheckpointCorruptError, match="changed during append"):
        store.append_event(_event("capture-safe-a"), expected_sequence=0)

    assert store.ledger_path("capture-safe-a").read_bytes() == b""


def test_atomic_json_keeps_committed_destination_when_directory_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_id = "capture-safe-a"
    store = CaptureCheckpointStore(tmp_path / "store")
    store.save_coverage(_coverage(capture_id, 0), expected_window_count=0)
    original_fsync = os.fsync

    def fail_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory fsync failed for test")
        original_fsync(descriptor)

    monkeypatch.setattr(store_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(CheckpointCorruptError, match="unwritable"):
        store.save_coverage(_coverage(capture_id, 1), expected_window_count=0)

    path = store.coverage_path(capture_id)
    assert path.is_file()
    assert len(json.loads(path.read_text(encoding="utf-8"))["windows"]) == 1


def test_atomic_json_closes_raw_descriptor_when_fdopen_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    original_open = os.open
    opened_file_descriptors: list[int] = []

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if not flags & os.O_DIRECTORY:
            opened_file_descriptors.append(descriptor)
        return descriptor

    def fail_fdopen(*args, **kwargs):
        raise OSError("fdopen failed for test")

    monkeypatch.setattr(store_module.os, "open", tracked_open)
    monkeypatch.setattr(store_module.os, "fdopen", fail_fdopen)
    monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: True)

    with pytest.raises(CheckpointCorruptError, match="unwritable"):
        store.save_coverage(_coverage("capture-safe-a", 0), expected_window_count=0)

    assert opened_file_descriptors
    for descriptor in opened_file_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_directory_chain_close_failure_does_not_mask_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    original_close = os.close

    def fail_close(descriptor: int) -> None:
        original_close(descriptor)
        raise OSError("close failed for test")

    monkeypatch.setattr(store_module.os, "close", fail_close)

    with pytest.raises(CheckpointCorruptError, match="link or invalid directory"):
        store_module._open_store_directory_chain(
            root, ("linked",), create=False
        )
