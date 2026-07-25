import json

import pytest

from discord_context_bridge.target_registry import (
    VALID_KEY_SCHEMES,
    VALID_SOURCES,
    load_target_registry,
    register_target,
    resolve_target,
    safe_target_label,
)


def test_register_target_appends_one_entry(tmp_path):
    path = tmp_path / "targets.ndjson"
    result = register_target(
        target_key="abc123",
        key_scheme="url_hash_16",
        url="https://discord.com/channels/1/2/3",
        server_label="example-community",
        channel_label="planning",
        path=path,
    )
    assert result["registered"] is True

    entries = load_target_registry(path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["schema"] == "dcb.target_registry_entry.v1"
    assert entry["target_key"] == "abc123"
    assert entry["key_scheme"] == "url_hash_16"
    assert entry["url"] == "https://discord.com/channels/1/2/3"
    assert entry["server_label"] == "example-community"
    assert entry["channel_label"] == "planning"
    assert entry["aliases"] == []
    assert entry["source"] == "capture"
    assert entry["source_ref"] is None
    assert entry["first_seen"]


def test_register_target_same_url_is_skipped(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)
    result = register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)
    assert result["registered"] is False
    assert result["reason"] == "already_registered"
    assert len(load_target_registry(path)) == 1


def test_register_target_new_alias_appends_supplement_event(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)
    result = register_target(
        target_key="abc123",
        key_scheme="url_hash_16",
        url="https://x",
        aliases=["旧チャンネル名"],
        path=path,
    )
    assert result["registered"] is True
    assert len(load_target_registry(path)) == 2

    resolved = resolve_target("abc123", path)
    assert resolved["aliases"] == ["旧チャンネル名"]


def test_register_target_new_label_appends_supplement_event(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)
    result = register_target(
        target_key="abc123",
        key_scheme="url_hash_16",
        url="https://x",
        channel_label="renamed-channel",
        path=path,
    )
    assert result["registered"] is True
    resolved = resolve_target("abc123", path)
    assert resolved["channel_label"] == "renamed-channel"


def test_resolve_target_unknown_returns_none(tmp_path):
    path = tmp_path / "targets.ndjson"
    assert resolve_target("unknown", path) is None


def test_register_target_rejects_unknown_key_scheme(tmp_path):
    path = tmp_path / "targets.ndjson"
    with pytest.raises(ValueError):
        register_target(target_key="abc", key_scheme="not_a_scheme", path=path)


def test_register_target_rejects_unknown_source(tmp_path):
    path = tmp_path / "targets.ndjson"
    with pytest.raises(ValueError):
        register_target(target_key="abc", key_scheme="url_hash_16", source="not_a_source", path=path)


def test_valid_key_schemes_and_sources_match_spec():
    assert VALID_KEY_SCHEMES == {
        "url_hash_16",
        "title_fallback_16",
        "content_hash_24",
        "source_url_hash_64",
    }
    assert VALID_SOURCES == {"capture", "backfill", "manual"}


def test_register_target_without_url_uses_title_fallback(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(
        target_key="title-key",
        key_scheme="title_fallback_16",
        channel_label="general",
        path=path,
    )
    resolved = resolve_target("title-key", path)
    assert resolved["url"] is None
    assert resolved["channel_label"] == "general"


def test_safe_target_label_never_includes_url(tmp_path):
    path = tmp_path / "targets.ndjson"
    url = "https://discord.com/channels/999/888/777"
    register_target(
        target_key="abc123",
        key_scheme="url_hash_16",
        url=url,
        channel_label="general",
        path=path,
    )
    label = safe_target_label("abc123", path)
    assert url not in label
    assert label == "general"


def test_safe_target_label_falls_back_to_target_key(tmp_path):
    path = tmp_path / "targets.ndjson"
    assert safe_target_label("unknown-key", path) == "unknown-key"


def test_load_target_registry_missing_file_returns_empty(tmp_path):
    path = tmp_path / "does-not-exist.ndjson"
    assert load_target_registry(path) == []


def test_entries_are_ndjson_lines(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    json.loads(lines[0])
