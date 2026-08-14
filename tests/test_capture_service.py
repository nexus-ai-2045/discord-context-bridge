from __future__ import annotations

import json
import threading
from hashlib import sha256

import pytest

from discord_context_bridge.capture.service import (
    append_persisted_message_event,
    advance_persisted_capture,
    merge_persisted_capture_window,
    start_capture_loop,
)
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore,
    CheckpointCorruptError,
    SequenceConflictError,
)
from discord_context_bridge.cli import main
import discord_context_bridge.cli as cli_module
import discord_context_bridge.capture.service as capture_service_module


def test_start_and_advance_are_metadata_only_and_durable(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store,
        "private-discord-url",
        "chrome_extension",
        "latest-message",
        tag_context={"scope": "thread_only", "refresh_check": True},
    )
    capture_id = status["capture_id"]

    assert status["raw_text_returned"] is False
    assert "private-discord-url" not in str(status)
    advanced = advance_persisted_capture(
        store,
        capture_id,
        "visible-snapshot-1",
        "visible_snapshot_saved",
        expected_sequence=0,
    )

    assert advanced["state"] == "route_preflight"
    assert len(store.load_events(capture_id)) == 1
    assert len(store.load_checkpoint(capture_id)["checkpoints"]) == 1
    duplicate = advance_persisted_capture(
        store,
        capture_id,
        "visible-snapshot-1",
        "visible_snapshot_saved",
        expected_sequence=0,
    )
    assert duplicate == advanced
    assert len(store.load_events(capture_id)) == 1


def test_stale_transition_fails_closed(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store, "private-target", "in_app_browser", "latest-message"
    )
    capture_id = status["capture_id"]
    advance_persisted_capture(
        store,
        capture_id,
        "visible-snapshot-1",
        "visible_snapshot_saved",
        expected_sequence=0,
    )

    with pytest.raises(SequenceConflictError):
        advance_persisted_capture(
            store,
            capture_id,
            "visible-snapshot-2",
            "visible_snapshot_saved",
            expected_sequence=0,
        )


def test_duplicate_event_recovers_checkpoint_after_ledger_first_crash(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store, "private-target", "saved_artifacts", "latest-message"
    )
    capture_id = status["capture_id"]
    store.append_event(
        {
            "schema": "dcb-capture-event.v1",
            "event_id": "route-ready-1",
            "capture_id": capture_id,
            "sequence": 1,
            "event": "route_ready",
            "semantic_event_digest": sha256(
                json.dumps(
                    {"type": "route_ready"},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "state": "traversing_to_oldest",
            "blocker": None,
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        },
        expected_sequence=0,
    )

    recovered = advance_persisted_capture(
        store,
        capture_id,
        "route-ready-1",
        "route_ready",
        expected_sequence=0,
    )

    assert recovered["state"] == "traversing_to_oldest"
    assert len(store.load_checkpoint(capture_id)["checkpoints"]) == 1


def test_capture_loop_cli_start_is_metadata_only(tmp_path, capsys) -> None:
    code = main(
        [
            "capture-loop",
            "start",
            "--store-root",
            str(tmp_path),
            "--target-key",
            "private-discord-url",
            "--route",
            "saved_artifacts",
            "--upper-watermark",
            "latest-message",
            "--scope",
            "thread_only",
            "--refresh-check",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 0
    assert "private-discord-url" not in output
    assert '"outbound_actions": "disabled"' in output
    assert '"thread-only"' in output


def test_capture_loop_cli_does_not_echo_invalid_event(tmp_path, capsys) -> None:
    status = start_capture_loop(
        CaptureCheckpointStore(tmp_path),
        "private-target",
        "in_app_browser",
        "latest-message",
    )
    code = main(
        [
            "capture-loop",
            "advance",
            "--store-root",
            str(tmp_path),
            "--capture-id",
            status["capture_id"],
            "--event",
            "private-discord-url",
            "--event-id",
            "invalid-event-1",
            "--expected-sequence",
            "0",
            "--json",
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert "private-discord-url" not in output
    assert '"reason": "capture_loop_operation_failed"' in output


def _capture_with_ledger(
    tmp_path, *, stable: bool = True, with_attachment: bool = False
) -> str:
    store = CaptureCheckpointStore(tmp_path)
    status = start_capture_loop(
        store, "private-target", "saved_artifacts", "message-2"
    )
    merge_persisted_capture_window(
        store,
        status["capture_id"],
        {
            "window_id": "window-1",
            "source": "chrome_visible_dom",
            "direction": "toward_latest",
            "scan_pass": 1,
            "oldest_reached": True,
            "latest_reached": True,
            "messages": [
                {"message_id": "message-1", "content_hash": "hash-1"},
                {
                    "message_id": "message-2",
                    "content_hash": "hash-2",
                    "attachment_ids": ["attachment-1"] if with_attachment else [],
                },
            ],
        },
        expected_window_count=0,
    )
    if stable:
        merge_persisted_capture_window(
            store,
            status["capture_id"],
            {
                "window_id": "window-2",
                "source": "chrome_visible_dom",
                "direction": "toward_latest",
                "scan_pass": 2,
                "oldest_reached": True,
                "latest_reached": True,
                "messages": [
                    {"message_id": "message-1", "content_hash": "hash-1"},
                    {
                        "message_id": "message-2",
                        "content_hash": "hash-2",
                        "attachment_ids": ["attachment-1"] if with_attachment else [],
                    },
                ],
            },
            expected_window_count=1,
        )
    return status["capture_id"]


def test_capture_loop_cli_reconcile_reports_partial_without_receipt(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path, stable=False)

    code = main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["status"] == "partial"
    assert payload["full_capture_confirmed"] is False
    assert payload["receipt_persisted"] is False
    assert CaptureCheckpointStore(tmp_path).load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) is None


def test_capture_loop_cli_reconcile_persists_receipt_only_after_strict_full_gate(
    tmp_path, capsys
) -> None:
    capture_id = _capture_with_ledger(tmp_path)

    code = main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    receipt = CaptureCheckpointStore(tmp_path).load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    )

    assert code == 0
    assert payload["status"] == "full"
    assert payload["full_capture_confirmed"] is True
    assert payload["receipt_persisted"] is True
    assert receipt is not None
    assert receipt["source_gate_schema"] == "discord_full_capture_completion_gate.v1"
    assert "private-target" not in output
    assert "discord.com/channels" not in output


def test_capture_loop_cli_reconcile_without_durable_evidence_fails_closed_without_echo(
    tmp_path, capsys
) -> None:
    capture_id = start_capture_loop(
        CaptureCheckpointStore(tmp_path),
        "private-discord-url",
        "saved_artifacts",
        "message-2",
    )["capture_id"]

    code = main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    )
    output = capsys.readouterr().out

    assert code == 2
    assert '"reason": "capture_loop_operation_failed"' in output
    assert "private-discord-url" not in output


def test_capture_loop_cli_reconcile_serializes_observe_through_receipt_persist(
    tmp_path, capsys, monkeypatch
) -> None:
    capture_id = _capture_with_ledger(tmp_path)
    original_evaluate = cli_module.evaluate_full_capture
    observe_errors: list[Exception] = []
    attempted = threading.Event()

    def evaluate_with_competing_observe(evidence):
        gate = original_evaluate(evidence)
        if gate.get("full_capture_confirmed") and not attempted.is_set():
            attempted.set()

            def observe() -> None:
                try:
                    merge_persisted_capture_window(
                        CaptureCheckpointStore(tmp_path),
                        capture_id,
                        {
                            "window_id": "competing-window",
                            "source": "chrome_visible_dom",
                            "direction": "toward_latest",
                            "messages": [
                                {"message_id": "message-3", "content_hash": "hash-3"}
                            ],
                        },
                        expected_window_count=2,
                    )
                except Exception as error:  # captured for the main test thread
                    observe_errors.append(error)

            worker = threading.Thread(target=observe)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
        return gate

    monkeypatch.setattr(cli_module, "evaluate_full_capture", evaluate_with_competing_observe)
    code = main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["receipt_persisted"] is True
    assert len(observe_errors) == 1
    assert isinstance(observe_errors[0], SequenceConflictError)
    assert len(CaptureCheckpointStore(tmp_path).load_coverage(capture_id)["windows"]) == 2


def test_capture_loop_cli_reconcile_fails_closed_for_unsealed_attachments(
    tmp_path, capsys
) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)

    code = main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["status"] == "partial"
    assert payload["full_capture_confirmed"] is False
    assert payload["receipt_persisted"] is False


def test_attachment_save_seal_and_reconcile_supports_attachment_full(
    tmp_path, capsys
) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    object_file = tmp_path / "private-object.bin"
    object_file.write_bytes(b"attachment-content")
    save_args = [
        "capture-loop", "attachment-save", "--store-root", str(tmp_path),
        "--capture-id", capture_id, "--attachment-id", "attachment-1",
        "--object-file", str(object_file), "--private-ref", "attachments/object.bin",
        "--expected-attachment-sequence", "0", "--json",
    ]

    assert main(
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 2
    capsys.readouterr()
    assert main(save_args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["idempotent"] is False
    assert main(save_args) == 0
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["idempotent"] is True
    assert main(
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--expected-attachment-sequence", "1", "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled["status"] == "full"
    assert reconciled["receipt_persisted"] is True


def test_attachment_save_rejects_unknown_missing_and_hash_mismatch(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    object_file = tmp_path / "object.bin"
    object_file.write_bytes(b"one")

    def save(attachment_id: str, path, sequence: int = 0) -> int:
        return main(
            [
                "capture-loop", "attachment-save", "--store-root", str(tmp_path),
                "--capture-id", capture_id, "--attachment-id", attachment_id,
                "--object-file", str(path), "--private-ref", "attachments/object.bin",
                "--expected-attachment-sequence", str(sequence), "--json",
            ]
        )

    assert save("unknown", object_file) == 2
    capsys.readouterr()
    other_capture = start_capture_loop(
        CaptureCheckpointStore(tmp_path),
        "other-private-target",
        "saved_artifacts",
        "message-9",
    )["capture_id"]
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", other_capture, "--attachment-id", "attachment-1",
            "--object-file", str(object_file), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 2
    capsys.readouterr()
    assert save("attachment-1", tmp_path / "missing.bin") == 2
    capsys.readouterr()
    assert save("attachment-1", object_file) == 0
    capsys.readouterr()
    object_file.write_bytes(b"two")
    assert save("attachment-1", object_file, 1) == 2
    output = capsys.readouterr().out
    assert "object.bin" not in output


@pytest.mark.parametrize(
    "private_ref", ["../object.bin", "attachments\\object.bin", "C:/object.bin", "/object.bin"]
)
def test_attachment_save_rejects_unsafe_private_ref(tmp_path, capsys, private_ref) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", private_ref,
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 2
    capsys.readouterr()


def test_attachment_seal_is_invalidated_by_later_observe(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    object_file = tmp_path / "object.bin"
    object_file.write_bytes(b"one")
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(object_file), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--expected-attachment-sequence", "1", "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    capsys.readouterr()
    merge_persisted_capture_window(
        CaptureCheckpointStore(tmp_path),
        capture_id,
        {
            "window_id": "window-3",
            "source": "chrome_visible_dom",
            "direction": "stationary",
            "messages": [
                {
                    "message_id": "message-2", "content_hash": "hash-2",
                    "attachment_ids": ["attachment-1"],
                }
            ],
        },
        expected_window_count=2,
    )
    assert CaptureCheckpointStore(tmp_path).load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) is None
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "partial"
    assert payload["receipt_persisted"] is False


@pytest.mark.parametrize("mutation", ["delete", "replace"])
def test_reconcile_rejects_deleted_or_replaced_managed_attachment(
    tmp_path, capsys, mutation
) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--expected-attachment-sequence", "1", "--json",
        ]
    ) == 0
    capsys.readouterr()
    managed = tmp_path / "attachment-objects" / capture_id / "attachments" / "object.bin"
    if mutation == "delete":
        managed.unlink()
    else:
        managed.write_bytes(b"replaced")

    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "partial"
    assert payload["receipt_persisted"] is False


@pytest.mark.parametrize("mutation", ["delete", "replace"])
def test_full_receipt_load_rechecks_managed_attachment_object(
    tmp_path, capsys, mutation
) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    commands = [
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", "attachments/日本語.bin",
            "--expected-attachment-sequence", "0", "--json",
        ],
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--expected-attachment-sequence", "1", "--json",
        ],
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ],
    ]
    for command in commands:
        assert main(command) == 0
        capsys.readouterr()
    managed = tmp_path / "attachment-objects" / capture_id / "attachments" / "日本語.bin"
    if mutation == "delete":
        managed.unlink()
    else:
        managed.write_bytes(b"replaced")
    with pytest.raises(CheckpointCorruptError, match="managed object"):
        CaptureCheckpointStore(tmp_path).load_full_capture_receipt(
            capture_id, consumer="context_acquisition"
        )


def test_attachment_save_rejects_symlinked_managed_parent(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    managed_capture = tmp_path / "attachment-objects" / capture_id
    managed_capture.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (managed_capture / "attachments").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 2
    capsys.readouterr()
    assert list(outside.iterdir()) == []


def test_attachment_save_rejects_parent_swap_after_path_check(
    tmp_path, capsys, monkeypatch
) -> None:
    if not capture_service_module._secure_dir_fd_writes_supported():
        pytest.skip("secure directory-relative writes are unavailable")
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    managed_parent = (
        tmp_path / "attachment-objects" / capture_id / "attachments"
    )
    managed_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_matches = capture_service_module._directory_binding_matches
    binding_checks = 0

    def swap_parent_after_check(parent_fd, name, child_fd) -> bool:
        nonlocal binding_checks
        matches = original_matches(parent_fd, name, child_fd)
        binding_checks += 1
        if binding_checks != 3:
            return matches
        displaced = managed_parent.with_name("attachments-displaced")
        managed_parent.rename(displaced)
        try:
            managed_parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            displaced.rename(managed_parent)
            pytest.skip("symlink creation is unavailable")
        return matches

    monkeypatch.setattr(
        capture_service_module,
        "_directory_binding_matches",
        swap_parent_after_check,
    )
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 2
    capsys.readouterr()
    assert list(outside.iterdir()) == []


def test_attachment_save_fails_closed_without_secure_directory_writes(
    tmp_path, capsys, monkeypatch
) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    monkeypatch.setattr(
        capture_service_module,
        "_secure_dir_fd_writes_supported",
        lambda: False,
    )

    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 2
    capsys.readouterr()
    managed = (
        tmp_path / "attachment-objects" / capture_id / "attachments" / "object.bin"
    )
    assert not managed.exists()
    assert (
        CaptureCheckpointStore(tmp_path).load_attachment_save_ledger(capture_id) is None
    )


def test_attachment_ledger_ref_and_tip_tamper_fail_closed(tmp_path) -> None:
    capture_id = _capture_with_ledger(tmp_path, with_attachment=True)
    source = tmp_path / "source.bin"
    source.write_bytes(b"trusted")
    store = CaptureCheckpointStore(tmp_path)
    assert main(
        [
            "capture-loop", "attachment-save", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--attachment-id", "attachment-1",
            "--object-file", str(source), "--private-ref", "attachments/object.bin",
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 0
    ledger = store.load_attachment_save_ledger(capture_id)
    ledger["records"][0]["managed_ref"] = "../other.bin"
    store.attachment_save_ledger_path(capture_id).write_text(
        json.dumps(ledger), encoding="utf-8"
    )
    with pytest.raises(CheckpointCorruptError):
        store.load_attachment_save_ledger(capture_id)


def test_direct_message_append_invalidates_full_receipt(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path)
    store = CaptureCheckpointStore(tmp_path)
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    capsys.readouterr()
    ledger = store.load_message_ledger(capture_id)
    append_persisted_message_event(
        store,
        capture_id,
        {
            "event_id": "direct-event-1",
            "sequence": len(ledger["events"]) + 1,
            "type": "message_observed",
            "message_id": "message-3",
            "content_hash": "hash-3",
            "attachment_ids": [],
            "source": "saved_snapshot",
        },
        expected_sequence=len(ledger["events"]),
    )
    assert store.full_capture_receipt_path(capture_id).exists() is False


def test_attachment_seal_after_receipt_invalidates_and_reconcile_recovers(
    tmp_path, capsys
) -> None:
    capture_id = _capture_with_ledger(tmp_path)
    store = CaptureCheckpointStore(tmp_path)
    reconcile = [
        "capture-loop", "reconcile", "--store-root", str(tmp_path),
        "--capture-id", capture_id, "--json",
    ]

    assert main(reconcile) == 0
    capsys.readouterr()
    assert store.full_capture_receipt_path(capture_id).exists() is True

    assert main(
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id,
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert store.full_capture_receipt_path(capture_id).exists() is False

    assert main(reconcile) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "full"
    assert payload["receipt_persisted"] is True
    assert store.full_capture_receipt_path(capture_id).exists() is True

    assert main(
        [
            "capture-loop", "attachment-seal", "--store-root", str(tmp_path),
            "--capture-id", capture_id,
            "--expected-attachment-sequence", "0", "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert store.full_capture_receipt_path(capture_id).exists() is True


def test_full_receipt_source_binding_rejects_direct_coverage_change(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path)
    store = CaptureCheckpointStore(tmp_path)
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    capsys.readouterr()
    coverage = store.load_coverage(capture_id)
    coverage["final_pass_new_message_count"] = 99
    store.coverage_path(capture_id).write_text(json.dumps(coverage), encoding="utf-8")
    with pytest.raises(CheckpointCorruptError, match="source binding is stale"):
        store.load_full_capture_receipt(capture_id, consumer="context_acquisition")


def test_retryable_failure_invalidates_full_receipt(tmp_path, capsys) -> None:
    capture_id = _capture_with_ledger(tmp_path)
    store = CaptureCheckpointStore(tmp_path)
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert store.full_capture_receipt_path(capture_id).exists() is True

    status = advance_persisted_capture(
        store,
        capture_id,
        "retryable-failure-after-receipt",
        "retryable_failure",
        expected_sequence=0,
    )

    assert status["state"] == "retry_wait"
    assert store.full_capture_receipt_path(capture_id).exists() is False
    assert store.load_full_capture_receipt(
        capture_id, consumer="context_acquisition"
    ) is None


def test_event_checkpoint_crash_window_invalidates_full_receipt(
    tmp_path, capsys, monkeypatch
) -> None:
    capture_id = _capture_with_ledger(tmp_path)
    store = CaptureCheckpointStore(tmp_path)
    assert main(
        [
            "capture-loop", "reconcile", "--store-root", str(tmp_path),
            "--capture-id", capture_id, "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert store.full_capture_receipt_path(capture_id).exists() is True

    def fail_checkpoint_save(*args, **kwargs):
        raise OSError("simulated checkpoint write failure")

    monkeypatch.setattr(store, "save_checkpoint", fail_checkpoint_save)
    with pytest.raises(OSError, match="simulated checkpoint write failure"):
        advance_persisted_capture(
            store,
            capture_id,
            "retryable-failure-before-checkpoint",
            "retryable_failure",
            expected_sequence=0,
        )

    assert store.full_capture_receipt_path(capture_id).exists() is False
    assert len(store.load_events(capture_id)) == 1
    assert len(store.load_checkpoint(capture_id)["checkpoints"]) == 0
