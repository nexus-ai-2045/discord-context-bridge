import json

import pytest

from discord_context_bridge.acquisition_gate import build_acquisition_completion_gate
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.core import target_key_for_url


def full_receipt(capture_id="capture-1", **overrides):
    receipt = {
        "schema": "discord_full_capture_completion_gate.v1", "status": "full",
        "full_capture_confirmed": True, "capture_id": capture_id,
        "boundaries": {"oldest_reached": True, "latest_reached": True, "capture_stable_after_rescan": True},
        "counts": {"messages": 12, "raw_records": 12, "markdown_messages": 12, "ledger_messages": 12,
                   "attachments_discovered": 0, "attachments_saved": 0, "attachments_manifested": 0},
        "counts_consistent": True, "attachments_consistent": True, "unresolved_gap_count": 0,
        "blockers": [], "raw_text_returned": False, "participant_names_returned": False,
        "url_output": "omitted", "path_output": "omitted", "outbound_actions": "disabled",
    }
    receipt.update(overrides)
    return receipt


def matching_record(capture_id="capture-1"):
    return {"capture_id": capture_id, "captured_at": "2026-08-01T07:01:00+00:00",
            "message_period": {"start": "2026-08-01T05:00:00+00:00", "end": "2026-08-01T07:00:00+00:00"},
            "content_hash": "hash", "source_route": "rest_backfill", "text": "private"}


def test_exact_snapshot_is_not_current_context_proof():
    gate = build_acquisition_completion_gate(
        [{"captured_at": "2026-08-01T06:00:00+00:00", "content_hash": "abc"}],
        requested_start="2026-08-01T05:00:00+00:00", requested_end="2026-08-01T07:00:00+00:00",
        freshness_status="recent")
    assert gate["summary_ready"] is False
    assert gate["coverage_state"] == "partial"
    assert gate["counts"]["message_count"] is None
    assert "full_capture_receipt_missing" in gate["blockers"]


def test_canonical_full_receipt_and_matching_capture_open_gate():
    gate = build_acquisition_completion_gate(
        [matching_record()], requested_start="2026-08-01T05:00:00+00:00",
        requested_end="2026-08-01T07:00:00+00:00", freshness_status="recent",
        user_confirmed=True, full_capture_receipt=full_receipt())
    assert gate["summary_ready"] is True
    assert gate["coverage_state"] == "full"
    assert gate["counts"]["message_count"] == 12
    assert gate["verified_receipt"] == {
        "id": "capture-1", "verified": True, "record_capture_ids": ["capture-1"]}


def test_snapshot_self_reported_verified_boolean_is_ignored():
    record = {**matching_record(), "receipt_verified": True, "evidence_verified": True,
              "full_capture_confirmed": True}
    gate = build_acquisition_completion_gate(
        [record], requested_start="2026-08-01T05:00:00+00:00",
        requested_end="2026-08-01T07:00:00+00:00", freshness_status="recent", user_confirmed=True)
    assert gate["summary_ready"] is False
    assert gate["verified_receipt"]["verified"] is False


def test_capture_id_mismatch_fails_closed():
    gate = build_acquisition_completion_gate(
        [matching_record("snapshot-capture")], requested_start="2026-08-01T05:00:00+00:00",
        requested_end="2026-08-01T07:00:00+00:00", freshness_status="recent", user_confirmed=True,
        full_capture_receipt=full_receipt("receipt-capture"))
    assert gate["summary_ready"] is False
    assert "full_capture_receipt_capture_id_mismatch" in gate["blockers"]
    assert gate["acquired_range"]["start"]["utc"] is None


def test_partial_canonical_receipt_fails_closed():
    receipt = full_receipt(status="partial", full_capture_confirmed=False, blockers=["oldest_not_reached"])
    gate = build_acquisition_completion_gate(
        [matching_record()], requested_start="2026-08-01T05:00:00+00:00",
        requested_end="2026-08-01T07:00:00+00:00", freshness_status="recent", user_confirmed=True,
        full_capture_receipt=receipt)
    assert gate["summary_ready"] is False
    assert "full_capture_receipt_not_full" in gate["blockers"]


def test_snapshot_capture_time_never_becomes_message_period():
    record = {"capture_id": "capture-1", "captured_at": "2026-08-01T07:00:00+00:00",
              "observed_at": "2026-08-01T07:00:00+00:00", "time": "2026-08-01T07:00:00+00:00"}
    gate = build_acquisition_completion_gate(
        [record], requested_start="2026-08-01T06:00:00+00:00",
        requested_end="2026-08-01T07:00:00+00:00", freshness_status="recent", user_confirmed=True,
        full_capture_receipt=full_receipt())
    assert gate["acquired_range"]["start"]["utc"] is None
    assert gate["coverage_state"] == "partial"
    assert gate["summary_ready"] is False


def _write_cli_artifacts(tmp_path, *, receipt=None, record_capture_id="capture-1"):
    url = "https://discord.com/channels/1/10/20"
    store = tmp_path / "snapshots.ndjson"
    record = {**matching_record(record_capture_id), "url": url, "target_key": target_key_for_url(url)}
    store.write_text(json.dumps(record) + "\n", encoding="utf-8")
    receipt_path = None
    if receipt is not None:
        receipt_path = tmp_path / "full-capture-receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return url, store, receipt_path


def _strict_cli_args(url, store, receipt_path=None):
    args = ["coverage-report", "--url", url, "--ai-log", str(store),
            "--requested-start", "2026-08-01T05:00:00+00:00",
            "--requested-end", "2026-08-01T07:00:00+00:00", "--user-confirmed",
            "--require-summary-ready"]
    if receipt_path:
        args.extend(["--full-capture-receipt", str(receipt_path)])
    return args


def test_cli_strict_full_receipt_matching_capture_exits_zero(tmp_path, capsys):
    url, store, receipt_path = _write_cli_artifacts(tmp_path, receipt=full_receipt())
    result = cli_main(_strict_cli_args(url, store, receipt_path))
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["acquisition_completion_gate"]["summary_ready"] is True


def test_cli_strict_mismatch_partial_and_missing_receipt_exit_two(tmp_path, capsys):
    cases = [
        (full_receipt("receipt-capture"), "snapshot-capture"),
        (full_receipt(status="partial", full_capture_confirmed=False, blockers=["incomplete"]), "capture-1"),
        (None, "capture-1"),
    ]
    for index, (receipt, record_id) in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        url, store, receipt_path = _write_cli_artifacts(case_dir, receipt=receipt, record_capture_id=record_id)
        assert cli_main(_strict_cli_args(url, store, receipt_path)) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["acquisition_completion_gate"]["summary_ready"] is False


@pytest.mark.parametrize(
    ("override", "blocker"),
    [
        ({"participant_names_returned": True}, "full_capture_receipt_participant_output_invalid"),
        ({"url_output": "included"}, "full_capture_receipt_url_output_invalid"),
        ({"path_output": "included"}, "full_capture_receipt_path_output_invalid"),
    ],
)
def test_cli_strict_rejects_receipt_metadata_output_violations(tmp_path, capsys, override, blocker):
    url, store, receipt_path = _write_cli_artifacts(tmp_path, receipt=full_receipt(**override))
    result = cli_main(_strict_cli_args(url, store, receipt_path))
    payload = json.loads(capsys.readouterr().out)
    gate = payload["acquisition_completion_gate"]
    assert result == 2
    assert gate["summary_ready"] is False
    assert blocker in gate["blockers"]


def test_coverage_cli_preserves_legacy_exact_match_exit(tmp_path, capsys):
    url, store, _ = _write_cli_artifacts(tmp_path)
    result = cli_main(["coverage-report", "--url", url, "--ai-log", str(store)])
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["coverage"]["exact_coverage"] is True
    assert payload["acquisition_completion_gate"]["summary_ready"] is False
