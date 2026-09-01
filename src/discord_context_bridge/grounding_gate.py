from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .site_adapter_runtime import MAX_INPUT_BYTES


GATE_SCHEMA = "discord_context_grounding_gate.v1"
ACQUISITION_SCHEMA = "discord_context_acquisition_completion_gate.v1"
EVIDENCE_SCHEMA = "discord_context_grounding_evidence.v1"
INTENTS = {"draft", "channel_recommendation"}
CLAIM_ROLE_POLICY = {"thread_owner": {"user_observed"},
                     "media_reaction": {"user_observed", "media_verified"},
                     "personal_impression": {"user_observed", "media_verified"}}
ISSUER_ROLE_POLICY = {"dcb_user_observation": "user_observed",
                      "dcb_media_verification": "media_verified"}
ACQUISITION_ISSUER = "dcb_context_acquisition"
RANKING_BASES = {"keyword", "topic", "temperature", "heuristic", "semantic_anchor",
                 "channel_purpose", "thread_purpose", "message_period"}
HEURISTIC_BASES = {"keyword", "topic", "temperature", "heuristic"}
PURPOSE_BASES = {"channel_purpose", "thread_purpose"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _parse_time(value: Any) -> datetime | None:
    if not _text(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _period(value: Any, *, require_role: bool = False) -> tuple[datetime, datetime] | None:
    if not isinstance(value, Mapping) or (require_role and value.get("source_role") != "message_event"):
        return None
    start, end = _parse_time(value.get("start")), _parse_time(value.get("end"))
    return (start, end) if start is not None and end is not None and start <= end else None


def _artifact_digest(path_value: Any, root: Path | None) -> str | None:
    if not _text(path_value) or root is None:
        return None
    try:
        root_resolved = root.resolve(strict=True)
        candidate = (root_resolved / path_value).resolve(strict=True)
        if not candidate.is_relative_to(root_resolved) or not candidate.is_file():
            return None
        if candidate.stat().st_size > MAX_INPUT_BYTES:
            return None
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, RuntimeError, ValueError):
        return None


def _result(intent: Any, blockers: list[str]) -> dict[str, Any]:
    reasons = list(dict.fromkeys(blockers))
    return {"schema": GATE_SCHEMA, "ready": not reasons,
            "intent": intent if isinstance(intent, str) else "unknown", "reason_codes": reasons,
            "evidence_policy": {"captured_at_role": "freshness_only", "receipts": "separate_trusted_input"},
            "raw_text_returned": False, "path_output": "omitted", "outbound_actions": "disabled"}


def build_context_grounding_gate(
    contract: Any, *, trusted_evidence_receipts: Any = None,
    trusted_acquisition_receipt: Any = None, trusted_evidence_root: Path | None = None,
    trusted_acquisition_root: Path | None = None,
) -> dict[str, Any]:
    """Bind an untrusted claim contract to separately loaded and read-back receipts."""
    if (not isinstance(contract, Mapping) or not isinstance(contract.get("intent"), str)
            or contract.get("intent") not in INTENTS
            or "evidence_receipts" in contract or "acquisition_completion_gate" in contract):
        return _result(contract.get("intent") if isinstance(contract, Mapping) else None, ["invalid_contract"])
    intent, target_key = contract["intent"], contract.get("target_key")
    if not _text(target_key) or not isinstance(trusted_evidence_receipts, Sequence) \
            or isinstance(trusted_evidence_receipts, (str, bytes)):
        return _result(intent, ["invalid_contract"])

    receipts: dict[str, Mapping[str, Any]] = {}
    for receipt in trusted_evidence_receipts:
        if not isinstance(receipt, Mapping):
            return _result(intent, ["invalid_contract"])
        receipt_id, issuer, role = receipt.get("receipt_id"), receipt.get("issuer"), receipt.get("source_role")
        digest = _artifact_digest(receipt.get("artifact_path"), trusted_evidence_root)
        if (receipt.get("schema") != EVIDENCE_SCHEMA or not _text(receipt_id) or receipt_id in receipts
                or not _text(receipt.get("artifact_id")) or not _sha256(receipt.get("artifact_sha256"))
                or ISSUER_ROLE_POLICY.get(issuer) != role or receipt.get("target_key") != target_key
                or digest is None or digest != receipt.get("artifact_sha256")):
            return _result(intent, ["invalid_contract"])
        receipts[receipt_id] = receipt

    claims = contract.get("claims")
    if not isinstance(claims, list) or any(not isinstance(item, Mapping) for item in claims) \
            or (intent == "draft" and not claims):
        return _result(intent, ["invalid_contract"])
    blockers: list[str] = []
    for claim in claims:
        claim_type, evidence_ref = claim.get("claim_type"), claim.get("evidence_ref")
        receipt = receipts.get(evidence_ref) if _text(evidence_ref) else None
        if (not isinstance(claim_type, str) or claim_type not in CLAIM_ROLE_POLICY
                or not _sha256(claim.get("expected_artifact_sha256"))):
            blockers.append("invalid_contract")
        elif (receipt is None or receipt.get("source_role") not in CLAIM_ROLE_POLICY[claim_type]
              or receipt.get("artifact_sha256") != claim.get("expected_artifact_sha256")):
            blockers.append("claim_source_role_mismatch")

    if intent == "channel_recommendation":
        acquisition = trusted_acquisition_receipt
        acquisition_digest = (_artifact_digest(acquisition.get("coverage_artifact_path"), trusted_acquisition_root)
                              if isinstance(acquisition, Mapping) else None)
        expected_hash = contract.get("expected_acquisition_artifact_sha256")
        expected_capture = contract.get("expected_capture_id")
        if (not isinstance(acquisition, Mapping) or acquisition.get("schema") != ACQUISITION_SCHEMA
                or acquisition.get("issuer") != ACQUISITION_ISSUER or not _text(acquisition.get("receipt_id"))
                or not _text(acquisition.get("artifact_id")) or not _sha256(acquisition.get("artifact_sha256"))
                or not _text(acquisition.get("capture_id")) or not _sha256(acquisition.get("coverage_artifact_sha256"))
                or acquisition.get("target_key") != target_key or acquisition_digest is None
                or acquisition_digest != acquisition.get("coverage_artifact_sha256")
                or acquisition.get("artifact_sha256") != acquisition_digest
                or contract.get("acquisition_ref") != acquisition.get("receipt_id")
                or expected_hash != acquisition_digest or expected_capture != acquisition.get("capture_id")):
            blockers.append("invalid_contract")
        elif acquisition.get("summary_ready") is not True:
            blockers.append("partial_context")

        candidates = contract.get("channel_candidates")
        if (not isinstance(candidates, list) or len(candidates) < 2
                or any(not isinstance(item, Mapping) for item in candidates)):
            blockers.append("invalid_contract")
            candidates = []
        candidate_ids = [item.get("candidate_id") for item in candidates]
        acquisition_period = _period(acquisition.get("message_period")) if isinstance(acquisition, Mapping) else None
        if (not isinstance(acquisition, Mapping) or acquisition.get("candidate_scope") != candidate_ids
                or acquisition_period is None):
            blockers.append("invalid_contract")
        seen: set[str] = set()
        for candidate in candidates:
            candidate_id, bases = candidate.get("candidate_id"), candidate.get("ranking_basis")
            anchors = candidate.get("semantic_anchors")
            purpose = candidate.get("channel_purpose") or candidate.get("thread_purpose")
            message_period = _period(candidate.get("message_period"), require_role=True)
            bases_valid = isinstance(bases, list) and bool(bases) and all(
                isinstance(base, str) and base in RANKING_BASES for base in bases)
            period_bound = bool(message_period and acquisition_period and acquisition_period[0] <= message_period[0]
                                and message_period[1] <= acquisition_period[1])
            if (not _text(candidate_id) or candidate_id in seen or candidate.get("target_key") != target_key
                    or not bases_valid or not isinstance(anchors, list) or not anchors
                    or any(not _text(anchor) for anchor in anchors) or not _text(purpose) or not period_bound):
                blockers.append("invalid_contract")
            if _text(candidate_id):
                seen.add(candidate_id)
            if candidate.get("message_period_source") == "captured_at" or (not message_period and candidate.get("captured_at")):
                blockers.append("capture_time_used_as_message_time")
            if bases_valid and set(bases) <= HEURISTIC_BASES:
                blockers.append("heuristic_only_ranking")
            if not (bases_valid and "semantic_anchor" in bases and set(bases) & PURPOSE_BASES
                    and message_period and isinstance(anchors, list) and anchors
                    and all(_text(anchor) for anchor in anchors) and _text(purpose)):
                blockers.append("semantic_anchor_missing")
    return _result(intent, blockers)
