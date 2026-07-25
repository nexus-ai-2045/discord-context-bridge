"""ADR-0162 Phase 1 W5: 構造化済み capture 入力を単一 append-only ledger へ ingest する。

対応フォーマット (Phase 1 は構造化済み変種のみ):

- `dcb.raw_capture.v1` (`site_adapter_runtime.build_capture` が作る既存 shape)
- `dcb.visible_message_record.v1` (単一 message 観測。`message` object を持つ)
- `dcb.incremental_visible_message.v1` (live 差分観測。shape は
  `dcb.visible_message_record.v1` と同じで `delta_kind` を持てる)

入力を検証し、メッセージ単位の event (`event_type: "message_observation"`) として
`text-snapshots.ndjson` へ追記する。event envelope は既存
`core.snapshot_visible_text` が作る V5 フィールド集合
(event_id/stream_id/stream_sequence/content_hash/previous_event_hash/event_hash/
acquisition_context 等) と整合させる。

dedupe は同一 (target_key, message_id) または正規化本文 content_hash の既出を
`duplicate_content=true` としてフラグするだけで、append-only ledger には
必ず追記する (保存を止めない)。取込時に target_registry へ自動 upsert する。

stdout は metadata-only。呼び出し側 (`scripts/ingest_capture.py`) はこの
モジュールが返す dict をそのまま出力してよい (raw text を含まない)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import (
    DEFAULT_TEXT_SNAPSHOT_STORE,
    acquisition_context_for_source,
    append_text_snapshot,
    canonical_event_hash,
    load_text_snapshots,
    redact_sensitive_storage_text,
    stable_text_hash,
    utc_now,
)
from .target_registry import DEFAULT_TARGET_REGISTRY_STORE, register_target

SUPPORTED_SCHEMAS = {
    "dcb.raw_capture.v1",
    "dcb.visible_message_record.v1",
    "dcb.incremental_visible_message.v1",
}


class IngestValidationError(ValueError):
    """入力 payload が対応フォーマットとして不正な場合。"""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise IngestValidationError(reason)


def _normalize_message(message: Any) -> dict[str, Any]:
    _require(isinstance(message, dict), "message_must_be_object")
    body_text = message.get("body_text")
    if body_text is None:
        body_text = message.get("text")
    return {
        "message_id": str(message.get("message_id") or message.get("id") or ""),
        "author_label": str(message.get("author_label") or message.get("author") or ""),
        "visible_timestamp": str(message.get("visible_timestamp") or message.get("timestamp") or ""),
        "body_text": str(body_text or ""),
    }


def _messages_from_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str, str]:
    """payload から (messages, url, title, source_hint) を取り出す。構造検証を兼ねる。"""
    schema = payload.get("schema")
    if schema == "dcb.raw_capture.v1":
        raw_messages = payload.get("messages")
        _require(isinstance(raw_messages, list), "raw_capture_messages_required")
        messages = [_normalize_message(item) for item in raw_messages]
        url = str(payload.get("source_url") or "")
        title = str(payload.get("channel_or_thread_title") or "")
        source_hint = str(payload.get("capture_method") or "source_command")
        return messages, url, title, source_hint

    if schema in ("dcb.visible_message_record.v1", "dcb.incremental_visible_message.v1"):
        message = payload.get("message")
        _require(isinstance(message, dict), "message_object_required")
        messages = [_normalize_message(message)]
        url = str(payload.get("url") or "")
        title = str(payload.get("title") or "")
        source_hint = str(payload.get("source") or "visible_text")
        return messages, url, title, source_hint

    raise IngestValidationError("adapter_not_implemented")


def _target_identity(url: str, title: str, fallback_text: str) -> tuple[str, str]:
    """(target_key, key_scheme) を返す。url > title > 本文冒頭 の順で決める。"""
    if url.strip():
        return stable_text_hash(url.strip()), "url_hash_16"
    if title.strip():
        return stable_text_hash(title.strip()), "title_fallback_16"
    identity = fallback_text.strip()[:120]
    return stable_text_hash(identity), "title_fallback_16"


def validate_ingest_payload(payload: Any) -> None:
    """payload が対応フォーマットとして構造的に妥当かを検証する。不正なら例外。"""
    _require(isinstance(payload, dict), "payload_must_be_object")
    _messages_from_payload(payload)


def _error_result(*, schema: Any, apply: bool, reason: str) -> dict[str, Any]:
    return {
        "schema": "dcb.ingest_result.v1",
        "ok": False,
        "reason": reason,
        "adapter": schema if isinstance(schema, str) else "unknown",
        "dry_run": not apply,
        "events_appended": 0,
        "duplicates": 0,
        "target_key": None,
        "outbound_actions": "disabled",
    }


def ingest_capture(
    payload: dict[str, Any],
    *,
    snapshot_store: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    registry_store: Path = DEFAULT_TARGET_REGISTRY_STORE,
    apply: bool = False,
    source_ref: str = "",
) -> dict[str, Any]:
    """構造化済み capture payload を検証し、message 単位で ledger へ ingest する。

    既定は dry-run (何も書き込まず、書き込む予定の件数だけ返す)。
    `apply=True` の時だけ `snapshot_store` へ追記し、`registry_store` を
    upsert する。
    """
    if not isinstance(payload, dict):
        return _error_result(schema=None, apply=apply, reason="payload_must_be_object")

    schema = payload.get("schema")
    try:
        messages, url, title, source_hint = _messages_from_payload(payload)
    except IngestValidationError as exc:
        return _error_result(schema=schema, apply=apply, reason=str(exc))

    fallback_text = messages[0]["body_text"] if messages else ""
    target_key, key_scheme = _target_identity(url, title, fallback_text)

    existing_records = [
        record for record in load_text_snapshots(snapshot_store) if record.get("target_key") == target_key
    ]
    seen_message_keys = {
        (target_key, record.get("message_id"))
        for record in existing_records
        if record.get("message_id")
    }
    seen_content_hashes = {record.get("content_hash") for record in existing_records}
    stream_sequence = len(existing_records)
    previous_event_hash = str(existing_records[-1].get("event_hash") or "") if existing_records else ""

    captured_at = utc_now()
    new_events: list[dict[str, Any]] = []
    duplicates = 0

    for message in messages:
        body_text = redact_sensitive_storage_text(message["body_text"])
        content_hash = stable_text_hash(body_text)
        message_id = message["message_id"]
        is_duplicate = (
            (target_key, message_id) in seen_message_keys if message_id else content_hash in seen_content_hashes
        )
        if is_duplicate:
            duplicates += 1

        stream_sequence += 1
        event: dict[str, Any] = {
            "schema": "discord_context_bridge_text_snapshot_observation.v1",
            "event_id": stable_text_hash(
                "|".join([captured_at, target_key, content_hash, source_hint, str(stream_sequence)])
            ),
            "event_type": "message_observation",
            "stream_id": target_key,
            "stream_sequence": stream_sequence,
            "expected_previous_stream_sequence": stream_sequence - 1,
            "captured_at": captured_at,
            "observed_at": captured_at,
            "ingested_at": captured_at,
            "source": source_hint,
            "url": url,
            "title": title,
            "target_key": target_key,
            "message_id": message_id,
            "author_label": message["author_label"],
            "visible_timestamp": message["visible_timestamp"],
            "content_hash": content_hash,
            "previous_event_hash": previous_event_hash,
            "duplicate_content": is_duplicate,
            "changed": not is_duplicate,
            "observation_index_for_target": stream_sequence,
            "acquisition_context": acquisition_context_for_source(source_hint),
            "text": body_text,
            "private_local_only": True,
            "external_share_allowed": False,
            "outbound_actions": "disabled",
        }
        event["event_hash"] = canonical_event_hash(event)
        previous_event_hash = event["event_hash"]
        if message_id:
            seen_message_keys.add((target_key, message_id))
        seen_content_hashes.add(content_hash)
        new_events.append(event)

    if apply:
        for event in new_events:
            append_text_snapshot(event, snapshot_store)
        register_target(
            target_key=target_key,
            key_scheme=key_scheme,
            url=url or None,
            channel_label=title or None,
            source="capture",
            source_ref=source_ref or None,
            path=registry_store,
        )

    return {
        "schema": "dcb.ingest_result.v1",
        "ok": True,
        "dry_run": not apply,
        "adapter": schema,
        "events_appended": len(new_events) if apply else 0,
        "events_pending": len(new_events) if not apply else 0,
        "duplicates": duplicates,
        "target_key": target_key,
        "outbound_actions": "disabled",
    }
