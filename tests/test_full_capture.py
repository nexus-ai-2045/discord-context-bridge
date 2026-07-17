from __future__ import annotations

import json

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.full_capture import (
    build_capture_route_policy,
    evaluate_full_capture,
)


def complete_evidence(**overrides):
    evidence = {
        "route": "in_app_browser",
        "evidence_producer": "discord_context_bridge.capture.reconcile.v1",
        "target_bound": True,
        "capture_id_present": True,
        "evidence_schema_valid": True,
        "evidence_fresh": True,
        "message_count": 53,
        "raw_record_count": 53,
        "markdown_message_count": 53,
        "ledger_message_count": 53,
        "oldest_reached": True,
        "latest_reached": True,
        "capture_stable_after_rescan": True,
        "stable_scan_count": 2,
        "upper_watermark_reached": True,
        "unresolved_gap_count": 0,
        "duplicate_message_count": 0,
        "message_id_sets_equal": True,
        "ordered_message_digest_equal": True,
        "artifact_hashes_verified": True,
        "attachment_discovered_count": 1,
        "attachment_saved_count": 1,
        "attachment_manifest_count": 1,
        "attachment_inventory_complete": True,
        "attachment_id_sets_equal": True,
        "manifest_schema_valid": True,
        "pending_retry_count": 0,
        "external_actions": "disabled",
    }
    evidence.update(overrides)
    return evidence


def test_in_app_browser_policy_prefers_scoped_dom_scroll():
    policy = build_capture_route_policy("in_app_browser")

    assert policy["primary_read"] == "visible_message_dom"
    assert policy["scroll_order"][0] == "scoped_element_dom_scroll"
    assert policy["scroll_order"] == ["scoped_element_dom_scroll"]
    assert "human_assisted_scroll" in policy["approval_required_fallbacks"]
    assert policy["fallback_state"] == "paused_human_approval"
    assert policy["background_full_capture"] is True


def test_chrome_extension_policy_uses_same_completion_gate():
    policy = build_capture_route_policy("chrome_extension")

    assert policy["primary_read"] == "visible_message_dom"
    assert policy["completion_gate"] == "strict_full_capture_v1"
    assert "focused_page_keys" not in policy["scroll_order"]
    assert "focused_page_keys" in policy["approval_required_fallbacks"]


def test_rest_route_uses_pagination_instead_of_scroll():
    policy = build_capture_route_policy("rest_backfill")

    assert policy["primary_read"] == "discord_rest_api"
    assert policy["scroll_order"] == []
    assert policy["pagination"] == "until_history_exhausted"


def test_full_capture_requires_all_boundaries_and_artifact_counts():
    result = evaluate_full_capture(complete_evidence())

    assert result["status"] == "full"
    assert result["full_capture_confirmed"] is True
    assert result["blockers"] == []
    assert result["counts_consistent"] is True


def test_partial_when_visible_range_has_no_oldest_boundary():
    result = evaluate_full_capture(complete_evidence(oldest_reached=False))

    assert result["status"] == "partial"
    assert result["full_capture_confirmed"] is False
    assert "oldest_not_reached" in result["blockers"]


def test_partial_when_rescan_is_not_stable():
    result = evaluate_full_capture(complete_evidence(capture_stable_after_rescan=False))

    assert result["status"] == "partial"
    assert "rescan_not_stable" in result["blockers"]


def test_partial_when_markdown_or_ledger_has_a_gap():
    result = evaluate_full_capture(complete_evidence(markdown_message_count=52))

    assert result["status"] == "partial"
    assert result["counts_consistent"] is False
    assert "message_artifact_count_mismatch" in result["blockers"]


def test_partial_when_any_attachment_is_not_saved_or_manifested():
    result = evaluate_full_capture(complete_evidence(attachment_saved_count=0))

    assert result["status"] == "partial"
    assert "attachment_count_mismatch" in result["blockers"]


def test_empty_capture_is_blocked():
    result = evaluate_full_capture(complete_evidence(message_count=0, raw_record_count=0, markdown_message_count=0, ledger_message_count=0))

    assert result["status"] == "blocked"
    assert "no_messages_captured" in result["blockers"]


def test_unknown_route_cannot_claim_full():
    result = evaluate_full_capture(complete_evidence(route="unknown"))

    assert result["status"] == "blocked"
    assert "unsupported_capture_route" in result["blockers"]


def test_target_binding_and_freshness_are_required():
    result = evaluate_full_capture(complete_evidence(target_bound=False, evidence_fresh=False))

    assert result["status"] == "partial"
    assert "target_not_bound" in result["blockers"]
    assert "evidence_stale" in result["blockers"]


def test_equal_counts_with_different_message_sets_are_partial():
    result = evaluate_full_capture(complete_evidence(message_id_sets_equal=False))

    assert result["status"] == "partial"
    assert "message_id_set_mismatch" in result["blockers"]


def test_attachment_zero_requires_complete_inventory_proof():
    result = evaluate_full_capture(
        complete_evidence(
            attachment_discovered_count=0,
            attachment_saved_count=0,
            attachment_manifest_count=0,
            attachment_inventory_complete=False,
        )
    )

    assert result["status"] == "partial"
    assert "attachment_inventory_incomplete" in result["blockers"]


def test_pending_retry_prevents_full_closeout():
    result = evaluate_full_capture(complete_evidence(pending_retry_count=1))

    assert result["status"] == "partial"
    assert "pending_retry" in result["blockers"]


def test_capture_content_understanding_is_a_separate_state():
    result = evaluate_full_capture(complete_evidence(attachment_extracted_count=0))

    assert result["status"] == "full"
    assert result["next_action"] == "context_understanding"


def test_gate_output_is_metadata_only():
    evidence = complete_evidence()
    evidence["raw_text"] = "private discord body"
    evidence["url"] = "https://discord.com/channels/1/2/3"

    result = evaluate_full_capture(evidence)
    encoded = json.dumps(result, ensure_ascii=False)

    assert "private discord body" not in encoded
    assert "discord.com/channels" not in encoded
    assert result["raw_text_returned"] is False
    assert result["outbound_actions"] == "disabled"


def test_cli_full_capture_gate_reads_evidence_and_returns_full(tmp_path, capsys):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(complete_evidence()), encoding="utf-8")

    result = cli_main(["full-capture-gate", "--evidence", str(evidence_path), "--json"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 0
    assert payload["full_capture_confirmed"] is True
    assert str(evidence_path) not in output


def test_cli_full_capture_gate_fails_closed_for_partial(tmp_path, capsys):
    evidence_path = tmp_path / "partial.json"
    evidence_path.write_text(json.dumps(complete_evidence(latest_reached=False)), encoding="utf-8")

    result = cli_main(["full-capture-gate", "--evidence", str(evidence_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["status"] == "partial"
    assert "latest_not_reached" in payload["blockers"]
