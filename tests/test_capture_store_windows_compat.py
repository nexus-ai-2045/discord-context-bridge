from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

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

CAPTURE_ID = "capture-windows-a"


@pytest.fixture(autouse=True)
def no_secure_dir_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: False)


def _checkpoint() -> dict[str, object]:
    return {
        "schema": "dcb-full-capture-orchestrator.v1",
        "capture_id": CAPTURE_ID,
        "target_digest": "target-windows-a",
        "state": "received",
        "checkpoints": [],
    }


def _coverage() -> dict[str, object]:
    return {
        "schema": "dcb-virtual-scroll-coverage.v1",
        "capture_id": CAPTURE_ID,
        "windows": [],
        "messages": {},
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def _message_ledger() -> dict[str, object]:
    return {
        "schema": "dcb-private-message-event-ledger.v1",
        "capture_id": CAPTURE_ID,
        "target_key": "target-windows-a",
        "upper_watermark": "watermark-windows-a",
        "events": [],
        "tip_hash": "",
        "outbound_actions": "disabled",
        "private_local_only": True,
    }


def _browser_receipt() -> dict[str, object]:
    observation = {
        "sequence": 1,
        "route": "in_app_browser",
        "state": "ready",
        "error_code": "none",
        "observed_at": "2026-08-14T00:00:00+00:00",
    }
    return {
        "schema": "dcb-browser-route-observation-receipt.v1",
        "schema_version": "1.0",
        "capture_id": CAPTURE_ID,
        "recorded_at": "2026-08-14T00:00:00+00:00",
        "recorded_by": "discord-context-bridge",
        "raw_text_returned": False,
        "outbound_actions": "disabled",
        "route": "in_app_browser",
        "latest_state": "ready",
        "observations": [observation],
    }


def _attachment_ledger() -> dict[str, object]:
    return {
        "schema": "dcb-private-attachment-save-ledger.v1",
        "capture_id": CAPTURE_ID,
        "records": [],
        "tip_hash": canonical_capture_digest([]),
        "seal": None,
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def test_legacy_backend_round_trips_normal_capture_artifacts(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")

    assert store.save_checkpoint(_checkpoint(), expected_sequence=0) == _checkpoint()
    assert store.load_checkpoint(CAPTURE_ID) == _checkpoint()

    coverage = _coverage()
    assert store.save_coverage(coverage, expected_window_count=0) == coverage
    assert store.load_coverage(CAPTURE_ID) == coverage

    messages = _message_ledger()
    assert store.save_message_ledger(messages, expected_sequence=0) == messages
    assert store.load_message_ledger(CAPTURE_ID) == messages

    event = {
        "schema": "dcb-capture-event.v1",
        "capture_id": CAPTURE_ID,
        "event_id": "event-windows-a",
        "sequence": 1,
        "state": "received",
    }
    assert store.append_event(event, expected_sequence=0)["appended"] is True
    assert store.load_events(CAPTURE_ID) == [event]

    receipt = _browser_receipt()
    store.save_receipt(store.browser_route_receipt_path(CAPTURE_ID), receipt)
    assert store.load_browser_route_receipt(CAPTURE_ID) == receipt
    assert list(store.root.rglob("*.tmp")) == []


def test_legacy_event_append_opens_binary_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    original_open = os.open
    native_binary = getattr(os, "O_BINARY", None)
    binary_flag = native_binary if native_binary is not None else 1 << 29
    observed_flags: list[int] = []

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        if str(path).endswith(".ndjson"):
            observed_flags.append(flags)
        forwarded = flags if native_binary is not None else flags & ~binary_flag
        return original_open(path, forwarded, mode, dir_fd=dir_fd)

    monkeypatch.setattr(store_module.os, "O_BINARY", binary_flag, raising=False)
    monkeypatch.setattr(store_module.os, "open", tracked_open)
    store.append_event(
        {
            "schema": "dcb-capture-event.v1",
            "capture_id": CAPTURE_ID,
            "event_id": "event-binary-a",
            "sequence": 1,
            "state": "received",
        },
        expected_sequence=0,
    )

    assert observed_flags
    assert observed_flags[-1] & binary_flag
    assert store.ledger_path(CAPTURE_ID).read_bytes().endswith(b"\n")


def test_legacy_backend_transition_lock_remains_usable(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")

    with store.transition_lock(CAPTURE_ID):
        assert store.checkpoint_path(CAPTURE_ID).parent.parent == store.root

    lock_path = store.root / "locks" / f"{CAPTURE_ID}.lock"
    assert lock_path.read_bytes() == b"\0"


def test_legacy_backend_keeps_attachment_storage_fail_closed(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")

    with pytest.raises(CheckpointCorruptError, match="unavailable"):
        store.save_attachment_save_ledger(_attachment_ledger(), expected_sequence=0)
    assert store.load_attachment_save_ledger(CAPTURE_ID) is None

    ledger_path = store.attachment_save_ledger_path(CAPTURE_ID)
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text("{}", encoding="utf-8")
    with pytest.raises(CheckpointCorruptError, match="unavailable"):
        store.load_attachment_save_ledger(CAPTURE_ID)
    with pytest.raises(CheckpointCorruptError, match="unavailable"):
        store.read_managed_object(
            f"attachment-objects/{CAPTURE_ID}/object.bin", max_bytes=100
        )


@requires_symlink_privilege
def test_legacy_backend_rejects_symlinked_store_directory(tmp_path: Path) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir()
    (store.root / "checkpoints").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CheckpointCorruptError, match="link|reparse"):
        store.save_checkpoint(_checkpoint(), expected_sequence=0)

    assert list(outside.iterdir()) == []


def test_legacy_backend_rejects_windows_reparse_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_file_attributes=0x400)
    monkeypatch.setattr(Path, "lstat", lambda self: fake)

    with pytest.raises(CheckpointCorruptError, match="reparse"):
        store_module._legacy_path_stat(Path("C:/store"))


def test_legacy_event_append_rolls_back_after_short_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    path = store.ledger_path(CAPTURE_ID)
    original_write = os.write
    calls = 0

    def partial_then_fail(descriptor: int, content: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, content[: max(1, len(content) // 2)])
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "write", partial_then_fail)
    event = {
        "schema": "dcb-capture-event.v1",
        "capture_id": CAPTURE_ID,
        "event_id": "event-windows-a",
        "sequence": 1,
        "state": "received",
    }
    with pytest.raises(CheckpointCorruptError, match="unwritable"):
        store.append_event(event, expected_sequence=0)

    assert path.read_bytes() == b""


def test_legacy_atomic_json_preserves_previous_file_after_short_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CaptureCheckpointStore(tmp_path / "store")
    original = _checkpoint()
    store.save_checkpoint(original, expected_sequence=0)
    path = store.checkpoint_path(CAPTURE_ID)
    before = path.read_bytes()
    original_write = os.write
    calls = 0

    def partial_then_fail(descriptor: int, content: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_write(descriptor, content[: max(1, len(content) // 2)])
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "write", partial_then_fail)
    updated = {
        **original,
        "state": "route_preflight",
        "checkpoints": [{"sequence": 1}],
    }
    with pytest.raises(CheckpointCorruptError, match="unwritable"):
        store.save_checkpoint(updated, expected_sequence=0)

    assert path.read_bytes() == before
    assert list(store.root.rglob("*.tmp")) == []
