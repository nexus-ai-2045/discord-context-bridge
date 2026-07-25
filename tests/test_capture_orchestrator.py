from __future__ import annotations

from discord_context_bridge.capture.orchestrator import advance_capture_run, new_capture_run
from discord_context_bridge.capture.reconcile import build_reconciliation_evidence
import pytest


def test_new_run_separates_immediate_and_persistent_lanes_and_is_idempotent() -> None:
    first = new_capture_run(
        target_key="discord:channel:thread",
        route="in_app_browser",
        upper_watermark="message-99",
        retry_budget=3,
    )
    second = new_capture_run(
        target_key="discord:channel:thread",
        route="in_app_browser",
        upper_watermark="message-99",
        retry_budget=3,
    )

    assert first["capture_id"] == second["capture_id"]
    assert first["idempotency_key"] == second["idempotency_key"]
    assert first["state"] == "received"
    assert first["lanes"]["immediate_visible"]["terminal_for_full_capture"] is False
    assert first["lanes"]["background_full"]["persistent"] is True
    assert first["retry"]["budget"] == 3
    assert "target_key" not in first  # identifiers are represented only by digests


def test_happy_path_is_checkpointed_and_resumable_until_full_closed() -> None:
    run = new_capture_run("discord:thread", "chrome_extension", "m9")
    sequence = [
        ("visible_snapshot_saved", "route_preflight"),
        ("route_ready", "traversing_to_oldest"),
        ("oldest_reached", "traversing_to_latest"),
        ("latest_reached", "attachment_inventory"),
        ("attachment_inventory_complete", "downloading_attachments"),
        ("attachments_saved", "reconciling"),
        ("reconciled", "stable_rescan"),
        ("stable_rescan_complete", "gate_evaluating"),
    ]
    for event, state in sequence:
        run = advance_capture_run(run, event)
        assert run["state"] == state

    run = advance_capture_run(run, {
        "type": "gate_full",
        "capture_id": run["capture_id"],
        "gate": {"status": "full", "full_capture_confirmed": True, "outbound_actions": "disabled"},
    })
    assert run["state"] == "full_closed"
    assert run["lanes"]["immediate_visible"]["status"] == "saved"
    assert run["lanes"]["background_full"]["status"] == "full"
    assert run["checkpoints"][-1]["event"] == "gate_full"
    assert len(run["checkpoints"]) == len(sequence) + 1


def test_retry_is_bounded_and_preserves_resume_state() -> None:
    run = new_capture_run("discord:thread", "rest_backfill", "m9", retry_budget=1)
    run = advance_capture_run(run, "visible_snapshot_saved")
    run = advance_capture_run(run, "route_ready")
    run = advance_capture_run(run, "retryable_failure")
    assert run["state"] == "retry_wait"
    assert run["resume_state"] == "traversing_to_oldest"
    assert run["retry"]["used"] == 1

    run = advance_capture_run(run, "retry_due")
    assert run["state"] == "traversing_to_oldest"
    run = advance_capture_run(run, "retryable_failure")
    assert run["state"] == "blocked_closed"
    assert run["blocker"] == "retry_budget_exhausted"


def test_rest_and_saved_routes_do_not_require_visible_snapshot() -> None:
    for route in ("rest_backfill", "saved_artifacts"):
        run = new_capture_run("discord:thread", route, "m9")
        run = advance_capture_run(run, "route_ready")
        assert run["state"] == "traversing_to_oldest"


def test_full_close_requires_bound_gate_result() -> None:
    run = new_capture_run("discord:thread", "chrome_extension", "m9")
    for event in (
        "visible_snapshot_saved", "route_ready", "oldest_reached", "latest_reached",
        "attachment_inventory_complete", "attachments_saved", "reconciled", "stable_rescan_complete",
    ):
        run = advance_capture_run(run, event)
    with pytest.raises(ValueError, match="bound strict"):
        advance_capture_run(run, "gate_full")


def test_auth_and_computer_use_pause_instead_of_auto_fallback() -> None:
    for event, blocker in [
        ("auth_required", "auth_required"),
        ("human_approval_required", "human_approval_required"),
    ]:
        run = new_capture_run("discord:thread", "in_app_browser", "m9")
        run = advance_capture_run(run, event)
        expected = "paused_auth" if event == "auth_required" else "paused_human_approval"
        assert run["state"] == expected
        assert run["blocker"] == blocker
        assert run["automatic_fallback_attempted"] is False


def test_reconciliation_uses_sets_order_digests_and_attachment_inventory() -> None:
    evidence = build_reconciliation_evidence(
        raw_message_ids=["1", "2", "3"],
        markdown_message_ids=["1", "2", "3"],
        ledger_message_ids=["1", "2", "3"],
        discovered_attachment_ids=["a", "b"],
        saved_attachment_ids=["b", "a"],
        manifest_attachment_ids=["a", "b"],
        attachment_inventory_traversal_complete=True,
    )

    assert evidence["message_id_sets_equal"] is True
    assert evidence["ordered_message_digest_equal"] is True
    assert evidence["duplicate_message_count"] == 0
    assert evidence["attachment_inventory_complete"] is True
    assert evidence["attachment_id_sets_equal"] is True
    assert set(evidence) <= {
        "message_count", "raw_record_count", "raw_message_count", "markdown_message_count", "ledger_message_count",
        "message_id_sets_equal", "ordered_message_digest_equal",
        "duplicate_message_count",
        "attachment_discovered_count", "attachment_saved_count",
        "attachment_manifest_count", "attachment_inventory_complete",
        "attachment_id_sets_equal", "evidence_producer",
    }


def test_reconciliation_detects_same_count_different_ids_and_order() -> None:
    evidence = build_reconciliation_evidence(
        raw_message_ids=["1", "2"],
        markdown_message_ids=["2", "1"],
        ledger_message_ids=["1", "9"],
        discovered_attachment_ids=[],
        saved_attachment_ids=[],
        manifest_attachment_ids=[],
        attachment_inventory_traversal_complete=False,
    )
    assert evidence["message_id_sets_equal"] is False
    assert evidence["ordered_message_digest_equal"] is False
    assert evidence["attachment_inventory_complete"] is False


def test_duplicate_count_is_maximum_excess_across_projections() -> None:
    evidence = build_reconciliation_evidence(
        raw_message_ids=["1", "1", "2"],
        markdown_message_ids=["1", "2"],
        ledger_message_ids=["1", "2"],
        discovered_attachment_ids=[],
        saved_attachment_ids=[],
        manifest_attachment_ids=[],
        attachment_inventory_traversal_complete=True,
    )
    assert evidence["duplicate_message_count"] == 1
