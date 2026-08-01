import json

from discord_context_bridge.acquisition_gate import build_acquisition_completion_gate
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.core import target_key_for_url


def test_exact_snapshot_is_not_current_context_proof():
    gate = build_acquisition_completion_gate(
        [{"captured_at": "2026-08-01T06:00:00+00:00", "content_hash": "abc"}],
        requested_start="2026-08-01T05:00:00+00:00", requested_end="2026-08-01T07:00:00+00:00",
        freshness_status="recent",
    )
    assert gate["summary_ready"] is False
    assert gate["coverage_state"] == "partial"
    assert gate["continuity"] == "unknown"
    assert gate["counts"]["message_count"] is None
    assert gate["acquired_range"]["start"]["jst"] is None


def test_gate_opens_only_with_all_evidence_and_confirmation():
    gate = build_acquisition_completion_gate([
        {"message_time": "2026-08-01T05:00:00+00:00", "captured_at": "2026-08-01T07:01:00+00:00", "content_hash": "a", "message_count": 12,
         "deduplicated_message_count": 10, "full_capture_confirmed": True, "continuity": "continuous",
         "gaps": [], "source_route": "rest_backfill"},
        {"message_time": "2026-08-01T07:00:00+00:00", "captured_at": "2026-08-01T07:01:00+00:00", "content_hash": "b", "message_count": 12,
         "deduplicated_message_count": 10},
    ], requested_start="2026-08-01T05:00:00+00:00", requested_end="2026-08-01T07:00:00+00:00",
       freshness_status="recent", user_confirmed=True)
    assert gate["summary_ready"] is True
    assert gate["coverage_state"] == "full"
    assert gate["recommended_refresh_route"] == "none"
    assert gate["blockers"] == []


def test_invalid_or_naive_requested_times_fail_closed():
    gate = build_acquisition_completion_gate([], requested_start="2026-08-01T10:00:00", requested_end="invalid", freshness_status="recent")
    assert gate["summary_ready"] is False
    assert gate["requested_range"]["valid"] is False
    assert gate["coverage_state"] == "unknown"
    assert gate["recommended_refresh_route"] == "thread-capture-plan"


def test_snapshot_capture_times_never_become_message_coverage_boundaries():
    gate = build_acquisition_completion_gate([{
        "captured_at": "2026-08-01T07:00:00+00:00", "observed_at": "2026-08-01T07:00:00+00:00",
        "time": "2026-08-01T07:00:00+00:00", "content_hash": "snapshot", "message_count": 8,
        "deduplicated_message_count": 8, "full_capture_confirmed": True, "continuity": "continuous", "gaps": [],
    }], requested_start="2026-08-01T06:00:00+00:00", requested_end="2026-08-01T07:00:00+00:00",
       freshness_status="recent", user_confirmed=True)

    assert gate["acquired_range"]["start"]["utc"] is None
    assert gate["acquired_range"]["end"]["utc"] is None
    assert gate["acquired_range"]["covers_requested_range"] is False
    assert gate["coverage_state"] == "partial"
    assert gate["summary_ready"] is False


def test_structured_message_period_can_prove_requested_boundaries():
    gate = build_acquisition_completion_gate([{
        "captured_at": "2026-08-01T07:01:00+00:00", "message_period": {
            "start": "2026-08-01T06:00:00+00:00", "end": "2026-08-01T07:00:00+00:00"},
        "content_hash": "range", "message_count": 8, "deduplicated_message_count": 8,
        "full_capture_confirmed": True, "continuity": "continuous", "gaps": [],
    }], requested_start="2026-08-01T06:00:00+00:00", requested_end="2026-08-01T07:00:00+00:00",
       freshness_status="recent", user_confirmed=True)

    assert gate["acquired_range"]["covers_requested_range"] is True
    assert gate["summary_ready"] is True


def test_coverage_cli_exact_snapshot_still_exits_fail_closed(tmp_path, capsys):
    url = "https://discord.com/channels/1/10/20"
    store = tmp_path / "snapshots.ndjson"
    store.write_text(json.dumps({
        "url": url, "target_key": target_key_for_url(url), "captured_at": "2026-08-01T06:00:00+00:00",
        "content_hash": "abc", "text": "private body",
    }) + "\n", encoding="utf-8")

    result = cli_main(["coverage-report", "--url", url, "--ai-log", str(store),
                       "--requested-start", "2026-08-01T05:00:00+00:00",
                       "--requested-end", "2026-08-01T07:00:00+00:00"])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert result == 2
    assert payload["coverage"]["exact_coverage"] is True
    assert payload["acquisition_completion_gate"]["summary_ready"] is False
    assert "private body" not in output
    assert url not in output
