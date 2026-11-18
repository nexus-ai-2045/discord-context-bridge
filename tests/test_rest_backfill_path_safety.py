from pathlib import Path, PurePosixPath
import json

import pytest

from discord_context_bridge.core import rest_backfill_config_safety


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shared = tmp_path / "Documents" / "discord" / "raw-snapshots"
    shared.mkdir(parents=True)
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(shared))
    return tmp_path, shared


def test_absolute_private_artifacts_and_external_fixture(roots):
    base, shared = roots
    fixture = base / "ordinary" / "discord-messages.json"
    fixture.parent.mkdir()
    fixture.write_text("[]")
    result = rest_backfill_config_safety(
        fixture_input=fixture,
        raw_output=shared / "123" / "discord-rest.ndjson",
        manifest_output=base / ".local/discord-context-bridge/inbox/manifests/rest.json",
    )
    assert result["ok"]
    assert str(base) not in json.dumps(result)


def test_default_relative_outputs_and_cross_argument_names(roots):
    base, _ = roots
    fixture = base / "chrome-fixture.json"
    fixture.write_text("[]")
    assert rest_backfill_config_safety(
        fixture_input=fixture,
        raw_output=Path(".local/discord-context-bridge/inbox/raw/rest.ndjson"),
        manifest_output=Path(".local/discord-context-bridge/inbox/manifests/rest.json"),
    )["ok"]


def test_outside_output_and_symlink_escape_are_rejected(roots):
    base, shared = roots
    outside = base / "outside"
    outside.mkdir()
    (shared / "escape").symlink_to(outside, target_is_directory=True)
    for output in (outside / "rest.json", shared / "escape/rest.json"):
        result = rest_backfill_config_safety(raw_output=output)
        assert "private_output_root_required" in result["blockers"]
        assert str(output) not in json.dumps(result)


@pytest.mark.parametrize("relative", [
    "Library/Application Support/discord/Local Storage/leveldb/000001.ldb",
    "Library/Application Support/Google/Chrome/Default/Login Data",
    ".config/discord/settings.json",
    "AppData/Roaming/discord/Local Storage/leveldb/000001.ldb",
    "secret/.env",
    "secret/.env.production",
    "secret/storage_state.json",
    "secret/storage-state.json",
    "secret/Keychains/login.keychain-db",
])
def test_credential_sources_and_aliases_are_rejected(roots, relative):
    base, _ = roots
    credential = base / relative
    credential.parent.mkdir(parents=True)
    credential.write_text("synthetic")
    alias = base / "ordinary.json"
    alias.symlink_to(credential)
    for fixture in (credential, alias):
        result = rest_backfill_config_safety(fixture_input=fixture)
        assert not result["ok"]
        assert "credential_bearing_local_path_in_config" in result["blockers"]
        assert str(base) not in json.dumps(result)


def test_legacy_string_api_still_blocks_absolute_discord_path():
    # Build the synthetic home-style path from parts at runtime so no literal
    # local absolute path is committed; the legacy heuristic still sees one.
    for home_root in ("Users", "home"):
        legacy = str(PurePosixPath("/", home_root, "example", "discord", "capture.json"))
        result = rest_backfill_config_safety(legacy)
        assert not result["ok"]
        assert "credential_bearing_local_path_in_config" in result["blockers"]
        assert legacy not in json.dumps(result)


def test_malformed_local_config_fails_closed(roots, monkeypatch):
    base, _ = roots
    config = base / "config.json"
    config.write_text("invalid")
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT")
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_CONFIG", str(config))
    result = rest_backfill_config_safety(raw_output=Path(".local/discord-context-bridge/raw.json"))
    assert result["blockers"] == ["private_path_resolution_failed"]


@pytest.mark.parametrize("anchor", [".local", ".local/discord-context-bridge"])
def test_scratch_root_symlink_relocation_is_rejected(roots, anchor):
    base, _ = roots
    outside = base / "outside"
    outside.mkdir()
    link = base / anchor
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    result = rest_backfill_config_safety(
        raw_output=base / ".local/discord-context-bridge/inbox/raw/rest.json",
    )
    assert "private_output_root_required" in result["blockers"]
