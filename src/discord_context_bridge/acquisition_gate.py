from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _message_period(record: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    """Read message coverage boundaries, never snapshot observation timestamps."""
    period = record.get("message_period")
    if not isinstance(period, dict):
        period = {}
    start = _parse_time(period.get("start") or record.get("oldest_message_at"))
    end = _parse_time(period.get("end") or record.get("latest_message_at"))
    event_time = _parse_time(record.get("message_time"))
    return start or event_time, end or event_time


def _display_time(value: datetime | None) -> dict[str, str | None]:
    if value is None:
        return {"utc": None, "jst": None}
    return {"utc": value.isoformat(), "jst": value.astimezone(JST).isoformat()}


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _receipt_id(record: dict[str, Any]) -> str:
    return str(record.get("capture_id") or record.get("evidence_id") or "").strip()


def load_full_capture_receipt(path: Path | None, *, max_bytes: int = 5_000_000) -> tuple[dict[str, Any] | None, str]:
    if path is None:
        return None, "full_capture_receipt_missing"
    try:
        if path.stat().st_size > max_bytes:
            return None, "full_capture_receipt_too_large"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "full_capture_receipt_unreadable"
    if not isinstance(payload, dict):
        return None, "full_capture_receipt_invalid"
    return payload, ""


def _validate_full_capture_receipt(receipt: Mapping[str, Any] | None) -> tuple[str, int | None, list[str]]:
    blockers: list[str] = []
    if receipt is None:
        return "", None, ["full_capture_receipt_missing"]
    capture_id = str(receipt.get("capture_id") or "").strip()
    boundaries = receipt.get("boundaries") if isinstance(receipt.get("boundaries"), dict) else {}
    counts = receipt.get("counts") if isinstance(receipt.get("counts"), dict) else {}
    messages = _positive_int(counts.get("messages"))
    artifact_counts = [_positive_int(counts.get(key)) for key in ("raw_records", "markdown_messages", "ledger_messages")]
    checks = [
        (receipt.get("schema") != "discord_full_capture_completion_gate.v1", "full_capture_receipt_schema_invalid"),
        (receipt.get("status") != "full", "full_capture_receipt_not_full"),
        (receipt.get("full_capture_confirmed") is not True, "full_capture_receipt_not_confirmed"),
        (not capture_id, "full_capture_receipt_capture_id_missing"),
        (not all(boundaries.get(key) is True for key in ("oldest_reached", "latest_reached", "capture_stable_after_rescan")),
         "full_capture_receipt_boundaries_incomplete"),
        (receipt.get("counts_consistent") is not True, "full_capture_receipt_counts_inconsistent"),
        (messages is None or messages <= 0 or any(value != messages for value in artifact_counts),
         "full_capture_receipt_counts_invalid"),
        (_positive_int(receipt.get("unresolved_gap_count")) != 0, "full_capture_receipt_has_gaps"),
        (bool(receipt.get("blockers")), "full_capture_receipt_has_blockers"),
        (receipt.get("raw_text_returned") is not False, "full_capture_receipt_raw_output_invalid"),
        (receipt.get("participant_names_returned") is not False, "full_capture_receipt_participant_output_invalid"),
        (receipt.get("url_output") != "omitted", "full_capture_receipt_url_output_invalid"),
        (receipt.get("path_output") != "omitted", "full_capture_receipt_path_output_invalid"),
        (receipt.get("outbound_actions") != "disabled", "full_capture_receipt_outbound_invalid"),
    ]
    blockers.extend(reason for failed, reason in checks if failed)
    return capture_id, messages, blockers


def build_acquisition_completion_gate(
    records: Iterable[dict[str, Any]], *, requested_start: str = "", requested_end: str = "",
    freshness_status: str = "unknown", source_kind: str = "saved_log", user_confirmed: bool = False,
    full_capture_receipt: Mapping[str, Any] | None = None, receipt_load_error: str = "",
) -> dict[str, Any]:
    """Build a metadata-only, fail-closed context acquisition receipt."""
    rows = [row for row in records if isinstance(row, dict)]
    start_req, end_req = _parse_time(requested_start), _parse_time(requested_end)
    receipt_capture_id, receipt_message_count, receipt_blockers = _validate_full_capture_receipt(full_capture_receipt)
    if receipt_load_error and receipt_load_error not in receipt_blockers:
        receipt_blockers.append(receipt_load_error)
    record_capture_ids = sorted({_receipt_id(row) for row in rows if _receipt_id(row)})
    receipt_rows = [row for row in rows if _receipt_id(row) == receipt_capture_id] if receipt_capture_id else []
    periods = [_message_period(row) for row in receipt_rows]
    starts = [start for start, _ in periods if start is not None]
    ends = [end for _, end in periods if end is not None]
    acquired_start = min(starts) if starts else None
    acquired_end = max(ends) if ends else None
    hashes = [str(row.get("content_hash") or "").strip() for row in rows]
    unique_records = len(set(hashes)) if rows and all(hashes) else None
    message_count = receipt_message_count
    dedupe_count = receipt_message_count
    duplicate_count = 0 if not receipt_blockers else None
    valid_range = bool(start_req and end_req and start_req <= end_req)
    range_covered = bool(valid_range and acquired_start and acquired_end and acquired_start <= start_req and acquired_end >= end_req)
    gaps = [] if not receipt_blockers else None
    explicit_continuous = not receipt_blockers
    continuity = "continuous" if explicit_continuous else "unknown"
    explicit_full = not receipt_blockers and bool(receipt_rows)
    coverage_state = "full" if explicit_full and explicit_continuous and range_covered else "partial" if rows else "unknown"
    blockers: list[str] = []
    checks = [
        (not valid_range, "requested_range_missing_or_invalid"), (not rows, "target_records_missing"),
        (bool(receipt_capture_id) and not receipt_rows, "full_capture_receipt_capture_id_mismatch"),
        (message_count is None, "message_count_unverified"), (dedupe_count is None, "deduplicated_message_count_unverified"),
        (duplicate_count is None, "duplicate_message_count_unverified"),
        (not range_covered, "requested_range_not_covered"), (continuity != "continuous", "continuity_unverified_or_gapped"),
        (freshness_status != "recent", "cache_not_recent"), (coverage_state != "full", "full_capture_unverified"),
        (not user_confirmed, "user_confirmation_required"),
    ]
    blockers.extend(reason for failed, reason in checks if failed)
    blockers.extend(reason for reason in receipt_blockers if reason not in blockers)
    if not rows:
        refresh = "thread-capture-plan"
    elif freshness_status != "recent" or not range_covered or continuity != "continuous":
        refresh = "refresh_requested_range_then_full_rescan_if_boundary_unknown"
    elif not explicit_full:
        refresh = "full-capture-gate"
    elif not user_confirmed:
        refresh = "request_user_coverage_confirmation"
    else:
        refresh = "none"
    routes = sorted({str(r.get("source_route") or r.get("source") or source_kind) for r in rows}) or [source_kind]
    return {
        "schema": "discord_context_acquisition_completion_gate.v1",
        "requested_range": {"start": _display_time(start_req), "end": _display_time(end_req), "valid": valid_range},
        "acquired_range": {"start": _display_time(acquired_start), "end": _display_time(acquired_end), "covers_requested_range": range_covered},
        "counts": {"snapshot_record_count": len(rows), "deduplicated_snapshot_record_count": unique_records,
                   "message_count": message_count, "deduplicated_message_count": dedupe_count,
                   "duplicate_message_count": duplicate_count},
        "verified_receipt": {"id": receipt_capture_id or None, "verified": not receipt_blockers,
                             "record_capture_ids": record_capture_ids},
        "continuity": continuity, "gap_intervals": gaps, "cache_freshness": freshness_status,
        "source_routes": routes, "coverage_state": coverage_state, "summary_ready": not blockers,
        "recommended_refresh_route": refresh, "user_confirmation_required": not user_confirmed,
        "recommended_event_preparation_command": "coverage-report --require-summary-ready",
        "user_confirmed": user_confirmed, "blockers": blockers, "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
