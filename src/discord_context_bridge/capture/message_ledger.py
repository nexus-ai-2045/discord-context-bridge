"""Canonical private message-event ledger and deterministic projections."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from heapq import heappop, heappush
import json
from typing import Any, Mapping, Sequence


_LEDGER_SCHEMA = "dcb-private-message-event-ledger.v1"
_EVENT_TYPES = {"message_observed"}
_SOURCES = {
    "background_cache",
    "chrome_visible_dom",
    "discord_desktop_cache",
    "rest_backfill",
    "saved_cache",
    "saved_snapshot",
}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def new_message_ledger(
    capture_id: str,
    *,
    target_key: str,
    upper_watermark: str,
) -> dict[str, Any]:
    """Create the only mutable source of truth for captured message events."""

    if not all(str(value).strip() for value in (capture_id, target_key, upper_watermark)):
        raise ValueError("capture_id, target_key, and upper_watermark are required")
    return {
        "schema": _LEDGER_SCHEMA,
        "capture_id": str(capture_id),
        "target_key": str(target_key),
        "upper_watermark": str(upper_watermark),
        "events": [],
        "outbound_actions": "disabled",
        "private_local_only": True,
    }


def append_message_event(
    ledger: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Append one validated event without mutating the caller's ledger."""

    if ledger.get("schema") != _LEDGER_SCHEMA:
        raise ValueError("unsupported message ledger schema")
    events = ledger.get("events")
    if not isinstance(events, list):
        raise ValueError("message ledger events are invalid")

    payload = dict(event)
    event_id = str(payload.get("event_id") or "").strip()
    message_id = str(payload.get("message_id") or "").strip()
    content_hash = str(payload.get("content_hash") or "").strip()
    event_type = str(payload.get("type") or "").strip()
    source = str(payload.get("source") or "").strip()
    sequence = payload.get("sequence")
    if not event_id or not message_id or not content_hash:
        raise ValueError("event_id, message_id, and content_hash are required")
    if event_type not in _EVENT_TYPES:
        raise ValueError("unsupported message event type")
    if source not in _SOURCES:
        raise ValueError("unsupported message event source")
    attachment_ids = payload.get("attachment_ids", [])
    if (
        not isinstance(attachment_ids, list)
        or any(not str(item).strip() for item in attachment_ids)
        or len(attachment_ids) != len(set(map(str, attachment_ids)))
    ):
        raise ValueError("attachment_ids must be unique non-empty values")
    semantic = {
        "event_id": event_id,
        "sequence": sequence,
        "type": event_type,
        "message_id": message_id,
        "content_hash": content_hash,
        "attachment_ids": [str(item) for item in attachment_ids],
        "source": source,
    }
    if payload.get("window_id") is not None:
        window_id = str(payload.get("window_id") or "").strip()
        window_index = payload.get("window_index")
        if (
            not window_id
            or not isinstance(window_index, int)
            or isinstance(window_index, bool)
            or window_index < 0
        ):
            raise ValueError("window_id and non-negative window_index must agree")
        semantic["window_id"] = window_id
        semantic["window_index"] = window_index
    if payload.get("content_ref"):
        semantic["content_ref"] = str(payload["content_ref"])
    for existing in events:
        if existing.get("event_id") != event_id:
            continue
        if all(existing.get(key) == value for key, value in semantic.items()):
            return deepcopy(dict(ledger))
        raise ValueError("event_id is bound to another message event")
    if sequence != len(events) + 1:
        raise ValueError("message event sequence must be contiguous")

    updated = deepcopy(dict(ledger))
    previous_hash = str(events[-1].get("event_hash") or "") if events else ""
    canonical = {
        **semantic,
        "previous_event_hash": previous_hash,
    }
    canonical["event_hash"] = _digest(canonical)
    updated["events"].append(canonical)
    return updated


def _ordered_message_ids(
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[str], bool]:
    first_seen: dict[str, int] = {}
    edges: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    windows: dict[str, list[tuple[int, str]]] = {}
    for event in events:
        message_id = str(event["message_id"])
        if message_id not in first_seen:
            first_seen[message_id] = len(first_seen)
        edges.setdefault(message_id, set())
        indegree.setdefault(message_id, 0)
        window_id = event.get("window_id")
        window_index = event.get("window_index")
        if isinstance(window_id, str) and isinstance(window_index, int):
            windows.setdefault(window_id, []).append((window_index, message_id))

    for rows in windows.values():
        ordered = [message_id for _, message_id in sorted(rows)]
        for left, right in zip(ordered, ordered[1:]):
            if left == right or right in edges[left]:
                continue
            edges[left].add(right)
            indegree[right] += 1

    ready: list[tuple[int, str]] = []
    for message_id, degree in indegree.items():
        if degree == 0:
            heappush(ready, (first_seen[message_id], message_id))
    result: list[str] = []
    while ready:
        _, message_id = heappop(ready)
        result.append(message_id)
        for target in sorted(edges[message_id], key=first_seen.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                heappush(ready, (first_seen[target], target))
    if len(result) != len(first_seen):
        return sorted(first_seen, key=first_seen.__getitem__), True
    return result, False


def _reduce_messages(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    messages: dict[str, dict[str, Any]] = {}
    attachment_ids: set[str] = set()
    for event in events:
        message_id = str(event["message_id"])
        if message_id not in messages:
            messages[message_id] = {
                "message_id": message_id,
                "version_hashes": [],
                "sources": [],
                "attachment_ids": [],
            }
        message = messages[message_id]
        content_hash = str(event["content_hash"])
        if content_hash not in message["version_hashes"]:
            message["version_hashes"].append(content_hash)
        source = str(event["source"])
        if source not in message["sources"]:
            message["sources"].append(source)
            message["sources"].sort()
        if event.get("content_ref"):
            message["content_ref"] = str(event["content_ref"])
        for attachment_id in map(str, event.get("attachment_ids") or []):
            attachment_ids.add(attachment_id)
            if attachment_id not in message["attachment_ids"]:
                message["attachment_ids"].append(attachment_id)
    ordered_message_ids, message_order_conflict = _ordered_message_ids(events)
    return {
        "messages": messages,
        "ordered_message_ids": ordered_message_ids,
        "message_order_conflict": message_order_conflict,
        "attachment_ids": sorted(attachment_ids),
    }


def build_capture_projections(
    ledger: Mapping[str, Any],
    *,
    oldest_reached: bool,
    latest_reached: bool,
    stable_scan_digests: Sequence[str],
    saved_attachment_ids: Sequence[str],
    upper_watermark_reached: bool,
    unresolved_gap_count: int,
    pending_retry_count: int,
    attachment_inventory_complete: bool,
) -> dict[str, Any]:
    """Rebuild every capture view and evidence field from one event ledger."""

    if ledger.get("schema") != _LEDGER_SCHEMA:
        raise ValueError("unsupported message ledger schema")
    events = ledger.get("events")
    if not isinstance(events, list):
        raise ValueError("message ledger events are invalid")
    expected_sequence = list(range(1, len(events) + 1))
    if [event.get("sequence") for event in events] != expected_sequence:
        raise ValueError("message event sequence is not contiguous")

    reduced = _reduce_messages(events)
    ordered_ids = reduced["ordered_message_ids"]
    records = [reduced["messages"][message_id] for message_id in ordered_ids]
    attachments = reduced["attachment_ids"]
    saved = sorted(set(map(str, saved_attachment_ids)))
    scan_digests = [str(item) for item in stable_scan_digests]
    stable_rescan = len(scan_digests) >= 2 and scan_digests[-1] == scan_digests[-2]

    normalized = {
        "schema": "dcb-normalized-thread-state.v1",
        "capture_id": str(ledger["capture_id"]),
        "target_key": str(ledger["target_key"]),
        "upper_watermark": str(ledger["upper_watermark"]),
        "message_count": len(records),
        "edited_message_count": sum(
            len(record["version_hashes"]) > 1 for record in records
        ),
        "records": records,
    }
    raw = {
        "schema": "dcb-private-raw-projection.v1",
        "message_ids": list(ordered_ids),
        "records": deepcopy(records),
    }
    markdown = {
        "schema": "dcb-markdown-projection.v1",
        "message_ids": list(ordered_ids),
        "record_count": len(ordered_ids),
        "content_refs": [
            record.get("content_ref")
            for record in records
            if record.get("content_ref")
        ],
    }
    attachment_manifest = {
        "schema": "dcb-attachment-manifest-projection.v1",
        "attachment_ids": list(attachments),
        "saved_attachment_ids": list(saved),
    }
    message_sets_equal = (
        raw["message_ids"] == markdown["message_ids"] == ordered_ids
    )
    ordered_message_digest_equal = bool(
        message_sets_equal and not reduced["message_order_conflict"]
    )
    attachments_equal = attachments == saved
    evidence = {
        "schema": "dcb-derived-full-capture-evidence.v1",
        "capture_id": str(ledger["capture_id"]),
        "target_key": str(ledger["target_key"]),
        "upper_watermark": str(ledger["upper_watermark"]),
        "oldest_reached": oldest_reached,
        "latest_reached": latest_reached,
        "upper_watermark_reached": upper_watermark_reached,
        "stable_scan_count": len(scan_digests),
        "capture_stable_after_rescan": stable_rescan,
        "message_id_sets_equal": message_sets_equal,
        "message_order_conflict": reduced["message_order_conflict"],
        "ordered_message_digest_equal": ordered_message_digest_equal,
        "attachment_id_sets_equal": attachments_equal,
        "attachment_inventory_complete": attachment_inventory_complete,
        "unresolved_gap_count": max(int(unresolved_gap_count), 0),
        "pending_retry_count": max(int(pending_retry_count), 0),
        "artifact_hashes": {
            "normalized": _digest(normalized),
            "raw": _digest(raw),
            "markdown": _digest(markdown),
            "attachment_manifest": _digest(attachment_manifest),
        },
        "full_candidate": bool(
            ordered_ids
            and oldest_reached
            and latest_reached
            and upper_watermark_reached
            and stable_rescan
            and ordered_message_digest_equal
            and attachments_equal
            and attachment_inventory_complete
            and int(unresolved_gap_count) == 0
            and int(pending_retry_count) == 0
        ),
        "outbound_actions": "disabled",
    }
    return {
        "normalized": normalized,
        "raw": raw,
        "markdown": markdown,
        "attachment_manifest": attachment_manifest,
        "evidence": evidence,
    }


def build_strict_full_capture_evidence_from_projections(
    projections: Mapping[str, Any],
    *,
    route: str,
    target_bound: bool = True,
    evidence_fresh: bool = True,
) -> dict[str, Any]:
    """Map ledger projections into the existing full-capture gate evidence shape.

    ``full_candidate`` is a local pre-check only. Final full confirmation must
    still come from ``evaluate_full_capture`` using the trusted reconcile
    producer contract — this helper does not invent a second full SSOT.
    """

    from .reconcile import build_reconciliation_evidence

    evidence = projections.get("evidence") if isinstance(projections.get("evidence"), Mapping) else {}
    raw = projections.get("raw") if isinstance(projections.get("raw"), Mapping) else {}
    markdown = projections.get("markdown") if isinstance(projections.get("markdown"), Mapping) else {}
    attachment = (
        projections.get("attachment_manifest")
        if isinstance(projections.get("attachment_manifest"), Mapping)
        else {}
    )
    raw_ids = [str(item) for item in list(raw.get("message_ids") or [])]
    markdown_ids = [str(item) for item in list(markdown.get("message_ids") or [])]
    discovered = [str(item) for item in list(attachment.get("attachment_ids") or [])]
    saved = [str(item) for item in list(attachment.get("saved_attachment_ids") or [])]
    recon = build_reconciliation_evidence(
        raw_message_ids=raw_ids,
        markdown_message_ids=markdown_ids,
        ledger_message_ids=raw_ids,
        discovered_attachment_ids=discovered,
        saved_attachment_ids=saved,
        manifest_attachment_ids=saved,
        attachment_inventory_traversal_complete=bool(
            evidence.get("attachment_inventory_complete")
        ),
    )
    capture_id = str(evidence.get("capture_id") or "").strip()
    return {
        **recon,
        "capture_id": capture_id,
        "route": str(route or "unknown"),
        "oldest_reached": bool(evidence.get("oldest_reached")),
        "latest_reached": bool(evidence.get("latest_reached")),
        "upper_watermark_reached": bool(evidence.get("upper_watermark_reached")),
        "capture_stable_after_rescan": bool(evidence.get("capture_stable_after_rescan")),
        "stable_scan_count": int(evidence.get("stable_scan_count") or 0),
        "unresolved_gap_count": int(evidence.get("unresolved_gap_count") or 0),
        "pending_retry_count": int(evidence.get("pending_retry_count") or 0),
        "target_bound": bool(target_bound),
        "capture_id_present": bool(capture_id),
        "evidence_schema_valid": evidence.get("schema") == "dcb-derived-full-capture-evidence.v1",
        "evidence_fresh": bool(evidence_fresh),
        "artifact_hashes_verified": bool(evidence.get("artifact_hashes")),
        "manifest_schema_valid": (
            attachment.get("schema") == "dcb-attachment-manifest-projection.v1"
        ),
        "external_actions": "disabled",
        "source_evidence_schema": str(evidence.get("schema") or ""),
        "full_candidate": bool(evidence.get("full_candidate")),
    }
