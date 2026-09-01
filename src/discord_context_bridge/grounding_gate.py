from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


GATE_SCHEMA = "discord_context_grounding_gate.v1"
ACQUISITION_SCHEMA = "discord_context_acquisition_completion_gate.v1"
EVIDENCE_SCHEMA = "discord_context_grounding_evidence.v1"
INTENTS = {"draft", "channel_recommendation"}
CLAIM_ROLE_POLICY = {
    "thread_owner": {"user_observed"},
    "media_reaction": {"user_observed", "media_verified"},
    "personal_impression": {"user_observed", "media_verified"},
}
EVIDENCE_ROLES = {"user_observed", "media_verified"}
RANKING_BASES = {
    "keyword", "topic", "temperature", "heuristic",
    "semantic_anchor", "channel_purpose", "thread_purpose", "message_period",
}
HEURISTIC_BASES = {"keyword", "topic", "temperature", "heuristic"}
PURPOSE_BASES = {"channel_purpose", "thread_purpose"}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_aware_time(value: Any) -> datetime | None:
    if not _text(value):
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
    return bool(start is not None and end is not None and start <= end and period.get("source_role") == "message_event")


def _result(intent: str, blockers: list[str]) -> dict[str, Any]:
    reasons = list(dict.fromkeys(blockers))
    return {
        "schema": GATE_SCHEMA, "ready": not reasons, "intent": intent, "reason_codes": reasons,
        "evidence_policy": {"captured_at_role": "freshness_only",
                            "claim_role_policy": {key: sorted(value) for key, value in CLAIM_ROLE_POLICY.items()}},
        "raw_text_returned": False, "outbound_actions": "disabled",
    }


def _invalid_result(intent: Any = None) -> dict[str, Any]:
    return _result(intent if isinstance(intent, str) else "unknown", ["invalid_contract"])


def build_context_grounding_gate(payload: Any) -> dict[str, Any]:
    """Fail closed unless claims and recommendations bind to typed evidence receipts."""
    if (not isinstance(payload, Mapping) or not isinstance(payload.get("intent"), str)
            or payload.get("intent") not in INTENTS):
        return _invalid_result(payload.get("intent") if isinstance(payload, Mapping) else None)
    intent = payload["intent"]
    target_key = payload.get("target_key")
    if not _text(target_key):
        return _invalid_result(intent)

    receipt_values = payload.get("evidence_receipts")
    if not isinstance(receipt_values, list) or any(not isinstance(item, Mapping) for item in receipt_values):
        return _invalid_result(intent)
    receipts: dict[str, Mapping[str, Any]] = {}
    for receipt in receipt_values:
        receipt_id = receipt.get("receipt_id")
        if (not _text(receipt_id) or receipt_id in receipts or receipt.get("schema") != EVIDENCE_SCHEMA
                or receipt.get("source_role") not in EVIDENCE_ROLES or receipt.get("target_key") != target_key):
            return _invalid_result(intent)
        receipts[receipt_id] = receipt

    claims = payload.get("claims")
    if not isinstance(claims, list) or any(not isinstance(item, Mapping) for item in claims):
        return _invalid_result(intent)
    if intent == "draft" and not claims:
        return _invalid_result(intent)
    blockers: list[str] = []
    for claim in claims:
        claim_type, evidence_ref = claim.get("claim_type"), claim.get("evidence_ref")
        if not isinstance(claim_type, str) or claim_type not in CLAIM_ROLE_POLICY or not _text(evidence_ref):
            blockers.append("invalid_contract")
            continue
        receipt = receipts.get(evidence_ref)
        if receipt is None or receipt.get("source_role") not in CLAIM_ROLE_POLICY[claim_type]:
            blockers.append("claim_source_role_mismatch")
        elif "source_role" in claim and (
                claim.get("source_role") not in EVIDENCE_ROLES
                or claim.get("source_role") != receipt.get("source_role")):
            blockers.append("claim_source_role_mismatch")

    if intent == "channel_recommendation":
        acquisition = payload.get("acquisition_completion_gate")
        if not isinstance(acquisition, Mapping):
            blockers.append("invalid_contract")
        elif (acquisition.get("schema") != ACQUISITION_SCHEMA or not _text(acquisition.get("receipt_id"))
              or acquisition.get("target_key") != target_key):
            blockers.append("invalid_contract")
        elif not isinstance(acquisition.get("summary_ready"), bool):
            blockers.append("invalid_contract")
        elif not acquisition["summary_ready"]:
            blockers.append("partial_context")

        candidates = payload.get("channel_candidates")
        if (not isinstance(candidates, list) or len(candidates) < 2
                or any(not isinstance(item, Mapping) for item in candidates)):
            blockers.append("invalid_contract")
            candidates = []
        ids: set[str] = set()
        for candidate in candidates:
            candidate_id, bases = candidate.get("candidate_id"), candidate.get("ranking_basis")
            anchors = candidate.get("semantic_anchors")
            purpose = candidate.get("channel_purpose") or candidate.get("thread_purpose")
            bases_valid = (isinstance(bases, list) and bool(bases)
                           and all(isinstance(base, str) and base in RANKING_BASES for base in bases))
            if (not _text(candidate_id) or candidate_id in ids or candidate.get("target_key") != target_key
                    or not bases_valid
                    or not isinstance(anchors, list) or not anchors or any(not _text(anchor) for anchor in anchors)
                    or not _text(purpose)):
                blockers.append("invalid_contract")
            if _text(candidate_id):
                ids.add(candidate_id)
            if candidate.get("message_period_source") == "captured_at" or (
                    not _actual_message_period(candidate) and bool(candidate.get("captured_at"))):
                blockers.append("capture_time_used_as_message_time")
            if bases_valid and set(bases) <= HEURISTIC_BASES:
                blockers.append("heuristic_only_ranking")
            if not (bases_valid and "semantic_anchor" in bases and bool(set(bases) & PURPOSE_BASES)
                    and _actual_message_period(candidate) and isinstance(anchors, list) and anchors
                    and all(_text(anchor) for anchor in anchors) and _text(purpose)):
                blockers.append("semantic_anchor_missing")
    return _result(intent, blockers)
