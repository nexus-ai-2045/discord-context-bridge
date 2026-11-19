from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Sequence

from .core import _cache_channel_dir, load_content_snapshot_records, target_key_for_url, redact_sensitive_storage_text
from .ingest import IngestValidationError, message_metadata_hash, normalize_message_metadata
from .knowledge_projection import _projection_records
from .url_identity import parse_effective_channel_url


class MessageQueryError(ValueError):
    pass


def _metadata(record: dict[str, Any]) -> dict[str, Any] | None:
    raw = record.get("message_metadata")
    message_id = record.get("message_id")
    if record.get("event_type") != "message_observation" or "expected_previous_stream_sequence" not in record or not isinstance(message_id, str) or re.fullmatch(r"[0-9]{17,20}", message_id) is None or raw is None:
        return None
    try:
        metadata = normalize_message_metadata(raw)
    except IngestValidationError:
        return None
    if metadata != raw or record.get("metadata_hash") != message_metadata_hash(metadata):
        raise MessageQueryError("message_query_metadata_hash_invalid")
    return metadata


def _conversation_identity(source_url: str) -> tuple[str, str]:
    identity = parse_effective_channel_url(source_url) if isinstance(source_url, str) else None
    if identity is None or source_url != f"https://discord.com/channels/{identity[0]}/{identity[1]}":
        raise MessageQueryError("message_query_target_invalid")
    return identity


def _message_order(record: dict[str, Any]) -> int:
    value = record.get("message_id")
    return int(value) if isinstance(value, str) and re.fullmatch(r"[0-9]{17,20}", value) is not None else -1


def conversation_store_paths(snapshot_root: Path, source_url: str) -> tuple[Path, Path]:
    identity = _conversation_identity(source_url)
    root = Path(snapshot_root)
    if not root.is_absolute():
        raise MessageQueryError("message_query_root_invalid")
    directory = _cache_channel_dir(root, guild_id=identity[0], channel_id=identity[1])
    return directory / "text-snapshots.ndjson", directory / "targets.ndjson"


def query_messages(
    *,
    snapshot_store: Path,
    source_url: str,
    participant_ids: Sequence[str],
    query: str = "",
    limit: int = 10,
    max_chars: int = 8000,
) -> dict[str, Any]:
    _conversation_identity(source_url)
    if isinstance(participant_ids, (str, bytes)) or not isinstance(participant_ids, (list, tuple)) or not 1 <= len(participant_ids) <= 100 or any(not isinstance(value, str) or re.fullmatch(r"[0-9]{17,20}", value) is None for value in participant_ids):
        raise MessageQueryError("message_query_participants_invalid")
    if not isinstance(query, str) or len(query) > 2000 or "\x00" in query:
        raise MessageQueryError("message_query_text_invalid")
    if type(limit) is not int or not 1 <= limit <= 100 or type(max_chars) is not int or not 512 <= max_chars <= 20000:
        raise MessageQueryError("message_query_budget_invalid")
    target_key = target_key_for_url(source_url)
    records = [
        record for record in load_content_snapshot_records(Path(snapshot_store))
        if record.get("url") == source_url
        and record.get("target_key") == target_key
        and record.get("source_url", source_url) == source_url
    ]
    latest = _projection_records(records)
    native = [(record, metadata) for record in records if (metadata := _metadata(record)) is not None]
    tombstones = {record["message_id"] for record, metadata in native if metadata["deleted"]}
    result: dict[str, Any] = {
        "schema": "dcb.message_query.v1",
        "messages": [],
        "latest_message_id": max((record["message_id"] for record, _ in native), key=int, default=None),
        "omitted": {"deleted": 0, "unknown_metadata": 0, "participant_denied": 0, "query_unmatched": 0, "budget": 0},
        "partial": True,
        "history_complete": False,
        "private_local_only": True,
        "outbound_actions": "disabled",
    }
    allowed = set(participant_ids)
    needle = query.strip().casefold()
    for record in sorted(latest, key=_message_order, reverse=True):
        if record.get("message_id") in tombstones:
            result["omitted"]["deleted"] += 1
            continue
        metadata = _metadata(record)
        if metadata is None:
            result["omitted"]["unknown_metadata"] += 1
            continue
        if metadata["author_id"] not in allowed:
            result["omitted"]["participant_denied"] += 1
            continue
        text = record.get("text")
        if not isinstance(text, str):
            result["omitted"]["unknown_metadata"] += 1
            continue
        visible = [text, *metadata.get("component_labels", [])]
        for embed in metadata.get("embeds", []):
            visible.extend(embed.get(key, "") for key in ("title", "description"))
            visible.extend(field[key] for field in embed.get("fields", []) for key in ("name", "value"))
        if needle and needle not in "\n".join(visible).casefold():
            result["omitted"]["query_unmatched"] += 1
            continue
        message = {
            "message_id": record["message_id"],
            "author_id": metadata["author_id"],
            "author_label": redact_sensitive_storage_text(str(record.get("author_label", ""))),
            "text": redact_sensitive_storage_text(text),
            "attachments": metadata["attachments"],
            "edited_at": metadata["edited_at"],
            "observed_at": redact_sensitive_storage_text(str(record.get("observed_at", ""))),
            "visible_timestamp": redact_sensitive_storage_text(str(record.get("visible_timestamp", ""))),
            "event_hash": record["event_hash"],
        }
        for key in ("embeds", "component_labels"):
            if key in metadata:
                message[key] = metadata[key]
        if len(result["messages"]) >= limit:
            result["omitted"]["budget"] += 1
            continue
        result["messages"].append(message)
        if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > max_chars:
            result["messages"].pop()
            result["omitted"]["budget"] += 1
    while result["messages"] and len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > max_chars:
        result["messages"].pop()
        result["omitted"]["budget"] += 1
    return result
