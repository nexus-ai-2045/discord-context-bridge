from __future__ import annotations

from datetime import UTC, datetime

from discord_context_bridge.claims import evaluate_context_claims

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def full_closeout() -> dict[str, object]:
    return {
        "schema": "dcb.parallel-run-operational-closeout.v1",
        "status": "full",
        "terminal_state": "full_closed",
        "full_capture_confirmed": True,
        "persistence_confirmed": True,
        "blockers": [],
        "evidence_observed_at": "2026-09-01T11:00:00Z",
        "outbound_actions": "disabled",
    }


def test_each_claim_stage_is_independent_and_composed() -> None:
    report = evaluate_context_claims(
        full_closeout(),
        understanding_confirmed=True,
        now=NOW,
    )
    assert report["claims"]["complete"]["allowed"] is True
    assert report["claims"]["complete"]["state"] == "allowed"
    assert report["claims"]["current"]["allowed"] is True
    assert report["claims"]["understood"]["allowed"] is True
    assert report["stages"]["capture"]["allowed"] is True
    assert report["stages"]["freshness"]["allowed"] is True
    assert report["stages"]["understanding"]["allowed"] is True


def test_projection_or_partial_closeout_cannot_authorize_any_claim() -> None:
    projection = {
        "schema": "chronica.projection.v1",
        "status": "full",
        "full_capture_confirmed": True,
        "persistence_confirmed": True,
        "terminal_state": "full_closed",
        "blockers": [],
        "evidence_observed_at": "2026-09-01T11:00:00Z",
        "outbound_actions": "disabled",
    }
    report = evaluate_context_claims(
        projection,
        understanding_confirmed=True,
        now=NOW,
    )
    assert all(item["allowed"] is False for item in report["claims"].values())
    assert all(item["state"] == "unknown" for item in report["claims"].values())
    assert "canonical_closeout_required" in report["claims"]["complete"]["blockers"]


def test_stale_capture_can_be_complete_but_never_current_or_understood() -> None:
    evidence = full_closeout()
    evidence["evidence_observed_at"] = "2026-08-01T00:00:00Z"
    report = evaluate_context_claims(
        evidence,
        understanding_confirmed=True,
        now=NOW,
    )
    assert report["claims"]["complete"]["allowed"] is True
    assert report["claims"]["current"]["allowed"] is False
    assert report["claims"]["current"]["state"] == "blocked"
    assert report["claims"]["understood"]["allowed"] is False
    assert "freshness_expired" in report["claims"]["current"]["blockers"]


def test_understanding_requires_explicit_confirmation() -> None:
    report = evaluate_context_claims(
        full_closeout(),
        now=NOW,
    )
    assert report["claims"]["complete"]["allowed"] is True
    assert report["claims"]["current"]["allowed"] is True
    assert report["claims"]["understood"]["allowed"] is False
    assert report["claims"]["understood"]["blockers"] == ["understanding_not_confirmed"]


def test_missing_persistence_blocks_all_claims() -> None:
    evidence = full_closeout()
    evidence["persistence_confirmed"] = False
    report = evaluate_context_claims(
        evidence,
        understanding_confirmed=True,
        now=NOW,
    )
    assert all(item["allowed"] is False for item in report["claims"].values())
    assert "persistence_not_confirmed" in report["claims"]["complete"]["blockers"]
