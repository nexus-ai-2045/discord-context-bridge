from __future__ import annotations

from typing import Any, Mapping, Sequence

from .capture.reconcile import build_reconciliation_evidence


_ROUTE_ALIASES = {
    "iab": "in_app_browser",
    "browser": "in_app_browser",
    "internal_browser": "in_app_browser",
    "chrome": "chrome_extension",
    "extension": "chrome_extension",
    "rest": "rest_backfill",
    "api": "rest_backfill",
    "saved": "saved_artifacts",
    "cache": "saved_artifacts",
    "desktop": "discord_desktop_accessibility",
}


def build_capture_route_policy(route: str) -> dict[str, Any]:
    """Return the route-specific read/scroll policy without exposing content."""

    normalized = _ROUTE_ALIASES.get(route.strip().lower(), route.strip().lower())
    common = {
        "schema": "discord_full_capture_route_policy.v1",
        "route": normalized,
        "immediate_visible_capture": True,
        "background_full_capture": True,
        "completion_gate": "strict_full_capture_v1",
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }
    if normalized in {"in_app_browser", "chrome_extension"}:
        return {
            **common,
            "primary_read": "visible_message_dom",
            "scroll_target": "scoped_message_list",
            "scroll_order": [
                "scoped_element_dom_scroll",
            ],
            "approval_required_fallbacks": [
                "scoped_dom_cua_scroll",
                "scoped_wheel_scroll",
                "focused_page_keys",
                "focused_home_end_keys",
                "human_assisted_scroll",
            ],
            "fallback_state": "paused_human_approval",
            "boundary_checks": [
                "oldest_reached",
                "latest_reached",
                "capture_stable_after_rescan",
            ],
        }
    if normalized == "rest_backfill":
        return {
            **common,
            "primary_read": "discord_rest_api",
            "scroll_target": "not_applicable",
            "scroll_order": [],
            "pagination": "until_history_exhausted",
            "boundary_checks": ["oldest_reached", "latest_reached"],
        }
    if normalized == "saved_artifacts":
        return {
            **common,
            "primary_read": "local_raw_markdown_ledger_reconcile",
            "scroll_target": "not_applicable",
            "scroll_order": [],
            "pagination": "not_applicable",
            "boundary_checks": ["saved_boundaries_present", "artifact_counts_consistent"],
        }
    if normalized == "discord_desktop_accessibility":
        return {
            **common,
            "primary_read": "accessibility_tree",
            "scroll_target": "scoped_message_list",
            "scroll_order": [
            ],
            "approval_required_fallbacks": [
                "scoped_accessibility_scroll",
                "focused_page_keys",
                "focused_home_end_keys",
                "human_assisted_scroll",
            ],
            "fallback_state": "paused_human_approval",
            "boundary_checks": [
                "oldest_reached",
                "latest_reached",
                "capture_stable_after_rescan",
            ],
        }
    return {
        **common,
        "primary_read": "unsupported",
        "scroll_target": "unknown",
        "scroll_order": [],
        "boundary_checks": [],
        "blocked_reason": "unsupported_capture_route",
    }


def _count(evidence: Mapping[str, Any], key: str) -> int:
    value = evidence.get(key, 0)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (OverflowError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


_BOUND_MESSAGE_ID_KEYS = (
    "raw_message_ids",
    "markdown_message_ids",
    "ledger_message_ids",
)
_BOUND_ATTACHMENT_ID_KEYS = (
    "discovered_attachment_ids",
    "saved_attachment_ids",
    "manifest_attachment_ids",
)


def _as_id_sequence(evidence: Mapping[str, Any], key: str) -> list[str] | None:
    value = evidence.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    return [str(item) for item in value]


def _recompute_from_bound_evidence(
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """caller 手書きの flag を信用せず、bound evidence (実データ) から再計算する。

    raw/markdown/ledger の message id 列 (bound evidence) が evidence に含まれる場合は、
    `build_reconciliation_evidence` で件数・集合一致・重複を再計算し、caller が別途
    手書きした同名 flag と矛盾していないかを検査する。id 列が渡されていない場合は
    再計算材料がないため、従来どおり caller の flag をそのまま使う (後方互換)。
    """

    message_id_lists = {
        key: _as_id_sequence(evidence, key) for key in _BOUND_MESSAGE_ID_KEYS
    }
    if any(value is None for value in message_id_lists.values()):
        return {}, []

    attachment_id_lists = {
        key: _as_id_sequence(evidence, key) or [] for key in _BOUND_ATTACHMENT_ID_KEYS
    }
    recomputed = build_reconciliation_evidence(
        raw_message_ids=message_id_lists["raw_message_ids"],
        markdown_message_ids=message_id_lists["markdown_message_ids"],
        ledger_message_ids=message_id_lists["ledger_message_ids"],
        discovered_attachment_ids=attachment_id_lists["discovered_attachment_ids"],
        saved_attachment_ids=attachment_id_lists["saved_attachment_ids"],
        manifest_attachment_ids=attachment_id_lists["manifest_attachment_ids"],
        attachment_inventory_traversal_complete=bool(
            evidence.get("attachment_inventory_complete")
        ),
    )
    mismatched_keys = [
        key
        for key, value in recomputed.items()
        if key in evidence and evidence.get(key) != value
    ]
    return recomputed, mismatched_keys


def evaluate_full_capture(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless boundaries, artifacts, attachments and rescan agree."""

    recomputed, mismatched_keys = _recompute_from_bound_evidence(evidence)
    if recomputed:
        merged = dict(evidence)
        merged.update(recomputed)
        evidence = merged

    message_count = _count(evidence, "message_count")
    raw_count = _count(evidence, "raw_record_count")
    markdown_count = _count(evidence, "markdown_message_count")
    ledger_count = _count(evidence, "ledger_message_count")
    discovered = _count(evidence, "attachment_discovered_count")
    saved = _count(evidence, "attachment_saved_count")
    manifested = _count(evidence, "attachment_manifest_count")

    message_counts = {message_count, raw_count, markdown_count, ledger_count}
    counts_consistent = message_count > 0 and len(message_counts) == 1
    attachments_consistent = discovered == saved == manifested
    blockers: list[str] = []

    route_policy = build_capture_route_policy(str(evidence.get("route") or "unknown"))
    if route_policy["primary_read"] == "unsupported":
        blockers.append("unsupported_capture_route")
    if evidence.get("evidence_producer") != "discord_context_bridge.capture.reconcile.v1":
        blockers.append("untrusted_evidence_producer")
    if not bool(evidence.get("target_bound")):
        blockers.append("target_not_bound")
    if not bool(evidence.get("capture_id_present")):
        blockers.append("capture_id_missing")
    if not bool(evidence.get("evidence_schema_valid")):
        blockers.append("evidence_schema_invalid")
    if not bool(evidence.get("evidence_fresh")):
        blockers.append("evidence_stale")

    if message_count <= 0:
        blockers.append("no_messages_captured")
    if not bool(evidence.get("oldest_reached")):
        blockers.append("oldest_not_reached")
    if not bool(evidence.get("latest_reached")):
        blockers.append("latest_not_reached")
    if not bool(evidence.get("upper_watermark_reached")):
        blockers.append("upper_watermark_not_reached")
    if not bool(evidence.get("capture_stable_after_rescan")):
        blockers.append("rescan_not_stable")
    if _count(evidence, "stable_scan_count") < 2:
        blockers.append("stable_scan_count_insufficient")
    if _count(evidence, "unresolved_gap_count") != 0:
        blockers.append("unresolved_message_gaps")
    if _count(evidence, "duplicate_message_count") != 0:
        blockers.append("duplicate_messages_present")
    if not counts_consistent:
        blockers.append("message_artifact_count_mismatch")
    if not bool(evidence.get("message_id_sets_equal")):
        blockers.append("message_id_set_mismatch")
    if not bool(evidence.get("ordered_message_digest_equal")):
        blockers.append("message_order_mismatch")
    if not bool(evidence.get("artifact_hashes_verified")):
        blockers.append("artifact_hash_verification_failed")
    if not bool(evidence.get("attachment_inventory_complete")):
        blockers.append("attachment_inventory_incomplete")
    if not attachments_consistent:
        blockers.append("attachment_count_mismatch")
    if not bool(evidence.get("attachment_id_sets_equal")):
        blockers.append("attachment_id_set_mismatch")
    if not bool(evidence.get("manifest_schema_valid")):
        blockers.append("attachment_manifest_schema_invalid")
    if _count(evidence, "pending_retry_count") != 0:
        blockers.append("pending_retry")
    if evidence.get("external_actions") != "disabled":
        blockers.append("external_actions_not_disabled")
    if mismatched_keys:
        blockers.append("evidence_recomputation_mismatch")

    hard_blockers = {
        "unsupported_capture_route",
        "evidence_schema_invalid",
        "untrusted_evidence_producer",
        "evidence_recomputation_mismatch",
    }
    status = "full" if not blockers else "blocked" if message_count <= 0 or hard_blockers.intersection(blockers) else "partial"
    return {
        "language": "ja",
        "schema": "discord_full_capture_completion_gate.v1",
        "status": status,
        "full_capture_confirmed": status == "full",
        "route": str(evidence.get("route") or "unknown"),
        "boundaries": {
            "oldest_reached": bool(evidence.get("oldest_reached")),
            "latest_reached": bool(evidence.get("latest_reached")),
            "capture_stable_after_rescan": bool(evidence.get("capture_stable_after_rescan")),
        },
        "counts": {
            "messages": message_count,
            "raw_records": raw_count,
            "markdown_messages": markdown_count,
            "ledger_messages": ledger_count,
            "attachments_discovered": discovered,
            "attachments_saved": saved,
            "attachments_manifested": manifested,
        },
        "counts_consistent": counts_consistent,
        "attachments_consistent": attachments_consistent,
        "unresolved_gap_count": _count(evidence, "unresolved_gap_count"),
        "bound_evidence_recomputed": bool(recomputed),
        "evidence_recomputation_mismatched_keys": mismatched_keys,
        "blockers": blockers,
        "next_action": "context_understanding" if status == "full" else "continue_full_capture",
        "raw_text_returned": False,
        "participant_names_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }
