"""ADR-0162 Phase 1 W5: 構造化済み capture 入力を単一 append-only ledger へ ingest する。

対応フォーマット (Phase 1 は構造化済み変種のみ):

- `dcb.raw_capture.v1` (`site_adapter_runtime.build_capture` が作る既存 shape。
  `messages` に複数メッセージのリストを持つ)
- `dcb.visible_message_record.v1` / `dcb.incremental_visible_message.v1`
  (実 capture artifact のフラットな 1 行 1 メッセージ shape。`message` object
  へのネストは持たない。NDJSON の複数行 (= 複数メッセージ) は `list[dict]` として
  1 回の呼び出しにまとめて渡せる)

入力を検証し、メッセージ単位の event (`event_type: "message_observation"`) として
`text-snapshots.ndjson` へ追記する。event envelope は既存
`core.snapshot_visible_text` (core.py:2010-2079) が書く V5 フィールド集合
(event_id/stream_id/stream_sequence/expected_previous_stream_sequence/time/
content_hash/previous_content_hash/previous_event_hash/event_hash/
specversion/type/subject/datacontenttype/dataschema/acquisition_context 等)
と整合させる (`docs/operating-contract.md` の ledger 契約を参照)。

dedupe は message_id 単位 (`duplicate_message_id`) と本文単位
(`duplicate_content`) を別フラグで表す。同一 message_id でも本文が変われば
`changed=true` として編集を検知する。message_id が無い場合は
(ordinal, author_label, visible_timestamp) を代替 identity として使う。
append-only ledger には必ず追記する (保存を止めない)。取込時に
target_registry へ自動 upsert する。

target 識別は target_key (明示フィールド) > url > title > 正規化済み本文
の優先順で決める (`docs/operating-contract.md` の `target` 手順、
`core.snapshot_visible_text` と同じ導出)。

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
    normalize_message_text,
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

# `message` object へネストせず、1 行そのものが 1 メッセージであるスキーマ。
FLAT_MESSAGE_SCHEMAS = {
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
        "ordinal": message.get("ordinal"),
    }


def _first_nonempty(rows: list[dict[str, Any]], key: str) -> str:
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _require_consistent_identity(rows: list[dict[str, Any]], key: str) -> None:
    """`rows` 内で `key` が非空の値を持つ行が複数あり、かつ値が食い違う場合は reject する。

    先頭行の identity だけを見て全行を同じ target へ ingest すると、複数
    target/url が混在する NDJSON バッチを黙って先頭 target の下へ取り込んで
    しまう (codex review #1)。target 識別に使う全フィールドで一貫性を検証する。
    """
    distinct_values = {str(row.get(key) or "").strip() for row in rows}
    distinct_values.discard("")
    _require(len(distinct_values) <= 1, "ndjson_batch_mixed_target")


def _messages_from_flat_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str, str, str, str]:
    """`dcb.visible_message_record.v1` / `dcb.incremental_visible_message.v1` 用アダプタ。

    実 capture artifact はネスト `message` object ではなくフラットな 1 行 1
    メッセージ shape。`rows` に複数件渡すことで NDJSON 複数行を 1 バッチとして
    扱える。
    """
    messages = [_normalize_message(row) for row in rows]
    _require(len(messages) > 0, "messages_empty")
    for key in ("target_key", "url", "title"):
        _require_consistent_identity(rows, key)
    target_key_hint = _first_nonempty(rows, "target_key")
    url = _first_nonempty(rows, "url")
    title = _first_nonempty(rows, "title")
    source_hint = _first_nonempty(rows, "source") or "visible_text"
    return messages, url, title, source_hint, target_key_hint


def _messages_from_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str, str, str]:
    """単一 payload dict から (messages, url, title, source_hint, target_key_hint) を取り出す。"""
    schema = payload.get("schema")
    if schema == "dcb.raw_capture.v1":
        raw_messages = payload.get("messages")
        _require(isinstance(raw_messages, list), "raw_capture_messages_required")
        messages = [_normalize_message(item) for item in raw_messages]
        _require(len(messages) > 0, "messages_empty")
        url = str(payload.get("source_url") or "").strip()
        title = str(payload.get("channel_or_thread_title") or "").strip()
        source_hint = str(payload.get("capture_method") or "source_command")
        return messages, url, title, source_hint, ""

    if schema in FLAT_MESSAGE_SCHEMAS:
        return _messages_from_flat_rows([payload])

    raise IngestValidationError("adapter_not_implemented")


def _messages_from_batch(rows: list[Any]) -> tuple[list[dict[str, Any]], str, str, str, str]:
    """NDJSON 複数行 (`list[dict]`) を 1 バッチとして扱うアダプタ。"""
    _require(bool(rows) and all(isinstance(row, dict) for row in rows), "payload_must_be_object")
    schemas = {row.get("schema") for row in rows}
    _require(len(schemas) == 1, "ndjson_batch_inconsistent_schema")
    (schema,) = schemas
    _require(schema in FLAT_MESSAGE_SCHEMAS, "adapter_not_implemented")
    return _messages_from_flat_rows(rows)


def _fallback_identity_text(messages: list[dict[str, Any]]) -> str:
    """core.py:2020 の `normalize_message_text` と同じ導出に揃えるための下地文字列。"""
    mapped = [
        {
            "author_label": message["author_label"],
            "text": message["body_text"],
            "timestamp": message["visible_timestamp"],
        }
        for message in messages
    ]
    return normalize_message_text(mapped)


def _target_identity(
    target_key_hint: str, url: str, title: str, messages: list[dict[str, Any]]
) -> tuple[str, str]:
    """(target_key, key_scheme) を返す。target_key > url > title > 本文由来 の順で決める。

    本文由来 fallback は core.py:2026 (`normalize_message_text` → redact →
    先頭120文字 → hash) と同じ導出にし、`title_fallback_16` とは別の
    `content_fallback_16` を使う (M3)。
    """
    if target_key_hint:
        return target_key_hint, "url_hash_16"
    if url:
        return stable_text_hash(url), "url_hash_16"
    if title:
        return stable_text_hash(title), "title_fallback_16"
    fallback_content = redact_sensitive_storage_text(_fallback_identity_text(messages))
    return stable_text_hash(fallback_content.strip()[:120]), "content_fallback_16"


def validate_ingest_payload(payload: Any) -> None:
    """payload が対応フォーマットとして構造的に妥当かを検証する。不正なら例外。"""
    if isinstance(payload, list):
        _messages_from_batch(payload)
        return
    _require(isinstance(payload, dict), "payload_must_be_object")
    _messages_from_payload(payload)


def _safe_adapter_label(schema: Any) -> str:
    """既知 schema 値の時だけ値を返す。未知値は stdout へ反射しない (C2)。"""
    return schema if isinstance(schema, str) and schema in SUPPORTED_SCHEMAS else "unknown"


def _batch_schema_hint(rows: Any) -> Any:
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0].get("schema")
    return None


def _error_result(*, schema: Any, apply: bool, reason: str) -> dict[str, Any]:
    return {
        "schema": "dcb.ingest_result.v1",
        "ok": False,
        "reason": reason,
        "adapter": _safe_adapter_label(schema),
        "dry_run": not apply,
        "events_appended": 0,
        "duplicates": 0,
        "target_key": None,
        "outbound_actions": "disabled",
    }


def ingest_capture(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    snapshot_store: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    registry_store: Path = DEFAULT_TARGET_REGISTRY_STORE,
    apply: bool = False,
    source_ref: str = "",
) -> dict[str, Any]:
    """構造化済み capture payload を検証し、message 単位で ledger へ ingest する。

    `payload` は単一 JSON object、または NDJSON 複数行を表す `list[dict]` の
    どちらでも受け付ける。list の場合は全行が同一 schema (flat message
    schema) である 1 バッチとして処理し、1 つの target へ ingest する。

    既定は dry-run (何も書き込まず、書き込む予定の件数だけ返す)。
    `apply=True` の時だけ `snapshot_store` へ追記し、`registry_store` を
    upsert する。
    """
    if isinstance(payload, list):
        schema = _batch_schema_hint(payload)
        try:
            messages, url, title, source_hint, target_key_hint = _messages_from_batch(payload)
        except IngestValidationError as exc:
            return _error_result(schema=schema, apply=apply, reason=str(exc))
    elif isinstance(payload, dict):
        schema = payload.get("schema")
        try:
            messages, url, title, source_hint, target_key_hint = _messages_from_payload(payload)
        except IngestValidationError as exc:
            return _error_result(schema=schema, apply=apply, reason=str(exc))
    else:
        return _error_result(schema=None, apply=apply, reason="payload_must_be_object")

    target_key, key_scheme = _target_identity(target_key_hint, url, title, messages)

    existing_records = [
        record for record in load_text_snapshots(snapshot_store) if record.get("target_key") == target_key
    ]
    seen_message_id_hashes: dict[tuple[str, str], Any] = {}
    seen_identity_hashes: dict[tuple[Any, ...], Any] = {}
    for record in existing_records:
        record_message_id = record.get("message_id")
        if record_message_id:
            seen_message_id_hashes[(target_key, record_message_id)] = record.get("content_hash")
        else:
            identity = (
                target_key,
                record.get("ordinal"),
                record.get("author_label"),
                record.get("visible_timestamp"),
            )
            seen_identity_hashes[identity] = record.get("content_hash")

    stream_sequence = len(existing_records)
    last_record = existing_records[-1] if existing_records else None
    previous_event_hash = (
        str(last_record.get("event_hash") or canonical_event_hash(last_record)) if last_record else ""
    )
    previous_content_hash = str(last_record.get("content_hash") or "") if last_record else None

    captured_at = utc_now()
    new_events: list[dict[str, Any]] = []
    duplicates = 0

    for message in messages:
        body_text = redact_sensitive_storage_text(message["body_text"])
        content_hash = stable_text_hash(body_text)
        message_id = message["message_id"]
        ordinal = message["ordinal"]

        if message_id:
            message_key = (target_key, message_id)
            duplicate_message_id = message_key in seen_message_id_hashes
            duplicate_content = duplicate_message_id and seen_message_id_hashes[message_key] == content_hash
        else:
            duplicate_message_id = False
            identity = (target_key, ordinal, message["author_label"], message["visible_timestamp"])
            duplicate_content = seen_identity_hashes.get(identity) == content_hash

        if duplicate_content:
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
            "specversion": "1.0",
            "type": "message_observation",
            "subject": target_key,
            "time": captured_at,
            "datacontenttype": "text/plain; charset=utf-8",
            "dataschema": "discord_context_bridge_text_snapshot_observation.v1",
            "captured_at": captured_at,
            "observed_at": captured_at,
            "ingested_at": captured_at,
            "source": source_hint,
            "url": url,
            "title": title,
            "target_key": target_key,
            "message_id": message_id,
            "ordinal": ordinal,
            "author_label": message["author_label"],
            "visible_timestamp": message["visible_timestamp"],
            "content_hash": content_hash,
            "previous_content_hash": previous_content_hash,
            "previous_event_hash": previous_event_hash,
            "duplicate_message_id": duplicate_message_id,
            "duplicate_content": duplicate_content,
            "changed": not duplicate_content,
            "observation_index_for_target": stream_sequence,
            "acquisition_context": acquisition_context_for_source(source_hint),
            "text": body_text,
            "private_local_only": True,
            "external_share_allowed": False,
            "outbound_actions": "disabled",
        }
        event["event_hash"] = canonical_event_hash(event)
        previous_event_hash = event["event_hash"]
        previous_content_hash = content_hash

        if message_id:
            seen_message_id_hashes[(target_key, message_id)] = content_hash
        else:
            identity = (target_key, ordinal, message["author_label"], message["visible_timestamp"])
            seen_identity_hashes[identity] = content_hash

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
        "adapter": _safe_adapter_label(schema),
        "events_appended": len(new_events) if apply else 0,
        "events_pending": len(new_events) if not apply else 0,
        "duplicates": duplicates,
        "target_key": target_key,
        "outbound_actions": "disabled",
    }
