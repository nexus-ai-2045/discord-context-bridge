import json

import pytest

from discord_context_bridge.site_adapter_runtime import validate_artifact
from discord_context_bridge.target_registry import (
    VALID_KEY_SCHEMES,
    VALID_SOURCES,
    load_target_registry,
    register_target,
    resolve_target,
    safe_target_label,
)

SCHEMA_NAME = "dcb_target_registry_entry.v1.schema.json"


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
        "content_fallback_16",
    }
    assert VALID_SOURCES == {"capture", "backfill", "manual"}


def test_register_target_with_content_fallback_scheme_matches_schema(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(
        target_key="content-fallback-key",
        key_scheme="content_fallback_16",
        channel_label=None,
        path=path,
    )
    entry = load_target_registry(path)[0]
    assert entry["key_scheme"] == "content_fallback_16"
    validate_artifact(entry, SCHEMA_NAME)


def test_register_target_rejects_entry_failing_schema_validation(tmp_path, monkeypatch):
    """M8: append 前に schema validate する。invalid schema value は書き込みを止める。"""
    import discord_context_bridge.target_registry as target_registry_module

    path = tmp_path / "targets.ndjson"

    def _broken_validate(entry, schema_name):
        raise ValueError("schema_validation_failed")

    monkeypatch.setattr(target_registry_module, "validate_artifact", _broken_validate)

    with pytest.raises(ValueError):
        register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)
    assert load_target_registry(path) == []


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


def test_register_target_url_omission_is_not_treated_as_url_change(tmp_path):
    """P2: 既存 target に URL 登録済みの時、URL 無し candidate を「URL 変更」と誤判定して
    null-URL の補完行を無駄に追記しない (URL 省略は「更新なし」として扱う)。"""
    path = tmp_path / "targets.ndjson"
    register_target(target_key="abc123", key_scheme="url_hash_16", url="https://x", path=path)

    result = register_target(target_key="abc123", key_scheme="url_hash_16", url=None, path=path)

    assert result["registered"] is False
    assert result["reason"] == "already_registered"
    entries = load_target_registry(path)
    assert len(entries) == 1
    resolved = resolve_target("abc123", path)
    assert resolved["url"] == "https://x"


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


def test_entry_matches_schema(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(
        target_key="abc123",
        key_scheme="url_hash_16",
        url="https://x",
        server_label="example-community",
        channel_label="planning",
        aliases=["旧チャンネル"],
        source="backfill",
        source_ref="text-snapshots.ndjson",
        path=path,
    )
    entry = load_target_registry(path)[0]
    validate_artifact(entry, SCHEMA_NAME)


def test_entry_without_url_matches_schema(tmp_path):
    path = tmp_path / "targets.ndjson"
    register_target(target_key="title-key", key_scheme="title_fallback_16", path=path)
    entry = load_target_registry(path)[0]
    validate_artifact(entry, SCHEMA_NAME)


def test_load_target_registry_preserves_unicode_line_separators_in_labels(tmp_path):
    # json.dumps(ensure_ascii=False) は U+2028/U+2029/U+0085 をエスケープせず
    # 生のまま書くため、読込側が splitlines() だと ledger 1 行が分断される。
    path = tmp_path / "targets.ndjson"
    label = "before\u2028mid\u0085tail\u2029end"
    register_target(
        target_key="abc123",
        key_scheme="url_hash_16",
        url="https://x",
        channel_label=label,
        path=path,
    )

    entries = load_target_registry(path)

    assert len(entries) == 1
    assert entries[0]["channel_label"] == label
