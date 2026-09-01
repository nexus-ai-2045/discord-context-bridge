from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


ALLOWED_ASSERTION_SOURCE_ROLES = {"user_observed", "media_verified"}
HEURISTIC_BASIS = {"keyword", "topic", "temperature", "heuristic"}


def _parse_aware_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _actual_message_period(candidate: Mapping[str, Any]) -> bool:
    period = candidate.get("message_period")
    if not isinstance(period, Mapping):
        return False
    start = _parse_aware_time(period.get("start"))
    end = _parse_aware_time(period.get("end"))
    return (
        start is not None
        and end is not None
        and start <= end
        and period.get("source_role") in {"message_event", "media_verified"}
    )


def build_context_grounding_gate(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate evidence roles before channel recommendations or drafted claims.

    This gate deliberately consumes the acquisition gate as an upstream receipt.
    It never infers message time from ``captured_at`` and never reads message text.
    """

    intent = str(payload.get("intent") or "draft")
    acquisition = payload.get("acquisition_completion_gate")
    acquisition = acquisition if isinstance(acquisition, Mapping) else {}
    candidates = payload.get("channel_candidates")
    candidates = [item for item in candidates if isinstance(item, Mapping)] if isinstance(candidates, list) else []
    claims = payload.get("claims")
    claims = [item for item in claims if isinstance(item, Mapping)] if isinstance(claims, list) else []
    blockers: list[str] = []

    if intent == "channel_recommendation":
        if acquisition.get("summary_ready") is not True:
            blockers.append("partial_context")

        if any(
            candidate.get("message_period_source") == "captured_at"
            or (
                not _actual_message_period(candidate)
                and bool(candidate.get("captured_at"))
            )
            for candidate in candidates
        ):
            blockers.append("capture_time_used_as_message_time")

        ranking_basis = {
            str(value)
            for candidate in candidates
            for value in (
                candidate.get("ranking_basis")
                if isinstance(candidate.get("ranking_basis"), list)
                else []
            )
        }
        if not ranking_basis or ranking_basis <= HEURISTIC_BASIS:
            blockers.append("heuristic_only_ranking")

        semantic_ready = bool(candidates) and all(
            _actual_message_period(candidate)
            and bool(candidate.get("semantic_anchors"))
            and bool(candidate.get("channel_purpose") or candidate.get("thread_purpose"))
            for candidate in candidates
        )
        if not semantic_ready:
            blockers.append("semantic_anchor_missing")

    if any(
        claim.get("claim_type") in {"media_reaction", "personal_impression"}
        and claim.get("source_role") not in ALLOWED_ASSERTION_SOURCE_ROLES
        for claim in claims
    ):
        blockers.append("claim_source_role_mismatch")

    blockers = list(dict.fromkeys(blockers))
    return {
        "schema": "discord_context_grounding_gate.v1",
        "ready": not blockers,
        "intent": intent,
        "reason_codes": blockers,
        "evidence_policy": {
            "captured_at_role": "freshness_only",
            "allowed_assertion_source_roles": sorted(ALLOWED_ASSERTION_SOURCE_ROLES),
        },
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
