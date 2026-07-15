from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core import (
    DEFAULT_TEXT_SNAPSHOT_STORE,
    matching_snapshot_records,
    parse_snapshot_timestamp,
    plan_discord_url_read,
    snapshot_freshness,
    stale_policy_for_freshness,
    target_key_for_url,
)
from .local_config import resolve_shared_snapshot_root


TEXT_RECORD_SUFFIXES = {".ndjson", ".jsonl"}
TITLE_KEYS = ("title", "conversation_title", "channel_name", "thread_name")


def _safe_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def _dedupe_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        identity = str(record.get("event_id") or "")
        if not identity:
            identity = json.dumps(
                {
                    "captured_at": record.get("captured_at") or record.get("observed_at"),
                    "content_hash": record.get("content_hash"),
                    "title": record.get("title"),
                    "url": record.get("url"),
                    "target_key": record.get("target_key"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(record)
    return unique


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:8192]
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    sections = text.split("---", 2)
    if len(sections) < 3:
        return {}
    payload: dict[str, str] = {}
    for line in sections[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() and value.strip():
            payload[key.strip()] = value.strip().strip('"\'')
    return payload


def _record_title(records: list[dict[str, Any]]) -> tuple[str, str]:
    candidates: list[tuple[datetime, str, str]] = []
    for record in records:
        title = next((str(record.get(key) or "").strip() for key in TITLE_KEYS if record.get(key)), "")
        if not title:
            continue
        captured = parse_snapshot_timestamp(
            record.get("captured_at") or record.get("observed_at") or record.get("timestamp")
        ) or datetime.min.replace(tzinfo=timezone.utc)
        source = str(record.get("source") or record.get("source_kind") or "snapshot_record")
        candidates.append((captured, title, source))
    if not candidates:
        return "", "none"
    _captured, title, source = max(candidates, key=lambda item: item[0])
    return title, source


def build_cache_inventory(
    *,
    url: str,
    snapshot_store: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    cache_root: Path | None = None,
    config_path: Path | None = None,
    include_private_title: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    plan = plan_discord_url_read(url)
    if not plan.get("ok_to_open"):
        return {
            "language": "ja",
            "schema": "discord_cache_inventory.v1",
            "ok": False,
            "state": "invalid_url",
            "reason": plan.get("reason", "discord_channel_url_required"),
            "url_output": "omitted",
            "path_output": "omitted",
            "raw_text_returned": False,
            "outbound_actions": "disabled",
        }

    resolved = resolve_shared_snapshot_root(explicit=cache_root, config_path=config_path)
    root = resolved.path
    target_key = target_key_for_url(url)
    files = _safe_files(root)
    markdown_files = [path for path in files if path.suffix.lower() == ".md"]
    record_files = [path for path in files if path.suffix.lower() in TEXT_RECORD_SUFFIXES]

    records: list[dict[str, Any]] = []
    matched_record_files: list[Path] = []
    for path in record_files:
        matches = matching_snapshot_records(path, url=url, target_key=target_key)
        if matches:
            records.extend(matches)
            matched_record_files.append(path)
    saved_matches = matching_snapshot_records(snapshot_store, url=url, target_key=target_key)
    records.extend(saved_matches)
    records = _dedupe_records(records)

    matching_markdown: list[tuple[Path, dict[str, str]]] = []
    for path in markdown_files:
        metadata = _frontmatter(path)
        if metadata.get("target_key") == target_key or metadata.get("url") == url:
            matching_markdown.append((path, metadata))

    title, title_source = _record_title(records)
    if not title:
        for _path, metadata in matching_markdown:
            if metadata.get("title"):
                title = metadata["title"]
                title_source = "markdown_frontmatter"
                break

    generated = generated_at or datetime.now(timezone.utc).isoformat()
    freshness = snapshot_freshness(records, generated_at=generated, source="local_snapshot")
    policy = stale_policy_for_freshness(freshness)
    if freshness["status"] == "recent":
        decision = "use_local_snapshot"
    elif freshness["status"] in {"stale", "unknown"}:
        decision = "refresh_exact_url_snapshot"
    else:
        decision = "capture_visible_or_read_only_adapter"

    latest_mtime = max((path.stat().st_mtime for path in files), default=0.0)
    title_payload: dict[str, Any] = {
        "available": bool(title),
        "source": title_source,
        "value_output": "included" if include_private_title and title else "omitted",
    }
    if include_private_title and title:
        title_payload["value"] = title

    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    return {
        "language": "ja",
        "schema": "discord_cache_inventory.v1",
        "ok": root.exists() or bool(records),
        "state": "ready" if records else "snapshot_missing",
        "target": {
            "target_key": target_key,
            "url_present": True,
            "url_output": "omitted",
        },
        "configuration": {
            "source": resolved.source,
            "root_present": root.exists(),
            "path_output": "omitted",
        },
        "inventory": {
            "total_file_count": len(files),
            "markdown_file_count": len(markdown_files),
            "ndjson_file_count": len(record_files),
            "total_bytes": sum(path.stat().st_size for path in files),
            "latest_file_age_seconds": max(0, int(time.time() - latest_mtime)) if latest_mtime else None,
            "exact_record_file_count": len(matched_record_files),
            "exact_markdown_file_count": len(matching_markdown),
            "exact_snapshot_record_count": len(records),
        },
        "title": title_payload,
        "freshness": freshness,
        "stale_policy": policy,
        "decision": decision,
        "scan_elapsed_ms": elapsed_ms,
        "raw_text_returned": False,
        "participant_names_returned": False,
        "discord_ids_returned": False,
        "local_paths_returned": False,
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }
