from __future__ import annotations

import os

import pytest

import discord_context_bridge.capture.service as service_module
import discord_context_bridge.capture.store as store_module
from discord_context_bridge.capture.service import (
    merge_persisted_capture_window,
    record_persisted_attachment_save,
    seal_persisted_attachment_inventory,
    start_capture_loop,
)
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
    SequenceConflictError,
)


def _capture_with_two_attachments(tmp_path):
    store = CaptureCheckpointStore(tmp_path)
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
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "scan_pass": index + 1,
                "oldest_reached": True,
                "latest_reached": True,
                "messages": messages,
            },
            expected_window_count=index,
        )
    return store, capture_id


def test_attachment_save_rejects_managed_ref_reuse(tmp_path) -> None:
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

    managed = tmp_path / "attachment-objects" / capture_id / private_ref
    assert managed.read_bytes() == b"first"
    assert len(store.load_attachment_save_ledger(capture_id)["records"]) == 1


def test_attachment_save_idempotency_rechecks_managed_object(tmp_path) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    private_ref = "attachments/object.bin"
    record_persisted_attachment_save(
        store,
        capture_id,
        "attachment-1",
        source,
        private_ref,
        expected_sequence=0,
    )
    managed = tmp_path / "attachment-objects" / capture_id / private_ref
    managed.unlink()

    with pytest.raises(CheckpointCorruptError, match="missing"):
        record_persisted_attachment_save(
            store,
            capture_id,
            "attachment-1",
            source,
            private_ref,
            expected_sequence=1,
        )


def test_managed_write_closes_directory_fds_when_cleanup_fails(
    tmp_path, monkeypatch
) -> None:
    destination = tmp_path / "attachment-objects" / "capture-1" / "object.bin"
    original_open = os.open
    original_fstat = os.fstat
    opened_directory_fds: list[int] = []
    binding_checks = 0

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_DIRECTORY:
            opened_directory_fds.append(descriptor)
        return descriptor

    def fail_after_write(root, root_fd, bindings) -> bool:
        nonlocal binding_checks
        binding_checks += 1
        return binding_checks < 3

    def deny_cleanup(path, *, dir_fd=None):
        raise PermissionError("cleanup denied for test")

    monkeypatch.setattr(service_module.os, "open", tracked_open)
    monkeypatch.setattr(service_module, "_directory_bindings_match", fail_after_write)
    monkeypatch.setattr(service_module.os, "unlink", deny_cleanup)

    with pytest.raises(ValueError, match="directory changed"):
        service_module._atomic_write_managed_object_posix(
            tmp_path, destination, b"trusted"
        )

    assert opened_directory_fds
    for descriptor in opened_directory_fds:
        with pytest.raises(OSError):
            original_fstat(descriptor)


def test_attachment_seal_rejects_parent_swap_during_managed_read(
    tmp_path, monkeypatch
) -> None:
    store, capture_id = _capture_with_two_attachments(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    private_ref = "attachments/object.bin"
    record_persisted_attachment_save(
        store,
        capture_id,
        "attachment-1",
        source,
        private_ref,
        expected_sequence=0,
    )
    managed_parent = tmp_path / "attachment-objects" / capture_id / "attachments"
    displaced = managed_parent.with_name("attachments-displaced")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "object.bin").write_bytes(b"trusted")
    original_matches = store_module._store_bindings_match
    swapped = False

    def swap_after_binding_check(root, directory_fds, bindings) -> bool:
        nonlocal swapped
        matches = original_matches(root, directory_fds, bindings)
        if (
            matches
            and not swapped
            and bindings
            and bindings[-1][1] == "attachments"
        ):
            managed_parent.rename(displaced)
            try:
                managed_parent.symlink_to(outside, target_is_directory=True)
            except OSError:
                displaced.rename(managed_parent)
                pytest.skip("symlink creation is unavailable")
            swapped = True
        return matches

    monkeypatch.setattr(
        store_module,
        "_store_bindings_match",
        swap_after_binding_check,
    )
    with pytest.raises(CheckpointCorruptError, match="changed"):
        seal_persisted_attachment_inventory(
            store,
            capture_id,
            expected_sequence=1,
        )

    assert (outside / "object.bin").read_bytes() == b"trusted"
