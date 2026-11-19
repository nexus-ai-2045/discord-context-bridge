from __future__ import annotations

import pytest

from discord_context_bridge.acquisition_gate import build_acquisition_completion_gate
from discord_context_bridge.capture.receipts import (
    closeout_correlation_digest,
    persist_browser_route_observation,
    persist_learning_handoff_receipt,
    persist_strict_full_capture_receipt,
)
from discord_context_bridge.capture.store import CaptureCheckpointStore, CaptureStoreError
from discord_context_bridge.capture.store import CheckpointCorruptError
from discord_context_bridge.capture.message_ledger import new_message_ledger
from discord_context_bridge.capture.virtual_scroll import new_virtual_scroll_coverage


def _full_gate(capture_id: str = "capture-safe-a") -> dict[str, object]:
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
            "messages": 1,
            "raw_records": 1,
            "markdown_messages": 1,
            "ledger_messages": 1,
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


def _seed_receipt_sources(store, capture_id="capture-safe-a"):
    store.save_message_ledger(
        new_message_ledger(
            capture_id, target_key="private-target", upper_watermark="message-1"
        ),
        expected_sequence=0,
    )
    store.save_coverage(
        new_virtual_scroll_coverage(capture_id), expected_window_count=0
    )


def _closed_post_send() -> dict[str, object]:
    return {
        "schema": "discord_post_send_closeout_packet.v1",
        "closeout_status": "closed",
        "human_sent_observed": True,
        "human_reviewed": True,
        "outbound_actions": "disabled",
    }


def _write_adapter_receipt(tmp_path, closeout, *, capture_id="capture-safe-a", **overrides):
    payload = {
        "schema": "absorbed_dialogue_learning_receipt.v1",
        "schema_version": "1.0",
        "capture_id": capture_id,
        "closeout_correlation_digest": closeout_correlation_digest(closeout),
        "status": "completed",
        "evidence_pointer": "absorbed-dialogue:case-123",
        "recorded_at": "2026-08-07T00:00:00+00:00",
        "recorded_by": "absorbed-dialogue-router",
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
    payload.update(overrides)
    path = tmp_path / "adapter-receipt.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    return path


def test_strict_full_receipt_is_atomically_persisted_and_consumer_bound(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    _seed_receipt_sources(store)

    receipt = persist_strict_full_capture_receipt(
        store,
        "capture-safe-a",
        _full_gate(),
        consumer="context_acquisition",
    )

    assert receipt["consumer_binding"] == "context_acquisition"
    assert receipt["schema_version"] == "1.0"
    assert receipt["recorded_by"] == "discord-context-bridge"
    assert store.load_full_capture_receipt("capture-safe-a", consumer="context_acquisition") == receipt
    assert list(tmp_path.rglob("*.tmp")) == []

    gate = build_acquisition_completion_gate(
        [{
            "capture_id": "capture-safe-a",
            "message_period": {
                "start": "2026-08-01T05:00:00+00:00",
                "end": "2026-08-01T07:00:00+00:00",
            },
            "content_hash": "safe-hash",
        }],
        requested_start="2026-08-01T05:00:00+00:00",
        requested_end="2026-08-01T07:00:00+00:00",
        freshness_status="recent",
        user_confirmed=True,
        full_capture_receipt=receipt,
    )
    assert gate["summary_ready"] is True


def test_acquisition_rejects_canonical_receipt_for_another_consumer(tmp_path) -> None:
    receipt = {**_full_gate(), "schema": "dcb-strict-full-capture-receipt.v1"}
    receipt["source_gate_schema"] = "discord_full_capture_completion_gate.v1"
    receipt["consumer_binding"] = "other_consumer"

    gate = build_acquisition_completion_gate(
        [{"capture_id": "capture-safe-a", "content_hash": "safe-hash"}],
        full_capture_receipt=receipt,
    )

    assert "full_capture_receipt_consumer_binding_invalid" in gate["blockers"]


def test_acquisition_rejects_canonical_receipt_without_provenance() -> None:
    receipt = {**_full_gate(), "schema": "dcb-strict-full-capture-receipt.v1"}
    receipt.update(
        source_gate_schema="discord_full_capture_completion_gate.v1",
        consumer_binding="context_acquisition",
    )
    gate = build_acquisition_completion_gate(
        [{"capture_id": "capture-safe-a", "content_hash": "safe-hash"}],
        full_capture_receipt=receipt,
    )
    assert "full_capture_receipt_provenance_invalid" in gate["blockers"]


def test_strict_full_receipt_rejects_self_attested_or_wrong_capture_gate(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    blocked = _full_gate()
    blocked["full_capture_confirmed"] = False

    with pytest.raises(CaptureStoreError):
        persist_strict_full_capture_receipt(
            store, "capture-safe-a", blocked, consumer="context_acquisition"
        )
    with pytest.raises(CaptureStoreError):
        persist_strict_full_capture_receipt(
            store, "capture-safe-a", _full_gate("other-capture"), consumer="context_acquisition"
        )


def test_strict_full_receipt_uses_metadata_allowlist(tmp_path) -> None:
    gate = {**_full_gate(), "raw_private_extension": "must-not-persist"}
    store = CaptureCheckpointStore(tmp_path)
    _seed_receipt_sources(store)
    receipt = persist_strict_full_capture_receipt(
        store, "capture-safe-a", gate, consumer="context_acquisition"
    )
    assert "raw_private_extension" not in receipt


def test_browser_route_observation_is_capture_bound_and_normalized(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)

    receipt = persist_browser_route_observation(
        store,
        "capture-safe-a",
        route="chrome_extension",
        observed_state="Blocked Extension UI",
        error_code="popup-open",
    )

    assert receipt["capture_id"] == "capture-safe-a"
    assert receipt["latest_state"] == "blocked_extension_ui"
    assert receipt["observations"][0]["error_code"] == "popup_open"
    assert receipt["observations"][0]["route"] == "chrome_extension"
    assert receipt["schema_version"] == "1.0"
    assert receipt["raw_text_returned"] is False


def test_browser_route_observation_maps_untrusted_values_to_unknown(tmp_path) -> None:
    receipt = persist_browser_route_observation(
        CaptureCheckpointStore(tmp_path),
        "capture-safe-a",
        route="private channel name",
        observed_state="private failure text",
        error_code="private error text",
    )

    assert receipt["route"] == "unknown"
    assert receipt["latest_state"] == "unknown"
    assert receipt["observations"][0]["error_code"] == "unknown"


def test_learning_handoff_remains_held_until_canonical_adapter_contract_exists(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    closeout = _closed_post_send()

    missing = persist_learning_handoff_receipt(store, "capture-safe-a", closeout)
    assert missing["status"] == "held"
    assert missing["hold_reason"] == "source_evidence_missing"

    observed = persist_learning_handoff_receipt(
        CaptureCheckpointStore(tmp_path / "complete"),
        "capture-safe-a",
        closeout,
        adapter_receipt_path=_write_adapter_receipt(tmp_path, closeout),
    )
    assert observed["status"] == "held"
    assert observed["completion_confirmed"] is False
    assert observed["hold_reason"] == "adapter_contract_unavailable"
    assert observed["evidence_pointer_output"] == "omitted"
    assert observed["evidence_pointer_digest"]


def test_learning_handoff_hold_is_explicit_and_not_reported_completed(tmp_path) -> None:
    receipt = persist_learning_handoff_receipt(
        CaptureCheckpointStore(tmp_path),
        "capture-safe-a",
        _closed_post_send(),
        hold_reason="human_review_required",
    )

    assert receipt["status"] == "held"
    assert receipt["completion_confirmed"] is False
    assert receipt["hold_reason"] == "human_review_required"


def test_learning_handoff_rejects_closeout_self_attestation(tmp_path) -> None:
    closeout = _closed_post_send()
    closeout["human_sent_observed"] = False

    with pytest.raises(CaptureStoreError):
        persist_learning_handoff_receipt(
            CaptureCheckpointStore(tmp_path),
            "capture-safe-a",
            closeout,
            adapter_receipt_path=_write_adapter_receipt(tmp_path, closeout),
        )


@pytest.mark.parametrize("mismatch", ["capture", "closeout"])
def test_learning_handoff_holds_on_adapter_correlation_mismatch(tmp_path, mismatch) -> None:
    closeout = _closed_post_send()
    overrides = (
        {"capture_id": "other-capture"}
        if mismatch == "capture"
        else {"closeout_correlation_digest": "0" * 64}
    )
    receipt = persist_learning_handoff_receipt(
        CaptureCheckpointStore(tmp_path / "store"),
        "capture-safe-a",
        closeout,
        adapter_receipt_path=_write_adapter_receipt(tmp_path, closeout, **overrides),
    )
    assert receipt["status"] == "held"
    assert receipt["completion_confirmed"] is False
    assert receipt["hold_reason"] == "source_evidence_mismatch"


def test_receipt_load_rejects_missing_provenance_and_corrupt_browser_sequence(tmp_path) -> None:
    store = CaptureCheckpointStore(tmp_path)
    browser = persist_browser_route_observation(
        store, "capture-safe-a", route="chrome_extension", observed_state="ready"
    )
    browser.pop("recorded_by")
    browser["observations"][0]["sequence"] = 2
    store.browser_route_receipt_path("capture-safe-a").write_text(
        __import__("json").dumps(browser), encoding="utf-8"
    )

    with pytest.raises(CheckpointCorruptError):
        store.load_browser_route_receipt("capture-safe-a")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["observations"].clear(),
        lambda payload: payload["observations"][0].update(error_code="private"),
        lambda payload: payload["observations"][0].update(observed_at="not-a-time"),
        lambda payload: payload.update(latest_state="auth_required"),
        lambda payload: payload.update(route="in_app_browser"),
    ],
)
def test_browser_receipt_load_rejects_inconsistent_projection(tmp_path, mutation) -> None:
    store = CaptureCheckpointStore(tmp_path)
    payload = persist_browser_route_observation(
        store, "capture-safe-a", route="chrome_extension", observed_state="ready"
    )
    mutation(payload)
    store.browser_route_receipt_path("capture-safe-a").write_text(
        __import__("json").dumps(payload), encoding="utf-8"
    )
    with pytest.raises(CheckpointCorruptError):
        store.load_browser_route_receipt("capture-safe-a")


def test_full_receipt_nested_metadata_is_projected_to_exact_keys(tmp_path) -> None:
    gate = _full_gate()
    gate["boundaries"]["private_extra"] = "drop"
    gate["counts"]["private_extra"] = 99
    gate["message_period"] = {
        "start": "2026-08-01T05:00:00+00:00",
        "end": "2026-08-01T07:00:00+00:00",
        "private_extra": "drop",
    }
    store = CaptureCheckpointStore(tmp_path)
    _seed_receipt_sources(store)
    receipt = persist_strict_full_capture_receipt(
        store, "capture-safe-a", gate, consumer="context_acquisition"
    )
    assert set(receipt["boundaries"]) == {
        "oldest_reached", "latest_reached", "capture_stable_after_rescan"
    }
    assert "private_extra" not in receipt["counts"]
    assert set(receipt["message_period"]) == {"start", "end"}
