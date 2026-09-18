"""Discord Desktop cache の messages 応答を `dcb.visible_message_record.v1` へ変換する。

`desktop-cache-probe` は metadata-only、`cache-inventory` / `cache-first-intake` は
S1 (`text-snapshots.ndjson`) を読むだけで、Desktop cache に残る
`channels/{id}/messages` 応答の本文を S1 へ入れる経路が無かった。このモジュールは
その変換だけを担う。

- 入力: Discord Desktop の `Cache/Cache_Data` (Chromium simple cache v5)。読み取りのみ。
- 出力: `out_dir/records/<target_key>.ndjson` (1 target = 1 ファイル。
  `scripts/ingest_capture.py --input` がそのまま 1 バッチとして読める)、
  `out_dir/report.json` (metadata-only)、`out_dir/channel-map.json`
  (channel → server 対応。Discord ID を含む private local file)。
- S1 へは書かない。S1 への取り込みは ADR-0164 に従い ingest CLI 経由で行う。

target_key は既存 `core.target_key_for_url` (sha256(url)[:16]) で計算する。URL は
S1 の既存表記に揃え、thread は `/channels/{guild}/{parent}/threads/{thread}`、
それ以外は `/channels/{guild}/{channel}` とする。server を特定できない channel は
URL を作れないため出力せず、`server_unknown` として件数を報告する。
本文が空のメッセージも黙って捨てず、理由別に件数を報告する。

同じ cache 入力からは常に同じ出力 (bytes 単位) になるよう、走査順・重複解決・
captured_at の決め方をすべて入力だけから決める。
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from .core import stable_text_hash, target_key_for_url
from .desktop_cache import (
    CacheEntryError,
    UnsupportedCacheFormat,
    UnsupportedEncoding,
    _decode_simple_cache_body,
    _read_simple_cache_entry_parts,
    _read_simple_cache_key,
)
from .site_adapter_runtime import MAX_MESSAGES

REPORT_SCHEMA = "dcb.desktop_cache_record_export.v1"
CHANNEL_MAP_SCHEMA = "dcb.desktop_cache_channel_map.v1"
RECORD_SCHEMA = "dcb.visible_message_record.v1"
RECORD_SOURCE = "discord_desktop_cache"

# 通常の会話メッセージとして扱う Discord message type。
# 0=DEFAULT, 19=REPLY, 20=CHAT_INPUT_COMMAND, 23=CONTEXT_MENU_COMMAND。
# それ以外 (参加通知・ピン留め通知・thread 作成通知など) は system message として除外する。
CONVERSATION_MESSAGE_TYPES = {0, 19, 20, 23}
THREAD_CHANNEL_TYPES = {10, 11, 12}
DM_CHANNEL_TYPES = {1, 3}

_SNOWFLAKE = r"\d{15,21}"
_MESSAGES_PATH_RE = re.compile(rf"/api/v\d+/channels/({_SNOWFLAKE})/messages$")
_GUILD_PATH_RE = re.compile(rf"/api/v\d+/guilds/({_SNOWFLAKE})/(messages/search|channels|threads/active)$")
_STREAM_KEY_RE = re.compile(rf"/streams/guild(?:%3A|:)({_SNOWFLAKE})(?:%3A|:)({_SNOWFLAKE})")
_SNOWFLAKE_RE = re.compile(rf"^{_SNOWFLAKE}$")
_SNAPSHOT_URL_RE = re.compile(
    rf"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/({_SNOWFLAKE}|@me)/({_SNOWFLAKE})"
    rf"(?:/threads/({_SNOWFLAKE}))?"
)


@dataclass
class _Candidate:
    message: dict[str, Any]
    observed_at: str
    entry_name: str

    def rank(self) -> tuple[str, str, str]:
        return (str(self.message.get("edited_timestamp") or ""), self.observed_at, self.entry_name)


@dataclass
class _ChannelMap:
    guild_of: dict[str, str] = field(default_factory=dict)
    parent_of: dict[str, str] = field(default_factory=dict)
    dm_channels: set[str] = field(default_factory=set)
    channel_names: dict[str, str] = field(default_factory=dict)
    guild_names: dict[str, str] = field(default_factory=dict)
    sources: dict[str, int] = field(default_factory=dict)
    conflicts: int = 0

    def add_guild(self, channel_id: Any, guild_id: Any, source: str) -> None:
        channel = str(channel_id or "")
        guild = str(guild_id or "")
        if not _SNOWFLAKE_RE.match(channel) or not _SNOWFLAKE_RE.match(guild):
            return
        existing = self.guild_of.get(channel)
        if existing is None:
            self.guild_of[channel] = guild
            self.sources[source] = self.sources.get(source, 0) + 1
        elif existing != guild:
            self.conflicts += 1

    def add_parent(self, channel_id: Any, parent_id: Any) -> None:
        channel = str(channel_id or "")
        parent = str(parent_id or "")
        if _SNOWFLAKE_RE.match(channel) and _SNOWFLAKE_RE.match(parent):
            self.parent_of.setdefault(channel, parent)

    def guild_for(self, channel_id: str) -> str | None:
        guild = self.guild_of.get(channel_id)
        if guild:
            return guild
        parent = self.parent_of.get(channel_id)
        return self.guild_of.get(parent) if parent else None


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _is_message_like(item: dict[str, Any]) -> bool:
    return "content" in item or "author" in item


def _collect_channel_metadata(key_path: str, payload: Any, channel_map: _ChannelMap) -> None:
    stream = _STREAM_KEY_RE.search(key_path)
    if stream:
        channel_map.add_guild(stream.group(2), stream.group(1), "stream_key")

    guild_path = _GUILD_PATH_RE.search(key_path)
    for item in _walk_dicts(payload):
        item_type = item.get("type")
        if "channel_id" in item and "guild_id" in item:
            channel_map.add_guild(item.get("channel_id"), item.get("guild_id"), "channel_guild_pair")
        if _is_message_like(item):
            if guild_path and guild_path.group(2) == "messages/search" and "channel_id" in item:
                channel_map.add_guild(item.get("channel_id"), guild_path.group(1), "guild_search_key")
            continue
        if "id" not in item:
            continue
        item_id = str(item.get("id"))
        if isinstance(item_type, int) and item_type in DM_CHANNEL_TYPES and isinstance(item.get("recipients"), list):
            channel_map.dm_channels.add(item_id)
            if isinstance(item.get("name"), str) and item["name"].strip():
                channel_map.channel_names.setdefault(item_id, item["name"].strip())
            continue
        if isinstance(item_type, int) and ("guild_id" in item or "parent_id" in item):
            if "guild_id" in item:
                channel_map.add_guild(item_id, item.get("guild_id"), "channel_object")
            if item_type in THREAD_CHANNEL_TYPES and item.get("parent_id"):
                channel_map.add_parent(item_id, item.get("parent_id"))
            if isinstance(item.get("name"), str) and item["name"].strip():
                channel_map.channel_names.setdefault(item_id, item["name"].strip())
            continue
        if guild_path and guild_path.group(2) in {"channels", "threads/active"} and isinstance(item_type, int):
            channel_map.add_guild(item_id, guild_path.group(1), "guild_channels_key")
            continue
        if "features" in item and "type" not in item and isinstance(item.get("name"), str):
            if _SNOWFLAKE_RE.match(item_id) and item["name"].strip():
                channel_map.guild_names.setdefault(item_id, item["name"].strip())


def _load_snapshot_url_hints(path: Path, channel_map: _ChannelMap) -> int:
    """S1 に既にある URL から channel → server 対応を補う (読み取りのみ)。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    added = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        for match in _SNAPSHOT_URL_RE.finditer(str(record.get("url") or "")):
            guild, channel, thread = match.group(1), match.group(2), match.group(3)
            if guild == "@me":
                channel_map.dm_channels.add(channel)
                continue
            before = len(channel_map.guild_of)
            channel_map.add_guild(channel, guild, "snapshot_store_url")
            if thread:
                channel_map.add_guild(thread, guild, "snapshot_store_url")
                channel_map.add_parent(thread, channel)
            added += len(channel_map.guild_of) - before
    return added


def _response_observed_at(response_metadata: bytes, path: Path) -> str:
    for raw_line in response_metadata.split(b"\n"):
        line = raw_line.strip().decode("latin-1", errors="replace")
        if line.lower().startswith("date:"):
            try:
                parsed = parsedate_to_datetime(line.split(":", 1)[1].strip())
            except (TypeError, ValueError):
                break
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat()
    except OSError:
        return ""


def _is_message_object(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and _SNOWFLAKE_RE.match(str(item.get("id") or "")) is not None
        and _SNOWFLAKE_RE.match(str(item.get("channel_id") or "")) is not None
        and isinstance(item.get("timestamp"), str)
        and isinstance(item.get("author"), dict)
    )


def _exclusion_reason(message: dict[str, Any]) -> str | None:
    message_type = message.get("type", 0)
    if message_type not in CONVERSATION_MESSAGE_TYPES:
        return "system_message_type"
    if str(message.get("content") or "").strip():
        return None
    if message.get("attachments"):
        return "empty_body_attachment_only"
    if message.get("message_snapshots"):
        return "empty_body_forward_only"
    if message.get("sticker_items"):
        return "empty_body_sticker_only"
    if message.get("poll"):
        return "empty_body_poll_only"
    if message.get("embeds"):
        return "empty_body_embed_only"
    return "empty_body_no_content"


def _author_label(author: dict[str, Any]) -> str:
    for key in ("global_name", "username"):
        value = author.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _server_key(guild_id: str) -> str:
    return stable_text_hash(f"discord_guild:{guild_id}")


def _channel_url(channel_id: str, channel_map: _ChannelMap) -> tuple[str | None, str, str | None]:
    """(url, kind, guild_id) を返す。server 不明なら url=None。"""
    if channel_id in channel_map.dm_channels:
        return f"https://discord.com/channels/@me/{channel_id}", "dm", None
    guild = channel_map.guild_for(channel_id)
    if not guild:
        return None, "server_unknown", None
    parent = channel_map.parent_of.get(channel_id)
    if parent:
        return f"https://discord.com/channels/{guild}/{parent}/threads/{channel_id}", "thread", guild
    return f"https://discord.com/channels/{guild}/{channel_id}", "channel", guild


def _base_report(*, state: str, ok: bool) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "language": "ja",
        "ok": ok,
        "state": state,
        "record_schema": RECORD_SCHEMA,
        "raw_text_returned": False,
        "participant_names_returned": False,
        "discord_ids_returned": False,
        "local_paths_returned": False,
        "snapshot_store_written": False,
        "outbound_actions": "disabled",
    }


def _prepare_out_dir(out_dir: Path) -> str | None:
    """out_dir を再生成可能な出力先として準備する。他人のファイルがある場所には書かない。"""
    if out_dir.exists() and not out_dir.is_dir():
        return "out_dir_not_directory"
    if out_dir.is_dir() and any(out_dir.iterdir()):
        report_path = out_dir / "report.json"
        try:
            previous = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return "out_dir_not_empty"
        if not isinstance(previous, dict) or previous.get("schema") != REPORT_SCHEMA:
            return "out_dir_not_empty"
        records_dir = out_dir / "records"
        if records_dir.is_dir():
            for stale in records_dir.glob("*.ndjson"):
                stale.unlink()
    return None


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    temp.replace(path)


def _dumps(payload: Any, *, indent: int | None = None) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=indent)


def export_desktop_cache_records(
    *,
    cache_root: Path,
    out_dir: Path,
    snapshot_store_hint: Path | None = None,
    include_dm: bool = False,
    include_labels: bool = False,
    max_files: int = 200_000,
) -> dict[str, Any]:
    """Desktop cache の messages 応答を visible_message_record.v1 の NDJSON に変換して書く。

    戻り値は metadata-only の report (本文・参加者名・Discord ID・path を含まない)。
    同じ内容を `out_dir/report.json` にも書く。
    """
    if not cache_root.is_dir():
        return _base_report(state="cache_missing", ok=False)

    counters = {
        "file_count": 0,
        "api_entry_count": 0,
        "message_entry_count": 0,
        "parsed_message_entry_count": 0,
        "unsupported_format_count": 0,
        "unsupported_encoding_count": 0,
        "non_json_count": 0,
        "corrupt_count": 0,
    }
    channel_map = _ChannelMap()
    candidates: dict[str, list[_Candidate]] = {}
    scan_complete = True

    for path in sorted(cache_root.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name == "index":
            continue
        if counters["file_count"] >= max_files:
            scan_complete = False
            break
        counters["file_count"] += 1
        try:
            key = _read_simple_cache_key(path)
        except UnsupportedCacheFormat:
            counters["unsupported_format_count"] += 1
            continue
        except (CacheEntryError, OSError):
            counters["corrupt_count"] += 1
            continue
        if "/api/" not in key:
            continue
        counters["api_entry_count"] += 1
        key_path = key.rsplit(" ", 1)[-1].split("?", 1)[0]
        messages_match = _MESSAGES_PATH_RE.search(key_path)
        if messages_match:
            counters["message_entry_count"] += 1
        try:
            parts = _read_simple_cache_entry_parts(path)
            payload = _decode_simple_cache_body(parts.body, parts.response_metadata)
        except UnsupportedEncoding:
            counters["unsupported_encoding_count"] += 1
            continue
        except CacheEntryError:
            counters["non_json_count"] += 1
            continue
        except (OSError, gzip.BadGzipFile, EOFError):
            counters["corrupt_count"] += 1
            continue

        _collect_channel_metadata(key_path, payload, channel_map)
        if not messages_match or not isinstance(payload, list):
            continue
        counters["parsed_message_entry_count"] += 1
        observed_at = _response_observed_at(parts.response_metadata, path)
        for item in payload:
            if _is_message_object(item):
                candidates.setdefault(str(item["id"]), []).append(
                    _Candidate(message=item, observed_at=observed_at, entry_name=path.name)
                )

    snapshot_hint_mappings = 0
    if snapshot_store_hint is not None:
        snapshot_hint_mappings = _load_snapshot_url_hints(snapshot_store_hint, channel_map)

    exclusions: dict[str, int] = {}
    by_channel: dict[str, list[_Candidate]] = {}
    channel_excluded: dict[str, int] = {}
    for message_id in sorted(candidates):
        chosen = max(candidates[message_id], key=_Candidate.rank)
        channel_id = str(chosen.message["channel_id"])
        _url, kind, _guild = _channel_url(channel_id, channel_map)
        reason: str | None
        if kind == "dm" and not include_dm:
            reason = "dm_channel"
        elif kind == "server_unknown":
            reason = "server_unknown"
        else:
            reason = _exclusion_reason(chosen.message)
        if reason:
            exclusions[reason] = exclusions.get(reason, 0) + 1
            channel_excluded[channel_id] = channel_excluded.get(channel_id, 0) + 1
            by_channel.setdefault(channel_id, [])
            continue
        by_channel.setdefault(channel_id, []).append(chosen)

    prepare_error = _prepare_out_dir(out_dir)
    if prepare_error:
        return _base_report(state=prepare_error, ok=False)

    outputs: list[dict[str, Any]] = []
    servers: dict[str, dict[str, Any]] = {}
    unknown = {"channel_count": 0, "message_count": 0}
    dm_summary = {"channel_count": 0, "message_count": 0, "included": include_dm}
    channel_rows: list[dict[str, Any]] = []
    emitted_total = 0

    for channel_id in sorted(by_channel, key=lambda value: (len(value), value)):
        chosen_messages = sorted(
            by_channel[channel_id], key=lambda cand: (cand.message["timestamp"], int(cand.message["id"]))
        )
        url, kind, guild = _channel_url(channel_id, channel_map)
        excluded_count = channel_excluded.get(channel_id, 0)
        if kind == "server_unknown":
            unknown["channel_count"] += 1
            unknown["message_count"] += excluded_count
        if kind == "dm":
            dm_summary["channel_count"] += 1
            dm_summary["message_count"] += len(chosen_messages) + excluded_count
        status = {
            "server_unknown": "server_unknown",
            "dm": "dm_included" if include_dm else "dm_excluded",
        }.get(kind, "resolved")
        target_key = target_key_for_url(url) if url else None
        channel_rows.append(
            {
                "channel_id": channel_id,
                "guild_id": guild,
                "parent_id": channel_map.parent_of.get(channel_id),
                "kind": kind,
                "status": status,
                "url": url,
                "target_key": target_key,
                "label": channel_map.channel_names.get(channel_id, ""),
                "emitted_count": len(chosen_messages),
                "excluded_count": excluded_count,
            }
        )
        if not chosen_messages or not url or not target_key:
            continue

        captured_at = max(cand.observed_at for cand in chosen_messages)
        title = channel_map.channel_names.get(channel_id, "")
        rows: list[dict[str, Any]] = []
        for ordinal, cand in enumerate(chosen_messages):
            message = cand.message
            reference = message.get("message_reference") if isinstance(message.get("message_reference"), dict) else {}
            rows.append(
                {
                    "schema": RECORD_SCHEMA,
                    "target_key": target_key,
                    "url": url,
                    "title": title,
                    "capture_id": stable_text_hash(f"{RECORD_SOURCE}|{target_key}|{captured_at}"),
                    "ordinal": ordinal,
                    "message_id": str(message["id"]),
                    "author_label": _author_label(message["author"]),
                    "visible_timestamp": message["timestamp"],
                    "edited_timestamp": message.get("edited_timestamp") or "",
                    "body_text": str(message.get("content") or ""),
                    "attachment_count": len(message.get("attachments") or []),
                    "reply_to_message_id": str(reference.get("message_id") or ""),
                    "captured_at": captured_at,
                    "source": RECORD_SOURCE,
                    "private_local_only": True,
                    "outbound_actions": "disabled",
                }
            )
        chunks = [rows[index : index + MAX_MESSAGES] for index in range(0, len(rows), MAX_MESSAGES)]
        server_key = _server_key(guild) if guild else "dm"
        for chunk_index, chunk in enumerate(chunks, start=1):
            name = f"{target_key}.ndjson" if len(chunks) == 1 else f"{target_key}.part{chunk_index:03d}.ndjson"
            _write_text(out_dir / "records" / name, "".join(_dumps(row) + "\n" for row in chunk))
            outputs.append(
                {
                    "file": f"records/{name}",
                    "target_key": target_key,
                    "record_count": len(chunk),
                    "server_key": server_key,
                    "kind": kind,
                }
            )
        emitted_total += len(rows)
        if guild:
            server = servers.setdefault(
                server_key, {"server_key": server_key, "channel_count": 0, "message_count": 0}
            )
            server["channel_count"] += 1
            server["message_count"] += len(rows)
            if include_labels and guild in channel_map.guild_names:
                server["label"] = channel_map.guild_names[guild]

    report = _base_report(state="records_written" if outputs else "no_records", ok=True)
    report.update(
        {
            "cache": {"scan_complete": scan_complete, "max_files": max_files, **counters},
            "messages": {
                "unique_seen": len(candidates),
                "emitted": emitted_total,
                "excluded": sum(exclusions.values()),
            },
            "exclusions": dict(sorted(exclusions.items())),
            "servers": [servers[key] for key in sorted(servers)],
            "server_unknown": unknown,
            "dm": dm_summary,
            "channel_mapping": {
                "sources": dict(sorted(channel_map.sources.items())),
                "conflicts": channel_map.conflicts,
                "snapshot_store_hint_used": snapshot_store_hint is not None,
                "snapshot_store_hint_mappings": snapshot_hint_mappings,
            },
            "outputs": sorted(outputs, key=lambda item: item["file"]),
        }
    )
    channel_map_payload = {
        "schema": CHANNEL_MAP_SCHEMA,
        "private_local_only": True,
        "guilds": [
            {
                "guild_id": guild_id,
                "server_key": _server_key(guild_id),
                "label": channel_map.guild_names.get(guild_id, ""),
            }
            for guild_id in sorted({row["guild_id"] for row in channel_rows if row["guild_id"]})
        ],
        "channels": channel_rows,
    }
    _write_text(out_dir / "channel-map.json", _dumps(channel_map_payload, indent=2) + "\n")
    _write_text(out_dir / "report.json", _dumps(report, indent=2) + "\n")
    return report


__all__ = ["export_desktop_cache_records", "REPORT_SCHEMA", "CHANNEL_MAP_SCHEMA"]

