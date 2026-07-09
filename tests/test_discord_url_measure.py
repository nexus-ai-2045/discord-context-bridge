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
