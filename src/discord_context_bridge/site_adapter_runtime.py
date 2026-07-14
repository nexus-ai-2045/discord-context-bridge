from __future__ import annotations

import hashlib
import fnmatch
import json
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlparse
from jsonschema import validate


REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_MESSAGES = 2_000
MAX_BODY_TEXT_CHARS = 1_000_000
MAX_ATTACHMENTS_PER_MESSAGE = 100


def enforce_capture_budget(payload: Any) -> None:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise ValueError("structured input must be JSON-compatible") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError("aggregate input limit exceeded")


def load_adapter_definition(
    *, index_path: Path | None = None, definition_path: Path | None = None
) -> dict[str, Any]:
    index_path = index_path or REPO_ROOT / "site_adapters" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next((item for item in index.get("adapters", []) if item.get("adapter_id") == "discord.v1"), None)
    if not entry:
        raise ValueError("site adapter definition drift: discord.v1 missing from index")
    definition_path = definition_path or REPO_ROOT / entry["definition"]
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    expected_fields = ["source_url", "captured_at", "site", "adapter_id", "adapter_version", "capture_method", "read_scope", "messages", "messages.ordinal", "messages.visible_timestamp", "messages.author_label", "messages.body_text", "messages.attachments", "messages.extraction_confidence"]
    if (
        definition.get("adapter_id") != entry["adapter_id"]
        or definition.get("site") != entry["site"]
        or definition.get("read_only") is not True
        or definition.get("outbound_actions") != "disabled"
        or definition.get("required_extraction_fields") != expected_fields
        or [item.get("method") for item in definition.get("capture_order", [])] != ["structured_dom", "body_inner_text", "message_container_text"]
        or definition.get("drift_checks") != [
            "adapter_url_pattern_miss", "message_container_zero_hits",
            "structured_dom_empty_but_body_text_present",
            "timestamp_or_author_or_body_missing_repeatedly",
            "attachment_url_unmapped_to_message", "selector_hit_rate_drop",
            "viewport_bottom_unconfirmed", "multiple_primary_selectors_failed",
            "empty_visible_text",
        ]
    ):
        raise ValueError("site adapter definition drift")
    definition = dict(definition)
    definition["_url_patterns"] = list(entry.get("url_patterns") or [])
    return definition


def select_adapter(source_url: str, *, index_path: Path | None = None, definition_path: Path | None = None) -> dict[str, str]:
    definition = load_adapter_definition(index_path=index_path, definition_path=definition_path)
    if not any(fnmatch.fnmatchcase(source_url, pattern) for pattern in definition["_url_patterns"]):
        raise ValueError("matching site adapter was not found")
    version = definition["adapter_id"].rsplit(".v", 1)[-1]
    return {"site": definition["site"], "adapter_id": definition["adapter_id"], "adapter_version": version}


def _message(item: dict[str, Any], ordinal: int) -> dict[str, Any]:
    attachments = item.get("attachments") or []
    if not isinstance(attachments, list) or not all(isinstance(value, (str, dict)) for value in attachments):
        raise ValueError("attachments must be a list of strings or objects")
    if len(attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise ValueError("attachment limit exceeded")
    body_text = str(item.get("body_text", ""))
    if len(body_text) > MAX_BODY_TEXT_CHARS:
        raise ValueError("message body limit exceeded")
    return {
        "ordinal": ordinal,
        "visible_timestamp": str(item.get("visible_timestamp", "")),
        "author_label": str(item.get("author_label", "")),
        "body_text": body_text,
        "attachments": attachments,
        "extraction_confidence": item.get("extraction_confidence", "high"),
    }


def build_capture(
    source_url: str,
    *,
    structured: dict[str, Any] | None = None,
    body_text: str | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    if structured is not None and not isinstance(structured, dict):
        raise ValueError("structured input must be an object")
    if structured is not None:
        enforce_capture_budget(structured)
    elif len((body_text or "").encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("aggregate input limit exceeded")
    adapter = select_adapter(source_url)
    drift: list[str] = []
    if structured is not None:
        raw_messages = structured.get("messages") or []
        if not isinstance(raw_messages, list) or not all(isinstance(item, dict) for item in raw_messages):
            raise ValueError("structured messages must be a list of objects")
        if len(raw_messages) > MAX_MESSAGES:
            raise ValueError("message limit exceeded")
        messages = [_message(item, index) for index, item in enumerate(raw_messages)]
        method = "structured_dom"
        if structured.get("selector_hits") == 0:
            drift.append("message_container_zero_hits")
        if any(not message["body_text"] or not message["author_label"] or not message["visible_timestamp"] for message in messages):
            drift.append("timestamp_or_author_or_body_missing_repeatedly")
        if not any(message["body_text"] for message in messages) and structured.get("body_text_present"):
            drift.append("structured_dom_empty_but_body_text_present")
        if structured.get("missing_field_streak", 0) >= 2:
            drift.append("timestamp_or_author_or_body_missing_repeatedly")
        if structured.get("selector_hit_rate_drop"):
            drift.append("selector_hit_rate_drop")
        if structured.get("viewport_bottom_confirmed") is False:
            drift.append("viewport_bottom_unconfirmed")
        if structured.get("failed_primary_selectors", 0) >= 2:
            drift.append("multiple_primary_selectors_failed")
        raw_unattributed = structured.get("unattributed_cache_candidates") or []
        if not isinstance(raw_unattributed, list) or not all(isinstance(value, (str, dict)) for value in raw_unattributed):
            raise ValueError("unattributed_cache_candidates must be a list of strings or objects")
        if len(raw_unattributed) > MAX_MESSAGES:
            raise ValueError("unattributed candidate limit exceeded")
        unattributed = raw_unattributed
    else:
        messages = [_message({"body_text": body_text or "", "extraction_confidence": "low"}, 0)]
        method = "body_inner_text"
        unattributed = []
        if not body_text:
            drift.append("empty_visible_text")
    if unattributed:
        drift.append("attachment_url_unmapped_to_message")
    return {
        "schema": "dcb.raw_capture.v1",
        **adapter,
        "source_url": source_url,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "capture_method": method,
        "read_scope": "visible_viewport",
        "messages": messages,
        "unattributed_cache_candidates": unattributed,
        "viewport_bottom_confirmed": structured.get("viewport_bottom_confirmed") if structured is not None else None,
        "completion_evidence": {"source": "caller_supplied", "trusted": False},
        "drift": {"detected": bool(drift), "items": drift},
        "safety": {
            "outbound_actions": "disabled",
            "raw_contains_private_text": True,
            "external_share_allowed": False,
        },
    }


def capture_id(capture: dict[str, Any]) -> str:
    encoded = json.dumps(capture, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _relative_path(value: str) -> str:
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact path must be relative")
    return path.as_posix()


def build_manifest(capture: dict[str, Any], *, raw_json: str) -> dict[str, Any]:
    raw_json = _relative_path(raw_json)
    drift = capture["drift"]
    messages = capture.get("messages") or []
    full_fields = bool(messages) and all(m.get("body_text") and m.get("author_label") and m.get("visible_timestamp") for m in messages)
    # v1 has no trusted completion producer. Caller-provided evidence must never
    # promote a visible-viewport capture to full.
    state = "drift_detected" if drift["detected"] else "partial"
    return {
        "schema": "dcb.capture_manifest.v1",
        "capture_id": capture_id(capture),
        "site": capture["site"],
        "adapter_id": capture["adapter_id"],
        "adapter_version": capture["adapter_version"],
        "status": state,
        "capture_state": "full" if state == "captured" else "partial",
        "raw_json": raw_json,
        "source_url_hash": hashlib.sha256(capture["source_url"].encode("utf-8")).hexdigest(),
        "coverage": {"visible_message_count": len(messages), "required_fields_complete": full_fields},
        "freshness": {"captured_at": capture["captured_at"], "viewport_bottom_confirmed": capture.get("viewport_bottom_confirmed")},
        "drift": {"detected": drift["detected"], "items": list(drift["items"])},
        "review_required": True,
        "outbound_actions": "disabled",
    }


def validate_artifact(payload: dict[str, Any], schema_name: str) -> None:
    schema = json.loads((REPO_ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    validate(instance=payload, schema=schema)
