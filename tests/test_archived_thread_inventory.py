from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

from discord_context_bridge.archive_inventory import (
    ArchiveInventoryError,
    build_public_report,
    enumerate_archive_pages,
)


def _thread(thread_id: str, timestamp: str) -> dict[str, object]:
    return {
        "id": thread_id,
        "name": "private-name",
        "thread_metadata": {"archive_timestamp": timestamp},
    }


def test_public_pagination_exhausts_without_exposing_identifiers() -> None:
    pages = iter(
        [
            {"threads": [_thread("1", "2026-09-01T00:00:00+00:00")], "has_more": True},
            {"threads": [_thread("2", "2026-08-01T00:00:00+00:00")], "has_more": False},
        ]
    )
    cursors: list[str | None] = []

    def fetch(cursor: str | None):
        cursors.append(cursor)
        return next(pages)

    result = enumerate_archive_pages(scope="public", fetch_page=fetch, max_pages=5)
    report = build_public_report([result])

    assert cursors == [None, "2026-09-01T00:00:00+00:00"]
    assert report["pagination_exhausted"] is True
    assert report["scopes"]["public"] == {
        "page_count": 2,
        "thread_count": 2,
        "pagination_exhausted": True,
    }
    rendered = json.dumps(report)
    assert "private-name" not in rendered
    assert '"id"' not in rendered


def test_joined_private_uses_snowflake_cursor() -> None:
    cursors: list[str | None] = []

    def fetch(cursor: str | None):
        cursors.append(cursor)
        return {"threads": [_thread("99", "unused")], "has_more": len(cursors) == 1}

    enumerate_archive_pages(scope="joined_private", fetch_page=fetch, max_pages=2)
    assert cursors == [None, "99"]


def test_cursor_loop_and_page_limit_fail_closed() -> None:
    payload = {"threads": [_thread("1", "same")], "has_more": True}
    try:
        enumerate_archive_pages(scope="public", fetch_page=lambda _cursor: payload, max_pages=3)
    except ArchiveInventoryError as error:
        assert str(error) == "archive_pagination_cursor_loop"
    else:
        raise AssertionError("cursor loop must fail closed")

    limited = enumerate_archive_pages(scope="public", fetch_page=lambda _cursor: payload, max_pages=1)
    assert limited["pagination_exhausted"] is False
    assert limited["blockers"] == ["archive_pagination_page_limit_reached"]


def _load_script(repo: Path):
    path = repo / "scripts" / "discord_archived_thread_inventory.py"
    spec = importlib.util.spec_from_file_location("discord_archived_thread_inventory", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_cli_writes_private_inventory_mode_0600(tmp_path: Path, capsys) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "public": [{"threads": [_thread("1", "2026-09-01T00:00:00Z")], "has_more": False}],
                "private": [{"threads": [], "has_more": False}],
                "joined_private": [{"threads": [], "has_more": False}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "private" / "inventory.json"

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--fixture-input",
            str(fixture),
            "--output",
            str(output),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["pagination_exhausted"] is True
    assert report["identifiers_returned"] is False
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    private = json.loads(output.read_text(encoding="utf-8"))
    assert private["parent_target_key"] == "2"
    assert private["scopes"]["public"]["threads"][0]["id"] == "1"


def test_cli_missing_token_is_metadata_only(tmp_path: Path, capsys, monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    monkeypatch.setattr(
        module,
        "load_bot_token_from_provider",
        lambda: type("Result", (), {"ok": False, "failure_stage": "bot_token_missing"})(),
    )
    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--output",
            str(tmp_path / "unused.json"),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert report["blockers"] == ["bot_token_missing"]
    assert report["identifiers_returned"] is False
    assert report["outbound_actions"] == "disabled"


def test_cli_rejects_message_url_as_parent_channel(tmp_path: Path, capsys) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2/3",
            "--fixture-input",
            str(fixture),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["blockers"] == ["discord_parent_channel_url_required"]
    assert report["outbound_actions"] == "disabled"


def test_cli_malformed_fixture_is_metadata_only(tmp_path: Path, capsys) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{broken", encoding="utf-8")

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--fixture-input",
            str(fixture),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["blockers"] == ["archive_fixture_invalid"]
    assert report["identifiers_returned"] is False
