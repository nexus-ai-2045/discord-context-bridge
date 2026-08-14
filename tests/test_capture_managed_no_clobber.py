from __future__ import annotations

import os

import pytest

import discord_context_bridge.capture.service as service_module
from discord_context_bridge.capture.service import (
    merge_persisted_capture_window,
    record_persisted_attachment_save,
    start_capture_loop,
)
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
    SequenceConflictError,
)


def _capture_with_two_attachments(tmp_path):
    store = CaptureCheckpointStore(tmp_path / "store")
    capture_id = start_capture_loop(
        store, "private-target", "saved_artifacts", "message-2"
    )["capture_id"]
    messages = [
        {
            "message_id": "message-1",
            "content_hash": "hash-1",
            "attachment_ids": ["attachment-1"],
        },
        {
            "message_id": "message-2",
            "content_hash": "hash-2",
            "attachment_ids": ["attachment-2"],
        },
    ]
    for index in range(2):
        merge_persisted_capture_window(
            store,
            capture_id,
            {
                "window_id": f"window-{index + 1}",
                "source": "saved_snapshot",
                "direction": "toward_latest",
                "scan_pass": index + 1,
                "oldest_reached": True,
                "latest_reached": True,
                "messages": messages,
            },
            expected_window_count=index,
        )
    return store, capture_id


def test_atomic_managed_write_never_clobbers_existing_destination(tmp_path) -> None:
    destination = tmp_path / "attachment-objects" / "capture-1" / "object.bin"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"first")

    with pytest.raises(ValueError, match="path changed"):
        service_module._atomic_write_managed_object_posix(
            tmp_path, destination, b"second"
        )

    assert destination.read_bytes() == b"first"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_attachment_save_rejects_exact_managed_ref_reuse(tmp_path) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    private_ref = "attachments/shared.bin"

    record_persisted_attachment_save(
        store,
        capture_id,
        "attachment-1",
        first,
        private_ref,
        expected_sequence=0,
    )
    with pytest.raises(SequenceConflictError, match="already in use"):
        record_persisted_attachment_save(
            store,
            capture_id,
            "attachment-2",
            second,
            private_ref,
            expected_sequence=1,
        )

    managed = (
        store.root / "attachment-objects" / capture_id / "attachments" / "shared.bin"
    )
    assert managed.read_bytes() == b"first"
    assert len(store.load_attachment_save_ledger(capture_id)["records"]) == 1


def test_attachment_save_rejects_case_only_filesystem_collision(tmp_path) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    record_persisted_attachment_save(
        store,
        capture_id,
        "attachment-1",
        first,
        "attachments/Object.bin",
        expected_sequence=0,
    )
    first_managed = (
        store.root / "attachment-objects" / capture_id / "attachments" / "Object.bin"
    )
    colliding = first_managed.with_name("object.bin")
    if not colliding.exists():
        pytest.skip("filesystem is case-sensitive")

    with pytest.raises(SequenceConflictError, match="already in use"):
        record_persisted_attachment_save(
            store,
            capture_id,
            "attachment-2",
            second,
            "attachments/object.bin",
            expected_sequence=1,
        )

    assert first_managed.read_bytes() == b"first"
    assert len(store.load_attachment_save_ledger(capture_id)["records"]) == 1
    assert list(first_managed.parent.glob(".object.bin.*.tmp")) == []


def test_no_clobber_commit_cleans_destination_and_closes_fds_on_fsync_failure(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "attachment-objects" / "capture-1" / "object.bin"
    original_open = os.open
    original_fstat = os.fstat
    original_fsync = os.fsync
    opened_directory_fds: list[int] = []
    fsync_calls = 0

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_DIRECTORY:
            opened_directory_fds.append(descriptor)
        return descriptor

    def fail_directory_fsync(descriptor):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("directory fsync failed for test")
        return original_fsync(descriptor)

    monkeypatch.setattr(service_module.os, "open", tracked_open)
    monkeypatch.setattr(service_module.os, "fsync", fail_directory_fsync)

    with pytest.raises(ValueError, match="path changed"):
        service_module._atomic_write_managed_object_posix(
            tmp_path, destination, b"trusted"
        )

    assert not destination.exists()
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []
    assert opened_directory_fds
    for descriptor in opened_directory_fds:
        with pytest.raises(OSError):
            original_fstat(descriptor)


def test_attachment_save_rolls_back_object_when_ledger_save_fails(
    tmp_path, monkeypatch
) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    original_save = store.save_attachment_save_ledger
    calls = 0

    def fail_once(ledger, *, expected_sequence):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CheckpointCorruptError("injected ledger failure")
        return original_save(ledger, expected_sequence=expected_sequence)

    monkeypatch.setattr(store, "save_attachment_save_ledger", fail_once)
    managed_ref = "attachments/object.bin"
    with pytest.raises(CheckpointCorruptError, match="injected"):
        record_persisted_attachment_save(
            store,
            capture_id,
            "attachment-1",
            source,
            managed_ref,
            expected_sequence=0,
        )

    managed = store.root / "attachment-objects" / capture_id / managed_ref
    assert not managed.exists()
    assert store.load_attachment_save_ledger(capture_id) is None

    result = record_persisted_attachment_save(
        store,
        capture_id,
        "attachment-1",
        source,
        managed_ref,
        expected_sequence=0,
    )
    assert result["attachment_sequence"] == 1
    assert managed.read_bytes() == b"trusted"


def test_attachment_save_adopts_matching_orphan_after_crash(tmp_path) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    managed_ref = f"attachment-objects/{capture_id}/attachments/object.bin"
    destination = store.root.joinpath(*managed_ref.split("/"))
    service_module._atomic_write_managed_object(
        store.root, destination, b"trusted"
    )

    result = record_persisted_attachment_save(
        store,
        capture_id,
        "attachment-1",
        source,
        "attachments/object.bin",
        expected_sequence=0,
    )

    assert result["attachment_sequence"] == 1
    assert destination.read_bytes() == b"trusted"
    assert len(store.load_attachment_save_ledger(capture_id)["records"]) == 1


def test_attachment_save_rejects_differing_orphan_after_crash(tmp_path) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    managed_ref = f"attachment-objects/{capture_id}/attachments/object.bin"
    destination = store.root.joinpath(*managed_ref.split("/"))
    service_module._atomic_write_managed_object(
        store.root, destination, b"different"
    )

    with pytest.raises(ValueError, match="orphan does not match"):
        record_persisted_attachment_save(
            store,
            capture_id,
            "attachment-1",
            source,
            "attachments/object.bin",
            expected_sequence=0,
        )

    assert destination.read_bytes() == b"different"
    assert store.load_attachment_save_ledger(capture_id) is None


def test_managed_write_closes_temporary_fd_when_fdopen_fails(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "attachment-objects" / "capture-1" / "object.bin"
    original_fdopen = os.fdopen
    original_fstat = os.fstat
    temporary_fd: int | None = None

    def fail_fdopen(descriptor, *args, **kwargs):
        nonlocal temporary_fd
        temporary_fd = descriptor
        raise OSError("injected fdopen failure")

    monkeypatch.setattr(service_module.os, "fdopen", fail_fdopen)
    with pytest.raises(ValueError, match="path changed"):
        service_module._atomic_write_managed_object_posix(
            tmp_path, destination, b"trusted"
        )

    monkeypatch.setattr(service_module.os, "fdopen", original_fdopen)
    assert temporary_fd is not None
    with pytest.raises(OSError):
        original_fstat(temporary_fd)
    assert not destination.exists()
