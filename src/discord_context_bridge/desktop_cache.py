from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SIMPLE_INITIAL_MAGIC = 0xFCFB6D1BA7725C30
SIMPLE_FINAL_MAGIC = 0xF4FA6F45970D41D8
SIMPLE_VERSION = 5
SIMPLE_HEADER = struct.Struct("<QIIII")
SIMPLE_EOF = struct.Struct("<QIIII")
FLAG_HAS_KEY_SHA256 = 1 << 1

DISCORD_URL_RE = re.compile(
    r"^https://discord\.com/channels/(?P<guild_id>\d{17,20})/(?P<channel_id>\d{17,20})(?:/\d{17,20})?/?$"
)
USER_DATA_DIR_RE = re.compile(r"--user-data-dir=(.*?)(?=\s+--[A-Za-z0-9-]+(?:=|\s)|$)")


class CacheEntryError(ValueError):
    pass


class UnsupportedCacheFormat(CacheEntryError):
    pass


class UnsupportedEncoding(CacheEntryError):
    pass


@dataclass(frozen=True)
class ResolvedUserDataDir:
    path: Path | None
    source: str


@dataclass(frozen=True)
class ParsedSimpleCacheEntry:
    key: str
    payload: Any


@dataclass(frozen=True)
class SimpleCacheEntryParts:
    key: str
    body: bytes
    response_metadata: bytes


def _platform_family(platform_name: str) -> str:
    if platform_name.startswith("win"):
        return "windows"
    if platform_name == "darwin":
        return "macos"
    return "linux"


def _running_process_commands(platform_name: str) -> list[str]:
    try:
        if platform_name.startswith("win"):
            command = [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -like 'Discord*.exe' } | "
                    "Select-Object -ExpandProperty CommandLine"
                ),
            ]
        else:
            command = ["ps", "-axo", "command="]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return completed.stdout.splitlines()


def _process_user_data_dirs(commands: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for command in commands:
        if "discord" not in command.lower() or "--user-data-dir=" not in command:
            continue
        match = USER_DATA_DIR_RE.search(command)
        if not match:
            continue
        value = match.group(1).strip().strip('"\'')
        if value:
            paths.append(Path(value).expanduser())
    return paths


def _platform_default_user_data_dir(
    *,
    platform_name: str,
    env: Mapping[str, str],
    home: Path,
) -> Path | None:
    if platform_name.startswith("win"):
        appdata = env.get("APPDATA", "").strip()
        return Path(appdata) / "discord" if appdata else None
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "discord"
    config_home = env.get("XDG_CONFIG_HOME", "").strip()
    return (Path(config_home) if config_home else home / ".config") / "discord"


def resolve_discord_user_data_dir(
    *,
    explicit: Path | None = None,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    process_commands: Sequence[str] | None = None,
) -> ResolvedUserDataDir:
    if explicit is not None:
        candidate = explicit.expanduser()
        return ResolvedUserDataDir(candidate if candidate.is_dir() else None, "explicit")

    platform_value = platform_name or sys.platform
    commands = list(process_commands) if process_commands is not None else _running_process_commands(platform_value)
    for candidate in _process_user_data_dirs(commands):
        if candidate.is_dir():
            return ResolvedUserDataDir(candidate, "running_process")

    env_value = env if env is not None else os.environ
    home_value = home if home is not None else Path.home()
    candidate = _platform_default_user_data_dir(
        platform_name=platform_value,
        env=env_value,
        home=home_value,
    )
    if candidate is not None and candidate.is_dir():
        return ResolvedUserDataDir(candidate, "platform_default")
    return ResolvedUserDataDir(None, "unresolved")


def _decode_simple_cache_body(body: bytes, response_metadata: bytes) -> Any:
    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    elif b"content-encoding: br" in response_metadata.lower():
        raise UnsupportedEncoding("brotli")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CacheEntryError("body_not_json") from exc


def _read_simple_cache_entry_parts(path: Path) -> SimpleCacheEntryParts:
    data = path.read_bytes()
    minimum_size = SIMPLE_HEADER.size + (2 * SIMPLE_EOF.size)
    if len(data) < minimum_size:
        raise CacheEntryError("entry_too_short")

    initial_magic, version, key_length, _key_hash, _padding = SIMPLE_HEADER.unpack_from(data, 0)
    if initial_magic != SIMPLE_INITIAL_MAGIC or version != SIMPLE_VERSION:
        raise UnsupportedCacheFormat("simple_cache_v5_required")
    key_start = SIMPLE_HEADER.size
    key_end = key_start + key_length
    if key_end > len(data) - (2 * SIMPLE_EOF.size):
        raise CacheEntryError("invalid_key_length")

    final_magic, flags, _crc32, stream_zero_size, _eof_padding = SIMPLE_EOF.unpack_from(
        data, len(data) - SIMPLE_EOF.size
    )
    if final_magic != SIMPLE_FINAL_MAGIC:
        raise CacheEntryError("invalid_final_eof")
    sha_size = 32 if flags & FLAG_HAS_KEY_SHA256 else 0
    stream_zero_start = len(data) - SIMPLE_EOF.size - sha_size - stream_zero_size
    stream_one_eof_start = stream_zero_start - SIMPLE_EOF.size
    if stream_one_eof_start < key_end:
        raise CacheEntryError("invalid_stream_boundaries")
    stream_one_magic, *_ = SIMPLE_EOF.unpack_from(data, stream_one_eof_start)
    if stream_one_magic != SIMPLE_FINAL_MAGIC:
        raise CacheEntryError("invalid_stream_one_eof")

    key = data[key_start:key_end].decode("utf-8", errors="replace")
    body = data[key_end:stream_one_eof_start]
    response_metadata = data[stream_zero_start : stream_zero_start + stream_zero_size]
    return SimpleCacheEntryParts(key=key, body=body, response_metadata=response_metadata)


def parse_simple_cache_entry(path: Path) -> ParsedSimpleCacheEntry:
    parts = _read_simple_cache_entry_parts(path)
    payload = _decode_simple_cache_body(parts.body, parts.response_metadata)
    return ParsedSimpleCacheEntry(key=parts.key, payload=payload)


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def probe_discord_desktop_cache(
    *,
    url: str,
    explicit_user_data_dir: Path | None = None,
    include_labels: bool = False,
    platform_name: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    process_commands: Sequence[str] | None = None,
) -> dict[str, Any]:
    match = DISCORD_URL_RE.match(url)
    if not match:
        return _result_envelope(
            state="invalid_discord_url",
            ok=False,
            platform_name=platform_name or sys.platform,
            source="unresolved",
            user_data_dir_resolved=False,
        )
    guild_id = match.group("guild_id")
    channel_id = match.group("channel_id")
    resolved = resolve_discord_user_data_dir(
        explicit=explicit_user_data_dir,
        platform_name=platform_name,
        env=env,
        home=home,
        process_commands=process_commands,
    )
    platform_value = platform_name or sys.platform
    if resolved.path is None:
        return _result_envelope(
            state="user_data_dir_unresolved",
            ok=False,
            platform_name=platform_value,
            source=resolved.source,
            user_data_dir_resolved=False,
        )

    cache_root = resolved.path / "Cache" / "Cache_Data"
    if not cache_root.is_dir():
        return _result_envelope(
            state="cache_missing",
            ok=False,
            platform_name=platform_value,
            source=resolved.source,
            user_data_dir_resolved=True,
        )

    counters = {
        "file_count": 0,
        "candidate_count": 0,
        "parsed_count": 0,
        "corrupt_count": 0,
        "unsupported_format_count": 0,
        "unsupported_encoding_count": 0,
        "non_json_count": 0,
        "reference_count": 0,
    }
    channel_objects: list[dict[str, Any]] = []
    for path in cache_root.iterdir():
        if not path.is_file() or path.name == "index":
            continue
        counters["file_count"] += 1
        try:
            parts = _read_simple_cache_entry_parts(path)
        except UnsupportedCacheFormat:
            counters["unsupported_format_count"] += 1
            continue
        except (CacheEntryError, OSError):
            counters["corrupt_count"] += 1
            continue

        if guild_id not in parts.key and channel_id not in parts.key:
            continue
        counters["candidate_count"] += 1
        try:
            payload = _decode_simple_cache_body(parts.body, parts.response_metadata)
        except UnsupportedEncoding:
            counters["unsupported_encoding_count"] += 1
            continue
        except CacheEntryError:
            counters["non_json_count"] += 1
            continue
        except (OSError, gzip.BadGzipFile):
            counters["corrupt_count"] += 1
            continue
        counters["parsed_count"] += 1
        referenced = channel_id in parts.key
        for item in _walk_dicts(payload):
            if str(item.get("channel_id", "")) == channel_id:
                referenced = True
            if str(item.get("id", "")) == channel_id and any(
                key in item for key in ("type", "name", "available_tags")
            ):
                channel_objects.append(item)
        if referenced:
            counters["reference_count"] += 1

    channel_types = sorted(
        {item.get("type") for item in channel_objects if isinstance(item.get("type"), int)}
    )
    channel_names = sorted(
        {str(item.get("name", "")).strip() for item in channel_objects if str(item.get("name", "")).strip()}
    )
    tag_names = sorted(
        {
            str(tag.get("name", "")).strip()
            for item in channel_objects
            for tag in (item.get("available_tags") or [])
            if isinstance(tag, dict) and str(tag.get("name", "")).strip()
        }
    )
    if channel_objects:
        state = "metadata_found"
    elif counters["reference_count"]:
        state = "reference_only"
    elif counters["file_count"] and counters["unsupported_format_count"] == counters["file_count"]:
        state = "unsupported_cache_format"
    elif counters["candidate_count"] and counters["unsupported_encoding_count"] == counters["candidate_count"]:
        state = "unsupported_encoding"
    else:
        state = "cache_missing"

    metadata: dict[str, Any] = {
        "channel_object_count": len(channel_objects),
        "channel_types": channel_types,
        "channel_label_count": len(channel_names),
        "tag_count": len(tag_names),
        "labels_included": include_labels,
    }
    if include_labels:
        metadata["channel_labels"] = channel_names
        metadata["tag_labels"] = tag_names
    result = _result_envelope(
        state=state,
        ok=state in {"metadata_found", "reference_only"},
        platform_name=platform_value,
        source=resolved.source,
        user_data_dir_resolved=True,
    )
    result["target"] = {
        "target_key": hashlib.sha256(url.encode("utf-8")).hexdigest()[:16],
        "url_present": True,
        "url_output": "omitted",
    }
    result["cache"] = {"present": True, **counters}
    result["metadata"] = metadata
    return result


def _result_envelope(
    *,
    state: str,
    ok: bool,
    platform_name: str,
    source: str,
    user_data_dir_resolved: bool,
) -> dict[str, Any]:
    return {
        "language": "ja",
        "schema": "discord_desktop_cache_probe.v1",
        "ok": ok,
        "state": state,
        "message": "Discord Desktop cache probe を実行しました。",
        "platform": _platform_family(platform_name),
        "user_data_dir": {
            "resolved": user_data_dir_resolved,
            "source": source,
            "path_output": "omitted",
        },
        "raw_text_returned": False,
        "participant_names_returned": False,
        "discord_ids_returned": False,
        "local_paths_returned": False,
        "outbound_actions": "disabled",
    }
