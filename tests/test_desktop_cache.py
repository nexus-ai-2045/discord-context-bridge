from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

from discord_context_bridge.desktop_cache import (
    FLAG_HAS_KEY_SHA256,
    SIMPLE_FINAL_MAGIC,
    SIMPLE_HEADER,
    SIMPLE_INITIAL_MAGIC,
    SIMPLE_VERSION,
    probe_discord_desktop_cache,
    resolve_discord_user_data_dir,
)


GUILD_ID = "12345678901234567"
CHANNEL_ID = "22345678901234567"
DISCORD_URL = f"https://discord.com/channels/{GUILD_ID}/{CHANNEL_ID}"


def _write_simple_cache_entry(
    path: Path,
    *,
    key: str,
    payload: object,
    gzip_body: bool = False,
    content_encoding: str = "",
    include_key_sha256: bool = False,
) -> None:
    key_bytes = key.encode("utf-8")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if gzip_body:
        body = gzip.compress(body, mtime=0)
        content_encoding = "gzip"
    response_metadata = (
        "HTTP/1.1 200 OK\r\n"
        "content-type: application/json\r\n"
        f"content-encoding: {content_encoding}\r\n"
    ).encode("ascii")
    header = SIMPLE_HEADER.pack(SIMPLE_INITIAL_MAGIC, SIMPLE_VERSION, len(key_bytes), 1, 0)
    stream_one_eof = struct.pack("<QIIII", SIMPLE_FINAL_MAGIC, 0, 0, 0, 0)
    flags = FLAG_HAS_KEY_SHA256 if include_key_sha256 else 0
    key_sha256 = b"x" * 32 if include_key_sha256 else b""
    stream_zero_eof = struct.pack(
        "<QIIII",
        SIMPLE_FINAL_MAGIC,
        flags,
        0,
        len(response_metadata),
        0,
    )
    path.write_bytes(header + key_bytes + body + stream_one_eof + response_metadata + key_sha256 + stream_zero_eof)


def test_resolve_user_data_dir_prefers_running_macos_process(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "Application Support" / "discord"
    user_data_dir.mkdir(parents=True)
    command = f"/Applications/Discord.app/Contents/MacOS/Discord --user-data-dir={user_data_dir} --lang=en-US"

    resolved = resolve_discord_user_data_dir(
        platform_name="darwin",
        home=tmp_path,
        process_commands=[command],
    )

    assert resolved.path == user_data_dir
    assert resolved.source == "running_process"


def test_resolve_user_data_dir_uses_windows_appdata(tmp_path: Path) -> None:
    appdata = tmp_path / "Roaming"
    user_data_dir = appdata / "discord"
    user_data_dir.mkdir(parents=True)

    resolved = resolve_discord_user_data_dir(
        platform_name="win32",
        env={"APPDATA": str(appdata)},
        home=tmp_path,
        process_commands=[],
    )

    assert resolved.path == user_data_dir
    assert resolved.source == "platform_default"


def test_probe_marks_missing_explicit_user_data_dir_unresolved(tmp_path: Path) -> None:
    result = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=tmp_path / "missing",
    )

    assert result["state"] == "user_data_dir_unresolved"
    assert result["user_data_dir"] == {
        "resolved": False,
        "source": "explicit",
        "path_output": "omitted",
    }


def test_probe_extracts_channel_and_tag_labels_only_when_requested(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "discord"
    cache_root = user_data_dir / "Cache" / "Cache_Data"
    cache_root.mkdir(parents=True)
    payload = [
        {
            "id": CHANNEL_ID,
            "type": 15,
            "name": "matching-board",
            "available_tags": [{"id": "1", "name": "仲間募集"}, {"id": "2", "name": "仕事相談"}],
        }
    ]
    _write_simple_cache_entry(
        cache_root / "fixture_0",
        key=f"1/0/https://discord.com/api/v9/guilds/{GUILD_ID}/channels",
        payload=payload,
        gzip_body=True,
        include_key_sha256=True,
    )

    private_result = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=user_data_dir,
        include_labels=True,
    )
    safe_result = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=user_data_dir,
        include_labels=False,
    )

    assert private_result["state"] == "metadata_found"
    assert private_result["metadata"]["channel_types"] == [15]
    assert private_result["metadata"]["channel_labels"] == ["matching-board"]
    assert private_result["metadata"]["tag_labels"] == ["仕事相談", "仲間募集"]
    assert safe_result["metadata"]["tag_count"] == 2
    assert "channel_labels" not in safe_result["metadata"]
    assert "tag_labels" not in safe_result["metadata"]
    serialized = json.dumps(safe_result, ensure_ascii=False)
    assert GUILD_ID not in serialized
    assert CHANNEL_ID not in serialized
    assert str(user_data_dir) not in serialized


def test_probe_reports_reference_only_for_cached_messages(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "discord"
    cache_root = user_data_dir / "Cache" / "Cache_Data"
    cache_root.mkdir(parents=True)
    _write_simple_cache_entry(
        cache_root / "fixture_0",
        key=f"1/0/https://discord.com/api/v9/channels/{CHANNEL_ID}/messages",
        payload={"id": "32345678901234567", "channel_id": CHANNEL_ID, "content": "private fixture"},
    )

    result = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=user_data_dir,
    )

    assert result["state"] == "reference_only"
    assert result["ok"] is True
    assert result["metadata"]["channel_object_count"] == 0
    assert result["raw_text_returned"] is False


def test_probe_reports_unsupported_encoding_without_leaking_body(tmp_path: Path) -> None:
    user_data_dir = tmp_path / "discord"
    cache_root = user_data_dir / "Cache" / "Cache_Data"
    cache_root.mkdir(parents=True)
    _write_simple_cache_entry(
        cache_root / "fixture_0",
        key=f"1/0/https://discord.com/api/v9/channels/{CHANNEL_ID}",
        payload={"private": "do not return"},
        content_encoding="br",
    )

    result = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=user_data_dir,
    )

    assert result["state"] == "unsupported_encoding"
    assert result["ok"] is False
    assert result["raw_text_returned"] is False
