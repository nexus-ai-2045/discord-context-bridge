import json
import os
from pathlib import Path

from discord_context_bridge.local_config import (
    build_configure_local_cache,
    classify_snapshot_store,
    default_cross_device_snapshot_store,
    promote_default_snapshot_store,
    resolve_shared_snapshot_root,
)
from discord_context_bridge.cli import main as cli_main


def test_snapshot_root_precedence_cli_env_config_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"shared_snapshot_root": str(tmp_path / "configured")}), encoding="utf-8")

    explicit = resolve_shared_snapshot_root(
        explicit=tmp_path / "explicit",
        config_path=config_path,
        env={"DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT": str(tmp_path / "environment")},
        home=tmp_path,
    )
    environment = resolve_shared_snapshot_root(
        config_path=config_path,
        env={"DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT": str(tmp_path / "environment")},
        home=tmp_path,
    )
    configured = resolve_shared_snapshot_root(config_path=config_path, env={}, home=tmp_path)
    defaulted = resolve_shared_snapshot_root(config_path=tmp_path / "missing.json", env={}, home=tmp_path)

    assert (explicit.path, explicit.source) == (tmp_path / "explicit", "cli")
    assert (environment.path, environment.source) == (tmp_path / "environment", "env")
    assert (configured.path, configured.source) == (tmp_path / "configured", "config")
    assert defaulted.source == "os_default"
    assert defaulted.path == tmp_path / "Projects" / "Documents" / "discord" / "raw-snapshots"


def test_cwd_local_scratch_is_not_shared_complete(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    classification = classify_snapshot_store(
        Path(".local/discord-context-bridge/text-snapshots.ndjson"),
        explicit_root=root,
        config_path=tmp_path / "missing.json",
        env={},
        home=tmp_path,
    )
    assert classification["cwd_local_scratch"] is True
    assert classification["shared_working_original"] is False
    assert classification["shared_save_complete"] is False
    assert classification["reason"] == "cwd_local_scratch"


def test_shared_dcb_ledger_counts_as_shared_save(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    store = default_cross_device_snapshot_store(
        explicit_root=root,
        config_path=tmp_path / "missing.json",
        env={},
        home=tmp_path,
    )
    assert store == root / ".dcb" / "text-snapshots.ndjson"
    classification = classify_snapshot_store(
        store,
        explicit_root=root,
        config_path=tmp_path / "missing.json",
        env={},
        home=tmp_path,
    )
    assert classification["shared_save_complete"] is True
    assert classification["reason"] == "shared_root"


def test_promote_default_redirects_cwd_local_to_shared_root(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    promoted, reason = promote_default_snapshot_store(
        Path(".local/discord-context-bridge/text-snapshots.ndjson"),
        explicit_root=root,
        config_path=tmp_path / "missing.json",
        env={},
        home=tmp_path,
    )
    assert reason == "shared_working_original"
    assert promoted == root / ".dcb" / "text-snapshots.ndjson"
    kept, kept_reason = promote_default_snapshot_store(tmp_path / "custom.ndjson")
    assert kept == tmp_path / "custom.ndjson"
    assert kept_reason == "explicit"


def test_configure_local_cache_is_dry_run_by_default_and_applies_atomically(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "config.json"
    root = tmp_path / "snapshots"

    dry_run = build_configure_local_cache(
        config_path=config_path,
        shared_snapshot_root=root,
        discord_desktop_user_data_dir=None,
        apply=False,
    )
    assert dry_run["dry_run"] is True
    assert dry_run["would_change"] is True
    assert not config_path.exists()

    applied = build_configure_local_cache(
        config_path=config_path,
        shared_snapshot_root=root,
        discord_desktop_user_data_dir=None,
        apply=True,
    )
    assert applied["changed"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["shared_snapshot_root"] == str(root)
    if os.name != "nt":
        assert config_path.stat().st_mode & 0o777 == 0o600
    assert str(tmp_path) not in json.dumps(applied)


def test_configure_local_cache_persists_relative_paths_as_absolute(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / "config.json"
    build_configure_local_cache(
        config_path=config_path,
        shared_snapshot_root=Path("snapshots"),
        discord_desktop_user_data_dir=Path("discord-data"),
        apply=True,
    )
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["shared_snapshot_root"] == str(tmp_path / "snapshots")
    assert payload["discord_desktop_user_data_dir"] == str(tmp_path / "discord-data")


def test_configure_local_cache_cli_dry_run_then_apply(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.json"
    snapshot_root = tmp_path / "snapshots"
    base_args = [
        "configure-local-cache",
        "--config-path",
        str(config_path),
        "--shared-snapshot-root",
        str(snapshot_root),
        "--json",
    ]

    assert cli_main(base_args) == 0
    dry_run_text = capsys.readouterr().out
    assert json.loads(dry_run_text)["dry_run"] is True
    assert not config_path.exists()
    assert str(tmp_path) not in dry_run_text

    assert cli_main([*base_args[:-1], "--apply", "--json"]) == 0
    applied_text = capsys.readouterr().out
    assert json.loads(applied_text)["changed"] is True
    assert config_path.exists()
    assert str(tmp_path) not in applied_text


def test_cache_inventory_cli_reports_malformed_config_in_human_language(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "broken.json"
    config_path.write_text("{broken", encoding="utf-8")

    result = cli_main(
        [
            "cache-inventory",
            "--url",
            "https://discord.com/channels/11111111111111111/22222222222222222",
            "--config-path",
            str(config_path),
            "--json",
        ]
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert result == 2
    assert payload["state"] == "blocked"
    assert payload["message"] == "ローカルcache設定を読めません。JSON形式と読取権限を確認してください。"
    assert str(tmp_path) not in rendered
