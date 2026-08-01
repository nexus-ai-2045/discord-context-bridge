from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
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


def build_acquisition_completion_gate(
    records: Iterable[dict[str, Any]], *, requested_start: str = "", requested_end: str = "",
    freshness_status: str = "unknown", source_kind: str = "saved_log", user_confirmed: bool = False,
) -> dict[str, Any]:
    """Build a metadata-only, fail-closed context acquisition receipt."""
    rows = [row for row in records if isinstance(row, dict)]
    start_req, end_req = _parse_time(requested_start), _parse_time(requested_end)
    periods = [_message_period(row) for row in rows]
    starts = [start for start, _ in periods if start is not None]
    ends = [end for _, end in periods if end is not None]
    acquired_start = min(starts) if starts else None
    acquired_end = max(ends) if ends else None
    hashes = [str(row.get("content_hash") or "").strip() for row in rows]
    unique_records = len(set(hashes)) if rows and all(hashes) else None
    message_values = [value for r in rows if (value := _positive_int(r.get("message_count"))) is not None]
    dedupe_values = [value for r in rows if (value := _positive_int(r.get("deduplicated_message_count"))) is not None]
    message_count = max(message_values, default=None)
    dedupe_count = max(dedupe_values, default=None)
    valid_range = bool(start_req and end_req and start_req <= end_req)
    range_covered = bool(valid_range and acquired_start and acquired_end and acquired_start <= start_req and acquired_end >= end_req)
    gaps = next((r.get("gaps") for r in reversed(rows) if isinstance(r.get("gaps"), list)), None)
    explicit_continuous = any(r.get("continuity") == "continuous" and r.get("gaps") == [] for r in rows)
    continuity = "continuous" if explicit_continuous else "gaps_detected" if gaps else "unknown"
    explicit_full = any(r.get("full_capture_confirmed") is True for r in rows)
    coverage_state = "full" if explicit_full and explicit_continuous and range_covered else "partial" if rows else "unknown"
    blockers: list[str] = []
    checks = [
        (not valid_range, "requested_range_missing_or_invalid"), (not rows, "target_records_missing"),
        (message_count is None, "message_count_unverified"), (dedupe_count is None, "deduplicated_message_count_unverified"),
        (not range_covered, "requested_range_not_covered"), (continuity != "continuous", "continuity_unverified_or_gapped"),
        (freshness_status != "recent", "cache_not_recent"), (coverage_state != "full", "full_capture_unverified"),
        (not user_confirmed, "user_confirmation_required"),
    ]
    blockers.extend(reason for failed, reason in checks if failed)
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
                   "message_count": message_count, "deduplicated_message_count": dedupe_count},
        "continuity": continuity, "gap_intervals": gaps, "cache_freshness": freshness_status,
        "source_routes": routes, "coverage_state": coverage_state, "summary_ready": not blockers,
        "recommended_refresh_route": refresh, "user_confirmation_required": not user_confirmed,
        "user_confirmed": user_confirmed, "blockers": blockers, "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
