from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import stat
import urllib.error
from pathlib import Path

import pytest

from discord_context_bridge.archive_inventory import (
    ArchiveInventoryError,
    build_active_filtered_result,
    build_public_report,
    build_scope_receipts_inventory,
    enumerate_archive_pages,
    write_private_scope_receipts,
)
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.completeness_store import CompletenessStore


def _sized_scope_receipt(size: int, *, observed_at: str = "2026-09-07T00:00:00+00:00") -> dict:
    payload = build_scope_receipts_inventory(
        parent_target_key="fixture-parent", parent_kind="forum", scan_id="fixture-scan",
        observed_at=observed_at,
        active_filtered=build_active_filtered_result({"threads": []}, parent_target_key="fixture-parent"),
        archived_results=[enumerate_archive_pages(
            scope="public", fetch_page=lambda _: {"threads": [], "has_more": False}, max_pages=1,
        )],
        private_authorization_confirmed=False,
    )
    payload["fixture_padding"] = ""
    base = len((json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    # 文字数ではなく UTF-8 byte 数の境界を往復検証する。
    padding_bytes = size - base
    payload["fixture_padding"] = "あ" * (padding_bytes // 3) + "x" * (padding_bytes % 3)
    return payload


@pytest.mark.parametrize("observed_at", ["not-a-time", "2026-09-07T00:00:00", "2026-09-07"])
def test_scope_receipt_producer_rejects_invalid_observation_time(observed_at):
    with pytest.raises(ArchiveInventoryError, match="inventory_observed_at_invalid"):
        _sized_scope_receipt(2000, observed_at=observed_at)


def test_scope_receipt_offset_time_normalized_producer_consumer_roundtrip(tmp_path, capsys):
    payload = _sized_scope_receipt(2000, observed_at="2026-09-07T09:00:00+09:00")
    assert payload["observed_at"] == "2026-09-07T00:00:00+00:00"
    evidence = tmp_path / "receipt.json"
    write_private_scope_receipts(evidence, payload)
    database = tmp_path / "db.sqlite3"
    assert cli_main([
        "record-parent-inventory", "--db", str(database), "--evidence", str(evidence), "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    with sqlite3.connect(database) as connection:
        saved = connection.execute("SELECT observed_at FROM inventory_scans").fetchone()
    assert saved[0] == payload["observed_at"]


@pytest.mark.parametrize("size", [1_000_001, 10_000_000])
def test_private_scope_receipt_producer_consumer_size_roundtrip(tmp_path, capsys, size):
    evidence = tmp_path / "receipt.json"
    write_private_scope_receipts(evidence, _sized_scope_receipt(size))
    assert evidence.stat().st_size == size
    assert cli_main([
        "record-parent-inventory", "--db", str(tmp_path / "db.sqlite3"),
        "--evidence", str(evidence), "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert "fixture-parent" not in json.dumps(report)


def test_private_scope_receipt_over_limit_rejected_by_producer_and_consumer(tmp_path, capsys):
    evidence = tmp_path / "receipt.json"
    payload = _sized_scope_receipt(10_000_001)
    with pytest.raises(ArchiveInventoryError, match="scope_receipts_too_large"):
        write_private_scope_receipts(evidence, payload)
    assert not evidence.exists()
    evidence.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    assert cli_main([
        "record-parent-inventory", "--db", str(tmp_path / "db.sqlite3"),
        "--evidence", str(evidence), "--json",
    ]) != 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "fixture-parent" not in json.dumps(report)


def test_generic_private_json_limit_remains_one_megabyte(tmp_path):
    from discord_context_bridge.cli import _load_private_json

    evidence = tmp_path / "generic.json"
    evidence.write_text(json.dumps({"padding": "x" * 1_000_000}), encoding="utf-8")
    with pytest.raises(ValueError, match="private_json_too_large"):
        _load_private_json(evidence)


def _thread(thread_id: str, timestamp: str) -> dict[str, object]:
    return {
        "id": thread_id,
        "name": "private-name",
        "thread_metadata": {"archive_timestamp": timestamp},
    }


def _bound_thread(
    thread_id: str,
    parent_id: str,
    *,
    timestamp: str = "2026-09-01T00:00:00+00:00",
    locked: bool = False,
) -> dict[str, object]:
    return {
        "id": thread_id,
        "parent_id": parent_id,
        "thread_metadata": {
            "archive_timestamp": timestamp,
            "locked": locked,
        },
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


def test_scope_receipts_use_canonical_routes_and_active_parent_filter() -> None:
    parent = "parent-private"
    active = build_active_filtered_result(
        {
            "threads": [
                _bound_thread("active-match", parent),
                _bound_thread("active-other", "other-parent"),
            ]
        },
        parent_target_key=parent,
    )
    public = enumerate_archive_pages(
        scope="public",
        fetch_page=lambda _cursor: {
            "threads": [_bound_thread("archived", parent, locked=True)],
            "has_more": False,
        },
        max_pages=2,
    )

    private = build_scope_receipts_inventory(
        parent_target_key=parent,
        parent_kind="forum",
        scan_id="scan-private",
        observed_at="2026-09-07T00:00:00+00:00",
        active_filtered=active,
        archived_results=[public],
        private_authorization_confirmed=False,
    )

    receipts = private["scope_receipts"]
    assert set(receipts) == {"active_filtered", "archived_public"}
    assert receipts["active_filtered"]["route"] == "GET /guilds/{guild_id}/threads/active"
    assert receipts["active_filtered"]["active_parent_filter_applied"] is True
    assert receipts["active_filtered"]["thread_ids"] == ["active-match"]
    assert receipts["archived_public"]["locked_count"] == 1
    assert receipts["archived_public"]["terminal_reached"] is True


def test_text_scope_requires_confirmed_manage_threads_authorization() -> None:
    parent = "parent-private"
    active = build_active_filtered_result({"threads": []}, parent_target_key=parent)
    public = enumerate_archive_pages(
        scope="public",
        fetch_page=lambda _cursor: {"threads": [], "has_more": False},
        max_pages=1,
    )
    private = enumerate_archive_pages(
        scope="private",
        fetch_page=lambda _cursor: {"threads": [], "has_more": False},
        max_pages=1,
    )

    result = build_scope_receipts_inventory(
        parent_target_key=parent,
        parent_kind="text",
        scan_id="scan-private",
        observed_at="2026-09-07T00:00:00+00:00",
        active_filtered=active,
        archived_results=[public, private],
        private_authorization_confirmed=False,
    )

    assert result["pagination_exhausted"] is False
    assert result["scope_receipts"]["archived_private"]["authorization"] == {
        "capability": "manage_threads",
        "confirmed": False,
    }


@pytest.mark.parametrize("host", [
    "discord.com", "canary.discord.com", "ptb.discord.com",
    "discordapp.com", "canary.discordapp.com", "ptb.discordapp.com",
])
def test_canonical_fixture_cli_saves_private_receipts_and_public_output_is_safe(
    tmp_path: Path, capsys, host
) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "channel_metadata": {"id": "2", "guild_id": "1", "type": 15},
                "active_filtered": {
                    "threads": [_bound_thread("private-active-id", "2")]
                },
                "public": [
                    {
                        "threads": [_bound_thread("private-archive-id", "2", locked=True)],
                        "has_more": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "private" / "scope-receipts.json"

    exit_code = module.main(
        [
            "--url",
            f"https://{host}/channels/1/2",
            "--parent-kind",
            "forum",
            "--fixture-input",
            str(fixture),
            "--output",
            str(output),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    stored = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["thread_count"] == 2
    assert report["locked_count"] == 1
    assert report["identifiers_returned"] is False
    assert "private-active-id" not in json.dumps(report)
    assert stored["schema"] == "dcb.parent-thread-scope-receipts.v1"
    assert stored["scope_receipts"]["active_filtered"]["thread_ids"] == [
        "private-active-id"
    ]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize("url", [
    "https://discord.com.evil.example/channels/1/2",
    "https://discord.com@evil.example/channels/1/2",
    "http://discord.com/channels/1/2",
    "https://discord.com:444/channels/1/2",
    "https://discord.com/channels/@me/2",
    "https://canary.discord.com/channels/1/2/3",
    "https://discordapp.com/channels/1/2/threads/3",
    "https://ptb.discord.com/channels/1/not-an-id",
    "https://discord.com/channels/1/１２",
])
def test_parent_url_validation_fails_before_credentials(tmp_path, capsys, monkeypatch, url):
    module = _load_script(Path(__file__).resolve().parents[1])
    monkeypatch.setattr(module, "load_bot_token_from_provider", lambda **kwargs: pytest.fail("credential access"))
    output = tmp_path / "receipt.json"
    assert module.main(["--url", url, "--output", str(output), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["blockers"] == ["discord_parent_channel_url_required"]
    assert url not in json.dumps(report)
    assert not output.exists()


def test_parent_kind_mismatch_from_channel_metadata_fails_closed(
    tmp_path: Path, capsys
) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "channel_metadata": {"id": "2", "guild_id": "1", "type": 0},
                "active_filtered": {"threads": []},
                "public": [{"threads": [], "has_more": False}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--parent-kind",
            "forum",
            "--fixture-input",
            str(fixture),
            "--output",
            str(tmp_path / "unused.json"),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["blockers"] == ["parent_kind_mismatch"]
    assert report["identifiers_returned"] is False


def test_fixture_requires_trusted_channel_metadata(tmp_path: Path, capsys) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "active_filtered": {"threads": []},
                "public": [{"threads": [], "has_more": False}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--parent-kind",
            "forum",
            "--fixture-input",
            str(fixture),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["blockers"] == ["channel_metadata_invalid"]


@pytest.mark.parametrize("guild_id", [None, "different-guild"])
def test_channel_metadata_guild_binding_fails_closed(
    tmp_path: Path, capsys, guild_id: str | None
) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "channel_metadata": {"id": "2", "guild_id": guild_id, "type": 15},
                "active_filtered": {"threads": []},
                "public": [{"threads": [], "has_more": False}],
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--parent-kind",
            "forum",
            "--fixture-input",
            str(fixture),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["blockers"] == ["channel_guild_binding_mismatch"]
    assert report["identifiers_returned"] is False


def test_type_five_is_announcement_with_public_archive_only(
    tmp_path: Path, capsys
) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "channel_metadata": {"id": "2", "guild_id": "1", "type": 5},
                "active_filtered": {"threads": []},
                "public": [{"threads": [], "has_more": False}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "announcement.json"

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--parent-kind",
            "announcement",
            "--fixture-input",
            str(fixture),
            "--output",
            str(output),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    stored = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["scope_count"] == 2
    assert stored["parent_kind"] == "announcement"
    assert set(stored["scope_receipts"]) == {
        "active_filtered",
        "archived_public",
    }


def test_storage_oserror_never_exposes_path_in_public_blocker(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "channel_metadata": {"id": "2", "guild_id": "1", "type": 15},
                "active_filtered": {"threads": []},
                "public": [{"threads": [], "has_more": False}],
            }
        ),
        encoding="utf-8",
    )
    private_path = tmp_path / "secret-location" / "inventory.json"
    monkeypatch.setattr(
        module,
        "write_private_scope_receipts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(str(private_path))),
    )

    exit_code = module.main(
        [
            "--url",
            "https://discord.com/channels/1/2",
            "--parent-kind",
            "forum",
            "--fixture-input",
            str(fixture),
            "--output",
            str(private_path),
            "--json",
        ]
    )
    visible = capsys.readouterr().out
    report = json.loads(visible)

    assert exit_code == 2
    assert report["blockers"] == ["archive_inventory_failed"]
    assert str(private_path) not in visible


def test_http_429_is_retried_once_without_leaking_token(monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    calls = 0

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"threads": [], "has_more": false}'

    def urlopen(request, timeout):
        nonlocal calls
        calls += 1
        assert "private-token" not in request.full_url
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {},
                io.BytesIO(b'{"retry_after": 0}'),
            )
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    payload = module._request_json(
        route="/channels/private/threads/archived/public",
        token="private-token",
        query={"limit": "100"},
        sleep_seconds=0,
        max_rate_limit_retries=1,
    )

    assert payload["has_more"] is False
    assert calls == 2


def test_http_authorization_failure_is_metadata_only(monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)

    def urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "forbidden private-token",
            {},
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    with pytest.raises(ArchiveInventoryError, match="permission_denied"):
        module._request_json(
            route="/channels/private/threads/archived/private",
            token="private-token",
            query={},
            sleep_seconds=0,
            max_rate_limit_retries=0,
        )


def test_scope_receipts_flow_into_two_stable_parent_scans_and_audit(
    tmp_path: Path, capsys
) -> None:
    repo = Path(__file__).resolve().parents[1]
    module = _load_script(repo)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "channel_metadata": {"id": "2", "guild_id": "1", "type": 15},
                "active_filtered": {
                    "threads": [_bound_thread("active-child", "2")]
                },
                "public": [
                    {
                        "threads": [_bound_thread("archive-child", "2")],
                        "has_more": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    database = tmp_path / "completeness.sqlite3"
    for index in (1, 2):
        evidence = tmp_path / f"scan-{index}.json"
        assert module.main(
            [
                "--url",
                "https://discord.com/channels/1/2",
                "--parent-kind",
                "forum",
                "--scan-id",
                f"scan-{index}",
                "--observed-at",
                f"2026-09-07T00:0{index}:00+00:00",
                "--fixture-input",
                str(fixture),
                "--output",
                str(evidence),
                "--json",
            ]
        ) == 0
        capsys.readouterr()
        assert cli_main(
            [
                "record-parent-inventory",
                "--db",
                str(database),
                "--evidence",
                str(evidence),
                "--json",
            ]
        ) == 0
        visible = capsys.readouterr().out
        assert "active-child" not in visible
        assert "archive-child" not in visible

    store = CompletenessStore(database)
    certificate = {
        "schema": "discord_full_capture_completion_gate.v1",
        "status": "full",
        "full_capture_confirmed": True,
        "counts": {
            "messages": 1,
            "attachments_discovered": 0,
            "attachments_saved": 0,
            "attachments_manifested": 0,
        },
        "attachments_consistent": True,
        "unresolved_gap_count": 0,
        "pending_retry_count": 0,
        "blockers": [],
    }
    for index, thread_id in enumerate(("active-child", "archive-child"), start=1):
        store.record_child_certificate(
            "2", thread_id, {**certificate, "capture_id": f"capture-{index}"}
        )

    audit = store.audit_parent("2")
    assert audit["status"] == "full"
    assert audit["parent_full_capture_confirmed"] is True
    assert audit["inventory"]["evidence_model"] == "scope_receipts_v1"
    assert audit["inventory"]["stable_scan_count"] == 2
