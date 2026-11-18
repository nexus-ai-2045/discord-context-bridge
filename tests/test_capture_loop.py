from __future__ import annotations

from discord_context_bridge.capture.loop import (
    advance_capture_loop,
    build_capture_status_projection,
    derive_operational_tags,
    new_capture_loop,
    validate_observed_full_receipt,
)


def _to_gate_evaluating(run: dict) -> dict:
    events = (
        "visible_snapshot_saved",
        "route_ready",
        "oldest_reached",
        "latest_reached",
        "attachment_inventory_complete",
        "attachments_saved",
        "reconciled",
        "stable_rescan_complete",
    )
    for event in events:
        run = advance_capture_loop(run, event)
    return run


def test_gate_partial_loop_stops_at_scan_pass_budget() -> None:
    run = new_capture_loop(
        "discord:thread",
        "chrome_extension",
        "m9",
        scan_pass_budget=2,
    )
    run = _to_gate_evaluating(run)

    run = advance_capture_loop(run, "gate_partial")
    assert run["state"] == "stable_rescan"
    assert run["scan_pass"]["used"] == 1

    run = advance_capture_loop(run, "stable_rescan_complete")
    run = advance_capture_loop(run, "gate_partial")
    assert run["state"] == "blocked_closed"
    assert run["blocker"] == "scan_pass_budget_exhausted"
    assert run["scan_pass"] == {"budget": 2, "used": 2}
    assert run["checkpoints"][-1]["event"] == "gate_partial"


def test_scan_pass_budget_must_be_positive() -> None:
    try:
        new_capture_loop("discord:thread", "chrome_extension", "m9", scan_pass_budget=0)
    except ValueError as exc:
        assert "scan_pass_budget" in str(exc)
    else:
        raise AssertionError("zero scan pass budget must fail")


def test_operational_tags_are_derived_from_safe_allowlist_only() -> None:
    tags = derive_operational_tags(
        {
            "scope": "server_threads",
            "refresh_check": True,
            "route": "chrome_extension",
            "observed_full": True,
            "guild_id": "should-not-leak",
            "custom_tag": "private-name",
        }
    )

    assert tags == [
        "chrome-visible",
        "observed-full",
        "refresh-check",
        "server-threads-all",
    ]
    assert all("private" not in tag and "guild" not in tag for tag in tags)


def test_fde_and_lcs_status_are_projected_independently() -> None:
    run = new_capture_loop(
        "discord:thread",
        "chrome_extension",
        "m9",
        scan_pass_budget=2,
    )
    projection = build_capture_status_projection(run)

    assert projection["fde"]["status"] == "in_progress"
    assert projection["fde"]["decision"] == "continue_capture"
    assert projection["lcs"]["status"] == "capture_pending"
    assert projection["lcs"]["event_emitted"] is False

    run["state"] = "full_closed"
    run["lanes"]["background_full"]["status"] = "full"
    projection = build_capture_status_projection(run)
    assert projection["fde"]["status"] == "full"
    assert projection["lcs"]["status"] == "passport_pending"
    assert projection["lcs"]["event_emitted"] is False


def test_observed_full_receipt_can_never_validate_as_api_full() -> None:
    valid = validate_observed_full_receipt(
        {
            "schema": "discord_observed_full_closeout.v1",
            "state": "observed_full_verified",
            "observed_full": {"verified": True},
            "api_full": {"verified": False, "status": "unavailable"},
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }
    )
    assert valid["valid"] is True
    assert valid["observed_full_verified"] is True
    assert valid["api_full_verified"] is False

    invalid = validate_observed_full_receipt(
        {
            "schema": "discord_observed_full_closeout.v1",
            "state": "api_full_verified",
            "observed_full": {"verified": True},
            "api_full": {"verified": True},
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }
    )
    assert invalid["valid"] is False
    assert "api_full_must_remain_false" in invalid["blockers"]
    assert "observed_receipt_claims_api_full" in invalid["blockers"]


def test_observed_full_receipt_requires_metadata_only_safety_boundary() -> None:
    result = validate_observed_full_receipt(
        {
            "schema": "discord_observed_full_closeout.v1",
            "state": "partial_or_blocked",
            "observed_full": {"verified": False},
            "api_full": {"verified": False},
            "raw_text_returned": True,
            "outbound_actions": "enabled",
        }
    )
    assert result["valid"] is False
    assert "raw_text_must_not_be_returned" in result["blockers"]
    assert "outbound_actions_must_be_disabled" in result["blockers"]
    assert "observed_full_not_verified" in result["blockers"]
