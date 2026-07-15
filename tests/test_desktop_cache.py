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


def _write_entry(path: Path, *, key: str, payload: object, gzip_body: bool = False) -> None:
    key_bytes = key.encode("utf-8")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if gzip_body:
        body = gzip.compress(body, mtime=0)
    metadata = b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
    header = SIMPLE_HEADER.pack(SIMPLE_INITIAL_MAGIC, SIMPLE_VERSION, len(key_bytes), 1, 0)
    stream_one_eof = struct.pack("<QIIII", SIMPLE_FINAL_MAGIC, 0, 0, 0, 0)
    stream_zero_eof = struct.pack("<QIIII", SIMPLE_FINAL_MAGIC, FLAG_HAS_KEY_SHA256, 0, len(metadata), 0)
    path.write_bytes(header + key_bytes + body + stream_one_eof + metadata + (b"x" * 32) + stream_zero_eof)


def test_resolve_user_data_dir_uses_running_process_then_platform_default(tmp_path: Path) -> None:
    running = tmp_path / "running-discord"
    running.mkdir()
    resolved = resolve_discord_user_data_dir(
        platform_name="darwin",
        home=tmp_path,
        process_commands=[f"Discord --user-data-dir={running} --lang=ja"],
    )
    assert (resolved.path, resolved.source) == (running, "running_process")

    default = tmp_path / "Library" / "Application Support" / "discord"
    default.mkdir(parents=True)
    resolved_default = resolve_discord_user_data_dir(
        platform_name="darwin", home=tmp_path, process_commands=[]
    )
    assert (resolved_default.path, resolved_default.source) == (default, "platform_default")


def test_probe_extracts_labels_only_when_explicitly_requested(tmp_path: Path) -> None:
    user_data = tmp_path / "discord"
    cache = user_data / "Cache" / "Cache_Data"
    cache.mkdir(parents=True)
    _write_entry(
        cache / "fixture",
        key=f"https://discord.com/api/v9/guilds/{GUILD_ID}/channels",
        payload=[{
            "id": CHANNEL_ID,
            "type": 15,
            "name": "matching-board",
            "available_tags": [{"id": "1", "name": "勉強会"}],
        }],
        gzip_body=True,
    )

    safe = probe_discord_desktop_cache(url=DISCORD_URL, explicit_user_data_dir=user_data)
    private = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=user_data,
        include_labels=True,
    )
    rendered = json.dumps(safe, ensure_ascii=False)

    assert safe["state"] == "metadata_found"
    assert safe["metadata"]["channel_label_count"] == 1
    assert "channel_labels" not in safe["metadata"]
    assert private["metadata"]["channel_labels"] == ["matching-board"]
    assert private["metadata"]["tag_labels"] == ["勉強会"]
    assert GUILD_ID not in rendered
    assert CHANNEL_ID not in rendered
    assert str(tmp_path) not in rendered


def test_probe_reports_reference_only_without_returning_message_body(tmp_path: Path) -> None:
    user_data = tmp_path / "discord"
    cache = user_data / "Cache" / "Cache_Data"
    cache.mkdir(parents=True)
    _write_entry(
        cache / "fixture",
        key=f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages",
        payload={"channel_id": CHANNEL_ID, "content": "private fixture"},
    )
    payload = probe_discord_desktop_cache(url=DISCORD_URL, explicit_user_data_dir=user_data)
    assert payload["state"] == "reference_only"
    assert payload["raw_text_returned"] is False
    assert "private fixture" not in json.dumps(payload)


def test_probe_stops_at_file_budget_and_returns_partial_scan(tmp_path: Path) -> None:
    user_data = tmp_path / "discord"
    cache = user_data / "Cache" / "Cache_Data"
    cache.mkdir(parents=True)
    _write_entry(cache / "first", key="https://example.invalid/not-target", payload={"ok": True})
    _write_entry(
        cache / "second",
        key=f"https://discord.com/api/v9/channels/{CHANNEL_ID}/messages",
        payload={"channel_id": CHANNEL_ID, "content": "private fixture"},
    )

    payload = probe_discord_desktop_cache(
        url=DISCORD_URL,
        explicit_user_data_dir=user_data,
        max_files=1,
    )

    assert payload["ok"] is True
    assert payload["state"] == "partial_scan"
    assert payload["cache"]["scan_complete"] is False
    assert payload["cache"]["scan_stop_reason"] == "max_files_reached"
    assert "private fixture" not in json.dumps(payload)
