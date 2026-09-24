import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import discord_url_measure


def test_discord_url_measure_reports_cache_without_leaking_url_or_ids(tmp_path: Path):
    snapshot_root = tmp_path / "discord"
    target_dir = snapshot_root / "servers" / "111111111111111111" / "channels" / "222222222222222222"
    capture_dir = target_dir / "live-visible-capture-20260708-1200"
    capture_dir.mkdir(parents=True)
    (capture_dir / "visible-capture.md").write_text("member-a: 本文は出さない\n", encoding="utf-8")
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir()

    payload = discord_url_measure.build_measurement(
        url="https://discord.com/channels/111111111111111111/222222222222222222",
        snapshot_root=snapshot_root,
        channel_dir=channel_dir,
        timeout=0.01,
        interval=0,
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "discord_url_measure.v1"
    assert payload["ok"] is True
    assert payload["cache"]["target_cache_present"] is True
    assert payload["cache"]["snapshot_dirs_count"] == 1
    assert payload["next_action"] == "use_local_cache_metadata"
    assert "111111111111111111" not in rendered
    assert "222222222222222222" not in rendered
    assert "member-a" not in rendered
    assert "本文は出さない" not in rendered
    assert str(tmp_path) not in rendered


def test_discord_url_measure_blocks_visible_fallback_without_cache(tmp_path: Path):
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir()

    payload = discord_url_measure.build_measurement(
        url="https://discord.com/channels/111111111111111111/222222222222222222",
        snapshot_root=tmp_path / "missing",
        channel_dir=channel_dir,
        timeout=0.01,
        interval=0,
    )

    assert payload["ok"] is False
    assert payload["cache"]["target_cache_present"] is False
    assert payload["route"]["decision"] == "ask_browser_fallback"
    assert payload["route"]["chrome_fallback"]["auto_open"] is False
    assert payload["safety_boundary"]["outbound_actions"] == "disabled"


URL = "https://discord.com/channels/111111111111111111/222222222222222222"


def _isolate_snapshot_env(monkeypatch, tmp_path: Path) -> None:
    # Keep the real HOME / user config from leaking into root resolution.
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_CONFIG", str(tmp_path / "missing-config.json"))
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", raising=False)
    monkeypatch.delenv("DISCORD_CONTEXT_BRIDGE_SNAPSHOT_ROOT", raising=False)


def _make_target_cache(discord_root: Path) -> None:
    capture_dir = discord_root / "servers" / "111111111111111111" / "channels" / "222222222222222222" / "capture-1"
    capture_dir.mkdir(parents=True)
    (capture_dir / "visible-capture.md").write_text("member-a: 本文は出さない\n", encoding="utf-8")


def test_cache_probe_default_reads_canonical_env_and_appends_discord(tmp_path: Path, monkeypatch, capsys):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    shared_root = tmp_path / "shared-raw-snapshots"
    _make_target_cache(shared_root / "discord")
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(shared_root))

    cache = discord_url_measure.build_cache_probe(URL)

    assert cache["ok"] is True
    assert cache["target_cache_present"] is True
    assert cache["snapshot_dirs_count"] == 1
    assert capsys.readouterr().err == ""


def test_cache_probe_canonical_env_does_not_match_without_discord_suffix(tmp_path: Path, monkeypatch):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    shared_root = tmp_path / "shared-raw-snapshots"
    # Cache placed directly under the shared root (no "discord" dir) must not match.
    _make_target_cache(shared_root)
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(shared_root))

    cache = discord_url_measure.build_cache_probe(URL)

    assert discord_url_measure.default_snapshot_root() == shared_root / "discord"
    assert cache["target_cache_present"] is False


def test_cache_probe_default_honors_deprecated_env_alias_as_is(tmp_path: Path, monkeypatch, capsys):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    legacy_root = tmp_path / "legacy" / "discord"
    _make_target_cache(legacy_root)
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SNAPSHOT_ROOT", str(legacy_root))

    cache = discord_url_measure.build_cache_probe(URL)

    assert cache["ok"] is True
    assert cache["target_cache_present"] is True
    err = capsys.readouterr().err
    assert "DISCORD_CONTEXT_BRIDGE_SNAPSHOT_ROOT is deprecated" in err
    assert str(tmp_path) not in err


def test_canonical_env_takes_precedence_over_deprecated_alias(tmp_path: Path, monkeypatch, capsys):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    shared_root = tmp_path / "shared-raw-snapshots"
    _make_target_cache(shared_root / "discord")
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(shared_root))
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SNAPSHOT_ROOT", str(tmp_path / "legacy-missing"))

    cache = discord_url_measure.build_cache_probe(URL)

    assert cache["target_cache_present"] is True
    err = capsys.readouterr().err
    assert "using DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT" in err
    assert str(tmp_path) not in err


def test_no_conflict_warning_when_both_envs_agree(tmp_path: Path, monkeypatch, capsys):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    shared_root = tmp_path / "shared-raw-snapshots"
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(shared_root))
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SNAPSHOT_ROOT", str(shared_root / "discord"))

    assert discord_url_measure.default_snapshot_root() == shared_root / "discord"
    assert capsys.readouterr().err == ""


def test_default_snapshot_root_uses_config_then_os_default_with_discord_suffix(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"shared_snapshot_root": str(tmp_path / "cfg-root")}), encoding="utf-8")

    from_config = discord_url_measure.default_snapshot_root(env={}, config_path=config_path, home=tmp_path)
    from_default = discord_url_measure.default_snapshot_root(
        env={}, config_path=tmp_path / "missing.json", home=tmp_path
    )

    assert from_config == tmp_path / "cfg-root" / "discord"
    assert from_default == tmp_path / "Projects" / "Documents" / "discord" / "raw-snapshots" / "discord"


def test_main_without_snapshot_root_flag_reads_canonical_env(tmp_path: Path, monkeypatch, capsys):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    shared_root = tmp_path / "shared-raw-snapshots"
    _make_target_cache(shared_root / "discord")
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT", str(shared_root))
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir()

    discord_url_measure.main(
        ["--url", URL, "--channel-dir", str(channel_dir), "--timeout", "0.01", "--interval", "0", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["cache"]["target_cache_present"] is True
    assert payload["next_action"] == "use_local_cache_metadata"


def test_main_reports_malformed_config_as_json_error(tmp_path: Path, monkeypatch, capsys):
    _isolate_snapshot_env(monkeypatch, tmp_path)
    config_path = tmp_path / "config.json"
    config_path.write_text("{bad-secret-marker", encoding="utf-8")
    monkeypatch.setenv("DISCORD_CONTEXT_BRIDGE_CONFIG", str(config_path))
    channel_dir = tmp_path / "channel"
    channel_dir.mkdir()

    exit_code = discord_url_measure.main(
        ["--url", URL, "--channel-dir", str(channel_dir), "--timeout", "0.01", "--interval", "0", "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code != 0
    assert payload["schema"] == "discord_context_bridge_local_config_error.v1"
    assert payload["ok"] is False
    assert payload["reason"] == "local_config_unreadable"
    assert payload["path_output"] == "omitted"
    assert "bad-secret-marker" not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err
