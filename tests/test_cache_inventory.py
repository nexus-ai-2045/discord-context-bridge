import json
from datetime import datetime, timezone
from pathlib import Path

from discord_context_bridge.cache_inventory import build_cache_inventory
from discord_context_bridge.cli import main as cli_main


URL = "https://discord.com/channels/11111111111111111/22222222222222222"


def _write_record(path: Path, *, captured_at: str, title: str = "交流広場B") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "url": URL,
                "captured_at": captured_at,
                "title": title,
                "content_hash": "safe-hash",
                "text": "private body",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_cache_inventory_requires_live_refresh_even_when_exact_cache_is_recent(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    target = root / "discord" / "servers" / "11111111111111111" / "channels" / "22222222222222222"
    _write_record(target / "text-snapshots.ndjson", captured_at="2026-07-15T01:00:00+00:00")
    (target / "INDEX.md").write_text(
        "---\ntarget_key: ignored-here\ntitle: 交流広場B\n---\n",
        encoding="utf-8",
    )
    (root / "other.md").write_text("# other\n", encoding="utf-8")

    payload = build_cache_inventory(
        url=URL,
        cache_root=root,
        snapshot_store=tmp_path / "missing.ndjson",
        generated_at="2026-07-15T02:00:00+00:00",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["decision"] == "refresh_exact_url_snapshot"
    assert payload["live_refresh"] == {
        "required": True,
        "reason": "discord_url_received",
        "cache_may_satisfy_reply": False,
    }
    assert payload["stale_policy"]["usable_for_reply"] is False
    assert payload["stale_policy"]["reason"] == "direct_url_requires_live_refresh"
    assert payload["inventory"]["markdown_file_count"] == 2
    assert payload["inventory"]["exact_snapshot_record_count"] == 1
    assert payload["title"] == {
        "available": True,
        "source": "snapshot_record",
        "value_output": "omitted",
    }
    assert "private body" not in rendered
    assert "交流広場B" not in rendered
    assert str(tmp_path) not in rendered
    assert "11111111111111111" not in rendered


def test_cache_inventory_can_include_private_title_and_marks_stale(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    record = root / "discord" / "servers" / "11111111111111111" / "channels" / "22222222222222222" / "text-snapshots.ndjson"
    _write_record(record, captured_at="2026-07-10T01:00:00+00:00")

    payload = build_cache_inventory(
        url=URL,
        cache_root=root,
        snapshot_store=tmp_path / "missing.ndjson",
        include_private_title=True,
        generated_at="2026-07-15T02:00:00+00:00",
    )

    assert payload["decision"] == "refresh_exact_url_snapshot"
    assert payload["freshness"]["status"] == "stale"
    assert payload["title"]["value"] == "交流広場B"


def test_cache_inventory_missing_target_requests_capture(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    root.mkdir()
    payload = build_cache_inventory(
        url=URL,
        cache_root=root,
        snapshot_store=tmp_path / "missing.ndjson",
        generated_at="2026-07-15T02:00:00+00:00",
    )
    assert payload["state"] == "snapshot_missing"
    assert payload["decision"] == "capture_visible_or_read_only_adapter"
    assert payload["stale_policy"]["usable_for_reply"] is False


def test_cache_inventory_still_requires_live_refresh_when_saved_snapshot_exists(tmp_path: Path) -> None:
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    _write_record(snapshot_store, captured_at="2026-07-15T01:00:00+00:00")
    payload = build_cache_inventory(
        url=URL,
        cache_root=tmp_path / "missing-raw-root",
        snapshot_store=snapshot_store,
        generated_at="2026-07-15T02:00:00+00:00",
    )
    assert payload["ok"] is True
    assert payload["state"] == "ready"
    assert payload["decision"] == "refresh_exact_url_snapshot"


def test_cache_inventory_allows_recent_cache_only_with_explicit_opt_in(tmp_path: Path) -> None:
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    _write_record(snapshot_store, captured_at="2026-07-15T01:00:00+00:00")

    payload = build_cache_inventory(
        url=URL,
        cache_root=tmp_path / "missing-raw-root",
        snapshot_store=snapshot_store,
        require_live_refresh=False,
        generated_at="2026-07-15T02:00:00+00:00",
    )

    assert payload["decision"] == "use_local_snapshot"
    assert payload["live_refresh"]["reason"] == "explicit_recent_cache_opt_in"


def test_cache_inventory_cli_requires_refresh_by_default_and_opt_in_allows_cache(tmp_path: Path, capsys) -> None:
    root = tmp_path / "raw-snapshots"
    record = root / "discord" / "target" / "text-snapshots.ndjson"
    _write_record(record, captured_at=datetime.now(timezone.utc).isoformat())

    base_args = [
        "cache-inventory",
        "--url",
        URL,
        "--cache-root",
        str(root),
        "--snapshot-store",
        str(tmp_path / "missing.ndjson"),
        "--json",
    ]
    assert cli_main(base_args) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "refresh_exact_url_snapshot"

    assert cli_main([*base_args[:-1], "--allow-recent-cache", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "use_local_snapshot"


def test_cache_inventory_cli_is_metadata_only_end_to_end(tmp_path: Path, capsys) -> None:
    root = tmp_path / "raw-snapshots"
    record = root / "discord" / "target" / "text-snapshots.ndjson"
    _write_record(record, captured_at="2026-07-15T01:00:00+00:00")

    result = cli_main(
        [
            "cache-inventory",
            "--url",
            URL,
            "--cache-root",
            str(root),
            "--snapshot-store",
            str(tmp_path / "missing.ndjson"),
            "--json",
        ]
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert result == 0
    assert payload["inventory"]["exact_snapshot_record_count"] == 1
    assert payload["raw_text_returned"] is False
    assert "private body" not in rendered
    assert "交流広場B" not in rendered
    assert str(tmp_path) not in rendered
    assert "11111111111111111" not in rendered
