from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from discord_context_bridge import cli, core
from discord_context_bridge.core import (
    LegacySnapshotRepairError,
    canonical_event_hash,
    load_text_snapshots,
    merge_orphan_snapshot_branch,
    repair_cross_thread_legacy_snapshot,
    snapshot_visible_text,
    stable_text_hash,
)


GUILD = "11111111111111111"
SOURCE_THREAD = "22222222222222222"
ROW_THREAD = "33333333333333333"
OTHER_THREAD = "44444444444444444"
MESSAGE = "55555555555555555"
OTHER_GUILD = "66666666666666666"
SOURCE_URL = f"https://discord.com/channels/{GUILD}/{SOURCE_THREAD}"
SOURCE_ALIAS_URL = f"https://discord.com/channels/{GUILD}/99999999999999999/threads/{SOURCE_THREAD}"
ROW_URL = f"https://discord.com/channels/{GUILD}/{ROW_THREAD}"
OTHER_URL = f"https://discord.com/channels/{GUILD}/{OTHER_THREAD}"
ROW_MESSAGE_URL = f"https://discord.com/channels/{GUILD}/{ROW_THREAD}/{MESSAGE}"
SOURCE_MESSAGE_URL = f"https://discord.com/channels/{GUILD}/{SOURCE_THREAD}/{MESSAGE}"
PRIVATE_TEXT = "private legacy body"


def _store(root: Path, thread_id: str) -> Path:
    return root / "discord" / "servers" / GUILD / "channels" / thread_id / "text-snapshots.ndjson"


def _write_records(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _legacy_row(*, url: str = ROW_URL, text: str = PRIVATE_TEXT) -> dict[str, object]:
    content_hash = stable_text_hash(text)
    return {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "target_key": stable_text_hash(url),
        "stream_id": stable_text_hash(url),
        "subject": stable_text_hash(url),
        "stream_sequence": 1,
        "expected_previous_stream_sequence": 0,
        "previous_event_hash": "",
        "previous_content_hash": None,
        "url": url,
        "content_hash": content_hash,
        "text": text,
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
    }


def _full_hash_legacy_row(*, url: str, text: str) -> dict[str, object]:
    row = _legacy_row(url=url, text=text)
    row["schema"] = "discord_visible_text_snapshot.v1"
    row["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row["content_hash_short"] = stable_text_hash(text)
    return row


def _seed_source(root: Path, *, legacy_rows: int = 1, source_url: str = SOURCE_URL):
    source = _store(root, SOURCE_THREAD)
    destination = _store(root, ROW_THREAD)
    snapshot_visible_text(text="source body", url=source_url, path=source)
    records = load_text_snapshots(source)
    legacy = _legacy_row()
    records.extend(dict(legacy) for _ in range(legacy_rows))
    _write_records(source, records)
    return source, destination, legacy, canonical_event_hash(legacy), source.read_bytes()


def _repair(root: Path, source: Path, destination: Path, row_hash: str, *, source_url: str = SOURCE_URL):
    return repair_cross_thread_legacy_snapshot(
        snapshot_root=root,
        source_store=source,
        source_target_url=source_url,
        quarantined_row_hash=row_hash,
        destination_store=destination,
    )


def _fixture_gate_status(path: Path) -> str:
    records = load_text_snapshots(path)
    quarantined = {
        record.get("quarantined_row_hash")
        for record in records
        if record.get("event_type") == "discord.snapshot_store.legacy_row_quarantined"
    }
    unresolved = [
        canonical_event_hash(record)
        for record in records
        if not record.get("event_type")
        and not record.get("event_hash")
        and canonical_event_hash(record) not in quarantined
    ]
    return "invalid" if unresolved else "ok"


def test_valid_flow_relocates_and_appends_correction(tmp_path):
    source, destination, legacy, row_hash, _ = _seed_source(tmp_path)
    result = _repair(tmp_path, source, destination, row_hash)
    assert result["ok"] is True
    assert result["saved"] is True
    assert result["duplicate"] is False
    relocated = load_text_snapshots(destination)[-1]
    correction = load_text_snapshots(source)[-1]
    assert relocated["text"] == legacy["text"]
    assert correction["event_type"] == "discord.snapshot_store.legacy_row_quarantined"
    assert correction["quarantined_row_hash"] == row_hash
    assert correction["relocated_snapshot_event_hash"] == relocated["event_hash"]
    assert correction["reason"] == "cross_thread_legacy_row"
    assert _fixture_gate_status(source) == "ok"


def test_missing_correction_remains_gate_invalid(tmp_path):
    source, _destination, _legacy, _row_hash, _ = _seed_source(tmp_path)
    assert _fixture_gate_status(source) == "invalid"


@pytest.mark.parametrize("legacy_rows,requested_hash", [(1, "0" * 64), (2, None)])
def test_missing_or_multiple_legacy_hash_is_rejected(tmp_path, legacy_rows, requested_hash):
    source, destination, _legacy, row_hash, before = _seed_source(tmp_path, legacy_rows=legacy_rows)
    with pytest.raises(LegacySnapshotRepairError):
        _repair(tmp_path, source, destination, requested_hash or row_hash)
    assert source.read_bytes() == before
    assert not destination.exists()


@pytest.mark.parametrize("same_thread_url", [SOURCE_URL, SOURCE_ALIAS_URL])
def test_same_logical_thread_flat_alias_is_recanonicalized_in_source_store(tmp_path, same_thread_url):
    source = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=same_thread_url)
    _write_records(source, load_text_snapshots(source) + [legacy])
    result = _repair(tmp_path, source, source, canonical_event_hash(legacy))
    records = load_text_snapshots(source)
    relocated, correction = records[-2:]
    assert result["saved"] is True
    assert relocated["url"] == SOURCE_URL
    assert correction["reason"] == "same_thread_legacy_row_recanonicalized"
    assert correction["previous_event_hash"] == relocated["event_hash"]


@pytest.mark.parametrize(
    "bad_url",
    [
        f"{ROW_URL}?query=1",
        f"https://discord.com/channels/{GUILD}/99999999999999999/{ROW_THREAD}",
    ],
)
def test_message_query_and_ambiguous_nested_urls_are_rejected(tmp_path, bad_url):
    source = _store(tmp_path, SOURCE_THREAD)
    destination = _store(tmp_path, ROW_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=bad_url)
    _write_records(source, load_text_snapshots(source) + [legacy])
    with pytest.raises(LegacySnapshotRepairError, match="target_invalid|store_target_path_mismatch"):
        _repair(tmp_path, source, destination, canonical_event_hash(legacy))


def test_caller_alias_uses_existing_official_source_stream_for_chain(tmp_path):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    previous = load_text_snapshots(source)[0]
    _repair(tmp_path, source, destination, row_hash, source_url=SOURCE_ALIAS_URL)
    correction = load_text_snapshots(source)[-1]
    assert correction["target_key"] == previous["target_key"]
    assert correction["previous_event_hash"] == previous["event_hash"]


@pytest.mark.parametrize("case", ["wrong_name", "escape", "hardlink", "symlink"])
def test_store_binding_rejects_unsafe_paths(tmp_path, case):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    candidate = destination
    if case == "wrong_name":
        candidate = destination.with_name("other.ndjson")
    elif case == "escape":
        candidate = tmp_path.parent / "text-snapshots.ndjson"
    elif case == "hardlink":
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.hardlink_to(source)
    elif case == "symlink":
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source)
    with pytest.raises(LegacySnapshotRepairError):
        _repair(tmp_path, source, candidate, row_hash)


def test_wrong_destination_target_is_rejected_before_relocation(tmp_path):
    source, destination, _legacy, row_hash, source_before = _seed_source(tmp_path)
    snapshot_visible_text(text="other body", url=OTHER_URL, path=destination)
    destination_before = destination.read_bytes()
    with pytest.raises(LegacySnapshotRepairError, match="destination_target_mismatch"):
        _repair(tmp_path, source, destination, row_hash)
    assert source.read_bytes() == source_before
    assert destination.read_bytes() == destination_before


def test_correction_has_own_hash_target_and_previous_proof(tmp_path):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    previous = load_text_snapshots(source)[0]
    _repair(tmp_path, source, destination, row_hash)
    correction = load_text_snapshots(source)[-1]
    assert correction["target_key"] == previous["target_key"]
    assert correction["stream_id"] == previous["stream_id"]
    assert correction["previous_event_hash"] == previous["event_hash"]
    assert correction["event_hash"] == canonical_event_hash(correction)


def test_idempotent_replay_and_conflicting_correction_rejected(tmp_path):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    _repair(tmp_path, source, destination, row_hash)
    source_after = source.read_bytes()
    destination_after = destination.read_bytes()
    replay = _repair(tmp_path, source, destination, row_hash)
    assert replay["duplicate"] is True
    assert replay["saved"] is False
    assert source.read_bytes() == source_after
    assert destination.read_bytes() == destination_after

    records = load_text_snapshots(source)
    records[-1]["relocated_content_hash"] = "f" * 16
    records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(source, records)
    with pytest.raises(LegacySnapshotRepairError, match="conflicting_correction"):
        _repair(tmp_path, source, destination, row_hash)


def test_crash_gap_retry_reuses_deterministic_relocation(tmp_path, monkeypatch):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    original_append = core.append_text_snapshot
    faulted = False

    def fail_correction_once(snapshot, path):
        nonlocal faulted
        if snapshot.get("event_type") == "discord.snapshot_store.legacy_row_quarantined" and not faulted:
            faulted = True
            raise OSError("private path must not escape")
        return original_append(snapshot, path)

    monkeypatch.setattr(core, "append_text_snapshot", fail_correction_once)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        _repair(tmp_path, source, destination, row_hash)
    assert len(load_text_snapshots(destination)) == 1

    monkeypatch.setattr(core, "append_text_snapshot", original_append)
    result = _repair(tmp_path, source, destination, row_hash)
    assert result["saved"] is True
    assert len(load_text_snapshots(destination)) == 1


def test_multiple_deterministic_relocations_are_ambiguous(tmp_path):
    source, destination, legacy, row_hash, source_before = _seed_source(tmp_path)
    relocation_source = f"legacy_snapshot_recanonicalization:{row_hash}"
    snapshot_visible_text(text=str(legacy["text"]), url=ROW_URL, source=relocation_source, path=destination)
    snapshot_visible_text(text=str(legacy["text"]), url=ROW_URL, source=relocation_source, path=destination)
    with pytest.raises(LegacySnapshotRepairError, match="relocation_ambiguous"):
        _repair(tmp_path, source, destination, row_hash)
    assert source.read_bytes() == source_before


def test_concurrent_replay_serializes_to_one_correction(tmp_path):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _n: _repair(tmp_path, source, destination, row_hash), range(2)))
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert len(load_text_snapshots(destination)) == 1
    assert sum(
        record.get("event_type") == "discord.snapshot_store.legacy_row_quarantined"
        for record in load_text_snapshots(source)
    ) == 1


@pytest.mark.parametrize("malformed_target", ["source", "destination"])
def test_malformed_store_is_safe_cli_failure(tmp_path, capsys, malformed_target):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    target = source if malformed_target == "source" else destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"{malformed\xff\n")
    rc = cli.main([
        "repair-cross-thread-legacy-snapshot", "--snapshot-root", str(tmp_path),
        "--source-store", str(source), "--source-target-url", SOURCE_URL,
        "--quarantined-row-hash", row_hash, "--destination-store", str(destination), "--json",
    ])
    output = capsys.readouterr().out
    assert rc == 2
    assert "Traceback" not in output
    assert str(source) not in output
    assert str(destination) not in output
    assert json.loads(output)["reason"] in {"store_decode_error", "store_io_error"}


def test_cli_is_append_only_metadata_only_and_outbound_disabled(tmp_path, capsys):
    source, destination, _legacy, row_hash, source_prefix = _seed_source(tmp_path)
    rc = cli.main([
        "repair-cross-thread-legacy-snapshot", "--snapshot-root", str(tmp_path),
        "--source-store", str(source), "--source-target-url", SOURCE_URL,
        "--quarantined-row-hash", row_hash, "--destination-store", str(destination), "--json",
    ])
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert rc == 0
    assert source.read_bytes().startswith(source_prefix)
    assert payload["outbound_actions"] == "disabled"
    assert payload["raw_text_returned"] is False
    assert payload["url_output"] == "omitted"
    assert payload["path_output"] == "omitted"
    assert PRIVATE_TEXT not in output
    assert SOURCE_URL not in output
    assert ROW_URL not in output
    assert str(source) not in output
    assert str(destination) not in output


@pytest.mark.parametrize("bad_sequence", [True, "1", -1, None])
def test_json_valid_invalid_sequence_is_safe_cli_failure(tmp_path, capsys, bad_sequence):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    records = load_text_snapshots(source)
    records[0]["stream_sequence"] = bad_sequence
    records[0]["event_hash"] = canonical_event_hash(records[0])
    _write_records(source, records)

    rc = cli.main([
        "repair-cross-thread-legacy-snapshot", "--snapshot-root", str(tmp_path),
        "--source-store", str(source), "--source-target-url", SOURCE_URL,
        "--quarantined-row-hash", row_hash, "--destination-store", str(destination), "--json",
    ])
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert rc == 2
    assert payload["reason"] == "source_official_stream_invalid"
    assert "Traceback" not in output
    assert str(source) not in output
    assert str(destination) not in output


def test_json_valid_non_object_row_is_safe_cli_failure(tmp_path, capsys):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    source.write_text("true\n", encoding="utf-8")
    rc = cli.main([
        "repair-cross-thread-legacy-snapshot", "--snapshot-root", str(tmp_path),
        "--source-store", str(source), "--source-target-url", SOURCE_URL,
        "--quarantined-row-hash", row_hash, "--destination-store", str(destination), "--json",
    ])
    output = capsys.readouterr().out
    assert rc == 2
    assert json.loads(output)["reason"] == "store_semantic_error"
    assert "Traceback" not in output
    assert str(source) not in output


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_previous_stream_sequence", 1),
        ("previous_event_hash", "tampered"),
        ("previous_content_hash", "tampered"),
    ],
)
def test_source_previous_stream_inconsistency_is_safe_cli_failure(tmp_path, capsys, field, value):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    records = load_text_snapshots(source)
    records[0][field] = value
    records[0]["event_hash"] = canonical_event_hash(records[0])
    _write_records(source, records)
    rc = cli.main([
        "repair-cross-thread-legacy-snapshot", "--snapshot-root", str(tmp_path),
        "--source-store", str(source), "--source-target-url", SOURCE_URL,
        "--quarantined-row-hash", row_hash, "--destination-store", str(destination), "--json",
    ])
    output = capsys.readouterr().out
    assert rc == 2
    assert json.loads(output)["reason"] == "source_official_stream_invalid"
    assert "Traceback" not in output
    assert str(source) not in output


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "tampered.v1"),
        ("type", "tampered"),
        ("datacontenttype", "text/plain"),
        ("private_local_only", False),
        ("external_share_allowed", True),
        ("outbound_actions", "enabled"),
        ("event_id", "tampered"),
        ("previous_content_hash", "tampered"),
        ("stream_sequence", True),
    ],
)
def test_rehashed_correction_semantic_tamper_is_rejected(tmp_path, field, value):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    _repair(tmp_path, source, destination, row_hash)
    records = load_text_snapshots(source)
    records[-1][field] = value
    records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(source, records)
    with pytest.raises(LegacySnapshotRepairError, match="conflicting_correction"):
        _repair(tmp_path, source, destination, row_hash)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "tampered.v1"),
        ("type", "tampered"),
        ("datacontenttype", "application/json"),
        ("private_local_only", False),
        ("external_share_allowed", True),
        ("outbound_actions", "enabled"),
        ("event_id", "tampered"),
        ("previous_content_hash", "tampered"),
        ("stream_sequence", False),
    ],
)
def test_rehashed_relocation_semantic_tamper_is_rejected(tmp_path, field, value):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    _repair(tmp_path, source, destination, row_hash)
    destination_records = load_text_snapshots(destination)
    destination_records[-1][field] = value
    destination_records[-1]["event_hash"] = canonical_event_hash(destination_records[-1])
    _write_records(destination, destination_records)
    with pytest.raises(LegacySnapshotRepairError, match="relocation_proof_mismatch"):
        _repair(tmp_path, source, destination, row_hash)


@pytest.mark.parametrize("event_kind", ["correction", "relocation"])
def test_rehashed_target_key_tamper_is_rejected_by_semantic_contract(tmp_path, event_kind):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    _repair(tmp_path, source, destination, row_hash)
    path = source if event_kind == "correction" else destination
    records = load_text_snapshots(path)
    records[-1]["target_key"] = "f" * 16
    records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(path, records)
    expected = "conflicting_correction" if event_kind == "correction" else "relocation_proof_mismatch"
    with pytest.raises(LegacySnapshotRepairError, match=expected):
        _repair(tmp_path, source, destination, row_hash)


def test_unknown_correction_field_is_forward_compatible_when_rehashed(tmp_path):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    _repair(tmp_path, source, destination, row_hash)
    records = load_text_snapshots(source)
    records[-1]["future_optional_metadata"] = {"version": 2}
    records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(source, records)
    result = _repair(tmp_path, source, destination, row_hash)
    assert result["duplicate"] is True


def test_legacy_message_deep_link_relocates_to_canonical_thread_identity(tmp_path):
    source = _store(tmp_path, SOURCE_THREAD)
    destination = _store(tmp_path, ROW_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=ROW_MESSAGE_URL)
    _write_records(source, load_text_snapshots(source) + [legacy])
    row_hash = canonical_event_hash(legacy)

    result = _repair(tmp_path, source, destination, row_hash)

    relocated = load_text_snapshots(destination)[-1]
    correction = load_text_snapshots(source)[-1]
    assert relocated["url"] == ROW_URL
    assert relocated["target_key"] == stable_text_hash(ROW_URL)
    assert relocated["stream_id"] == stable_text_hash(ROW_URL)
    assert relocated["subject"] == stable_text_hash(ROW_URL)
    assert ROW_MESSAGE_URL not in json.dumps(result, ensure_ascii=False)
    assert "url" not in correction
    assert correction["quarantined_row_hash"] == row_hash
    assert correction["relocated_content_hash"] == legacy["content_hash"]
    assert correction["relocated_snapshot_event_hash"] == relocated["event_hash"]


def test_same_thread_legacy_message_deep_link_is_recanonicalized(tmp_path):
    source = _store(tmp_path, SOURCE_THREAD)
    destination = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL)
    _write_records(source, load_text_snapshots(source) + [legacy])
    result = _repair(tmp_path, source, destination, canonical_event_hash(legacy))
    relocated, correction = load_text_snapshots(source)[-2:]
    assert result["saved"] is True
    assert relocated["url"] == SOURCE_URL
    assert relocated["target_key"] == stable_text_hash(SOURCE_URL)
    assert correction["previous_event_hash"] == relocated["event_hash"]
    assert correction["reason"] == "same_thread_legacy_row_recanonicalized"


@pytest.mark.parametrize(
    "bad_legacy_url",
    [
        f"{ROW_MESSAGE_URL}?query=1",
        f"{ROW_MESSAGE_URL}/66666666666666666",
        f"https://discord.com/channels/{GUILD}/99999999999999999/threads/{ROW_THREAD}/{MESSAGE}",
    ],
)
def test_legacy_message_deep_link_rejects_query_and_extra_segments(tmp_path, bad_legacy_url):
    source = _store(tmp_path, SOURCE_THREAD)
    destination = _store(tmp_path, ROW_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=bad_legacy_url)
    _write_records(source, load_text_snapshots(source) + [legacy])
    with pytest.raises(LegacySnapshotRepairError, match="legacy_row_target_invalid"):
        _repair(tmp_path, source, destination, canonical_event_hash(legacy))


def test_source_target_message_deep_link_remains_rejected(tmp_path):
    source, destination, _legacy, row_hash, _ = _seed_source(tmp_path)
    with pytest.raises(LegacySnapshotRepairError, match="source_target_invalid"):
        _repair(tmp_path, source, destination, row_hash, source_url=SOURCE_MESSAGE_URL)


def test_cross_guild_legacy_message_is_rejected_before_destination_creation(tmp_path):
    source = _store(tmp_path, SOURCE_THREAD)
    destination = (
        tmp_path
        / "discord"
        / "servers"
        / OTHER_GUILD
        / "channels"
        / ROW_THREAD
        / "text-snapshots.ndjson"
    )
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    cross_guild_url = f"https://discord.com/channels/{OTHER_GUILD}/{ROW_THREAD}/{MESSAGE}"
    legacy = _legacy_row(url=cross_guild_url)
    _write_records(source, load_text_snapshots(source) + [legacy])
    source_before = source.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="cross_guild_legacy_row"):
        _repair(tmp_path, source, destination, canonical_event_hash(legacy))

    assert source.read_bytes() == source_before
    assert not destination.exists()
    assert not destination.parent.exists()


def test_three_same_store_legacy_rows_form_one_valid_repair_chain(tmp_path):
    source = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy_rows = [
        _legacy_row(url=f"{SOURCE_URL}/{int(MESSAGE) + index}", text=f"legacy-{index}")
        for index in range(3)
    ]
    initial_records = load_text_snapshots(source) + legacy_rows
    _write_records(source, initial_records)

    for legacy in legacy_rows:
        _repair(tmp_path, source, source, canonical_event_hash(legacy))

    appended = load_text_snapshots(source)[len(initial_records):]
    assert len(appended) == 6
    previous_hash = str(initial_records[0]["event_hash"])
    for index, record in enumerate(appended):
        assert record["event_hash"] == canonical_event_hash(record)
        assert record["previous_event_hash"] == previous_hash
        previous_hash = record["event_hash"]
        if index % 2 == 1:
            assert record["event_type"] == "discord.snapshot_store.legacy_row_quarantined"


def test_canonical_repair_cli_name_is_available_for_same_store(tmp_path, capsys):
    source = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL)
    _write_records(source, load_text_snapshots(source) + [legacy])
    rc = cli.main([
        "repair-legacy-snapshot-row", "--snapshot-root", str(tmp_path),
        "--source-store", str(source), "--source-target-url", SOURCE_URL,
        "--quarantined-row-hash", canonical_event_hash(legacy),
        "--destination-store", str(source), "--json",
    ])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["saved"] is True


def test_same_thread_repair_requires_destination_to_bind_source_store(tmp_path):
    source = _store(tmp_path, SOURCE_THREAD)
    wrong_destination = _store(tmp_path, ROW_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL)
    _write_records(source, load_text_snapshots(source) + [legacy])
    before = source.read_bytes()
    with pytest.raises(LegacySnapshotRepairError, match="store_target_path_mismatch"):
        _repair(tmp_path, source, wrong_destination, canonical_event_hash(legacy))
    assert source.read_bytes() == before
    assert not wrong_destination.exists()


def test_same_store_crash_gap_retry_and_replay_are_idempotent(tmp_path, monkeypatch):
    source = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL)
    row_hash = canonical_event_hash(legacy)
    _write_records(source, load_text_snapshots(source) + [legacy])
    original_append = core.append_text_snapshot
    faulted = False

    def fail_correction_once(snapshot, path):
        nonlocal faulted
        if snapshot.get("event_type") == "discord.snapshot_store.legacy_row_quarantined" and not faulted:
            faulted = True
            raise OSError("private path must not escape")
        return original_append(snapshot, path)

    monkeypatch.setattr(core, "append_text_snapshot", fail_correction_once)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        _repair(tmp_path, source, source, row_hash)
    assert sum(
        str(record.get("source") or "").startswith("legacy_snapshot_recanonicalization:")
        for record in load_text_snapshots(source)
    ) == 1

    monkeypatch.setattr(core, "append_text_snapshot", original_append)
    repaired = _repair(tmp_path, source, source, row_hash)
    repaired_bytes = source.read_bytes()
    replay = _repair(tmp_path, source, source, row_hash)
    assert repaired["saved"] is True
    assert replay["duplicate"] is True
    assert source.read_bytes() == repaired_bytes


def test_same_store_concurrent_repair_appends_one_pair(tmp_path):
    source = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source body", url=SOURCE_URL, path=source)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL)
    row_hash = canonical_event_hash(legacy)
    _write_records(source, load_text_snapshots(source) + [legacy])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _n: _repair(tmp_path, source, source, row_hash), range(2)))
    assert sorted(result["duplicate"] for result in results) == [False, True]
    records = load_text_snapshots(source)
    assert sum(record.get("quarantined_row_hash") == row_hash for record in records) == 1
    assert sum(str(record.get("source") or "").endswith(row_hash) for record in records) == 1


def _graph_event(
    *,
    event_type: str,
    previous_hash: str,
    sequence: int,
    text: str,
    url: str = SOURCE_URL,
) -> dict[str, object]:
    target_key = stable_text_hash(url)
    content_hash = stable_text_hash(text)
    captured_at = f"2026-09-01T00:00:{sequence:02d}+00:00"
    record: dict[str, object] = {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "event_id": stable_text_hash("|".join([captured_at, target_key, content_hash, "test_fixture", str(sequence)])),
        "event_type": event_type,
        "stream_id": target_key,
        "stream_sequence": sequence,
        "expected_previous_stream_sequence": max(0, sequence - 1),
        "specversion": "1.0",
        "type": event_type,
        "subject": target_key,
        "time": captured_at,
        "datacontenttype": "text/plain; charset=utf-8",
        "dataschema": "discord_context_bridge_text_snapshot_observation.v1",
        "captured_at": captured_at,
        "observed_at": captured_at,
        "ingested_at": captured_at,
        "source": "test_fixture",
        "url": url,
        "title": "fixture",
        "target_key": target_key,
        "content_hash": content_hash,
        "previous_content_hash": None,
        "previous_event_hash": previous_hash,
        "changed": True,
        "duplicate_content": False,
        "text": text,
        "observation_index_for_target": sequence,
        "acquisition_context": core.acquisition_context_for_source("test_fixture"),
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
    }
    if event_type == "message_observation":
        record.update(
            message_id=None,
            ordinal=sequence,
            author_label="fixture-author",
            visible_timestamp=captured_at,
            duplicate_message_id=False,
        )
    record["event_hash"] = canonical_event_hash(record)
    return record


def _seed_orphan_branch(root: Path):
    store = _store(root, SOURCE_THREAD)
    main_root = _graph_event(event_type="message_observation", previous_hash="", sequence=1, text="main-1")
    main_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(main_root["event_hash"]),
        sequence=2,
        text="main-2",
    )
    main_head["previous_content_hash"] = main_root["content_hash"]
    main_head["event_hash"] = canonical_event_hash(main_head)
    legacy_root = _legacy_row(url=SOURCE_ALIAS_URL, text="legacy-root")
    legacy_hash = canonical_event_hash(legacy_root)
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=legacy_hash,
        sequence=2,
        text="orphan-head",
        url=SOURCE_ALIAS_URL,
    )
    branch_head["previous_content_hash"] = legacy_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    _write_records(store, [main_root, main_head, legacy_root, branch_head])
    return store, main_head, legacy_root, branch_head


def _seed_alias_orphan_branch(root: Path):
    store = _store(root, SOURCE_THREAD)
    main_root = _graph_event(
        event_type="message_observation",
        previous_hash="",
        sequence=1,
        text="alias-main",
        url=SOURCE_ALIAS_URL,
    )
    main_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(main_root["event_hash"]),
        sequence=2,
        text="alias-main-snapshot",
        url=SOURCE_ALIAS_URL,
    )
    main_head["previous_content_hash"] = main_root["content_hash"]
    main_head["event_hash"] = canonical_event_hash(main_head)
    legacy_root = _legacy_row(url=SOURCE_ALIAS_URL, text="legacy-alias")
    legacy_root_hash = canonical_event_hash(legacy_root)
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=legacy_root_hash,
        sequence=2,
        text="orphan-alias",
        url=SOURCE_ALIAS_URL,
    )
    branch_head["previous_content_hash"] = legacy_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    _write_records(store, [main_root, main_head, legacy_root, branch_head])
    return store, legacy_root, branch_head


def _seed_message_contract_orphan(root: Path):
    store = _store(root, SOURCE_THREAD)
    main_root = _graph_event(
        event_type="message_observation",
        previous_hash="",
        sequence=1,
        text="",
    )
    main_root["ordinal"] = 0
    main_root["event_hash"] = canonical_event_hash(main_root)
    legacy_root = _legacy_row(url=SOURCE_ALIAS_URL, text="legacy-message-contract")
    legacy_root_hash = canonical_event_hash(legacy_root)
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=legacy_root_hash,
        sequence=2,
        text="orphan-message-contract",
        url=SOURCE_ALIAS_URL,
    )
    branch_head["previous_content_hash"] = legacy_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    _write_records(store, [main_root, legacy_root, branch_head])
    return store, legacy_root, branch_head


def _merge_orphan(root: Path, store: Path, legacy_root, branch_head):
    return merge_orphan_snapshot_branch(
        snapshot_root=root,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        legacy_root_hash=canonical_event_hash(legacy_root),
    )


def _append_orphan_fixture(store: Path, *, suffix: str):
    legacy_root = _legacy_row(url=SOURCE_ALIAS_URL, text=f"legacy-{suffix}")
    legacy_hash = canonical_event_hash(legacy_root)
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=legacy_hash,
        sequence=2,
        text=f"orphan-{suffix}",
        url=SOURCE_ALIAS_URL,
    )
    branch_head["previous_content_hash"] = legacy_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    _write_records(store, load_text_snapshots(store) + [legacy_root, branch_head])
    return legacy_root, branch_head


def _append_orphan_branch_extension(store: Path, prior: dict, *, text: str) -> dict:
    extension = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(prior["event_hash"]),
        sequence=int(prior["stream_sequence"]) + 1,
        text=text,
        url=SOURCE_ALIAS_URL,
    )
    extension["previous_content_hash"] = prior["content_hash"]
    extension["event_hash"] = canonical_event_hash(extension)
    _write_records(store, load_text_snapshots(store) + [extension])
    return extension


def test_merge_orphan_snapshot_branch_appends_metadata_only_main_head_event(tmp_path):
    store, main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    before = store.read_bytes()
    result = _merge_orphan(tmp_path, store, legacy_root, branch_head)
    merged = load_text_snapshots(store)[-1]
    assert result["saved"] is True
    assert store.read_bytes().startswith(before)
    assert merged["event_type"] == "discord.snapshot_store.orphan_branch_merged"
    assert merged["previous_event_hash"] == main_head["event_hash"]
    assert merged["branch_head_event_hash"] == branch_head["event_hash"]
    assert merged["branch_root_event_hash"] == canonical_event_hash(legacy_root)
    assert merged["branch_content_hash"] == branch_head["content_hash"]
    assert merged["reason"] == "orphan_snapshot_branch_merged"
    assert "text" not in merged and "url" not in merged and "path" not in merged
    assert merged["event_hash"] == canonical_event_hash(merged)


def test_merge_orphan_snapshot_branch_is_idempotent_and_concurrent(tmp_path):
    store, _main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _n: _merge_orphan(tmp_path, store, legacy_root, branch_head), range(2))
        )
    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert sum(
        record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
        for record in load_text_snapshots(store)
    ) == 1


@pytest.mark.parametrize("mutation", ["head_missing", "head_not_snapshot", "head_has_child", "branch_message", "main_fork"])
def test_merge_orphan_snapshot_branch_preconditions_fail_closed(tmp_path, mutation):
    store, main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    records = load_text_snapshots(store)
    requested_head = str(branch_head["event_hash"])
    if mutation == "head_missing":
        requested_head = "f" * 64
    elif mutation == "head_not_snapshot":
        records[-1]["event_type"] = "message_observation"
        records[-1]["type"] = "message_observation"
        records[-1]["event_hash"] = canonical_event_hash(records[-1])
        requested_head = str(records[-1]["event_hash"])
    elif mutation == "head_has_child":
        records.append(_graph_event(event_type="discord.visible_text.snapshot_observed", previous_hash=requested_head, sequence=3, text="child", url=SOURCE_ALIAS_URL))
    elif mutation == "branch_message":
        records[-1]["event_type"] = "message_observation"
        records[-1]["type"] = "message_observation"
        records[-1]["event_hash"] = canonical_event_hash(records[-1])
        requested_head = str(records[-1]["event_hash"])
    elif mutation == "main_fork":
        records.append(_graph_event(event_type="discord.visible_text.snapshot_observed", previous_hash=str(records[0]["event_hash"]), sequence=2, text="fork"))
    _write_records(store, records)
    before = store.read_bytes()
    with pytest.raises(LegacySnapshotRepairError):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=requested_head,
            legacy_root_hash=canonical_event_hash(legacy_root),
        )
    assert store.read_bytes() == before


def test_merged_orphan_is_excluded_from_official_stream_and_three_repairs_continue(tmp_path):
    store, _main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, branch_head)
    new_legacy = [
        _legacy_row(url=f"{SOURCE_URL}/{int(MESSAGE) + 10 + index}", text=f"new-{index}")
        for index in range(3)
    ]
    _write_records(store, load_text_snapshots(store) + new_legacy)
    for legacy in new_legacy:
        result = _repair(tmp_path, store, store, canonical_event_hash(legacy))
        assert result["saved"] is True


def test_merge_orphan_snapshot_branch_cli_is_metadata_only(tmp_path, capsys):
    store, _main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    rc = cli.main([
        "merge-orphan-snapshot-branch", "--snapshot-root", str(tmp_path),
        "--source-store", str(store), "--source-target-url", SOURCE_URL,
        "--branch-head-event-hash", str(branch_head["event_hash"]),
        "--legacy-root-hash", canonical_event_hash(legacy_root), "--json",
    ])
    output = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(output)
    assert payload["outbound_actions"] == "disabled"
    assert payload["raw_text_returned"] is False
    assert SOURCE_URL not in output and str(store) not in output


def test_merge_orphan_crash_after_append_recovers_as_duplicate(tmp_path, monkeypatch):
    store, _main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    original_append = core.append_text_snapshot
    faulted = False

    def append_then_fail(snapshot, path):
        nonlocal faulted
        original_append(snapshot, path)
        if snapshot.get("event_type") == "discord.snapshot_store.orphan_branch_merged" and not faulted:
            faulted = True
            raise OSError("private path must not escape")

    monkeypatch.setattr(core, "append_text_snapshot", append_then_fail)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        _merge_orphan(tmp_path, store, legacy_root, branch_head)
    monkeypatch.setattr(core, "append_text_snapshot", original_append)
    result = _merge_orphan(tmp_path, store, legacy_root, branch_head)
    assert result["duplicate"] is True
    assert sum(
        record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
        for record in load_text_snapshots(store)
    ) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", "tampered.v1"),
        ("event_id", "tampered"),
        ("target_key", "f" * 16),
        ("private_local_only", False),
        ("reason", "tampered"),
        ("branch_content_hash", "f" * 16),
    ],
)
def test_rehashed_orphan_merge_semantic_tamper_is_rejected(tmp_path, field, value):
    store, _main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, branch_head)
    records = load_text_snapshots(store)
    records[-1][field] = value
    records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(store, records)
    with pytest.raises(LegacySnapshotRepairError):
        _merge_orphan(tmp_path, store, legacy_root, branch_head)


@pytest.mark.parametrize(
    "mutation",
    [
        "main_target_key",
        "branch_target_key",
        "branch_private_local_only",
        "branch_external_share_allowed",
        "branch_outbound_actions",
        "main_private_local_only",
        "main_content_text_mismatch",
        "branch_content_text_mismatch",
    ],
)
def test_orphan_merge_rejects_rehashed_semantically_invalid_source_events(tmp_path, mutation):
    store, _main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    records = load_text_snapshots(store)
    requested_head_hash = str(branch_head["event_hash"])
    if mutation.startswith("main_"):
        target = records[1]
        field = mutation.removeprefix("main_")
        if field == "content_text_mismatch":
            target["text"] = "tampered but content hash retained"
        else:
            target[field] = False if field == "private_local_only" else "f" * 16
        target["event_hash"] = canonical_event_hash(target)
    else:
        target = records[-1]
        field = mutation.removeprefix("branch_")
        if field == "private_local_only":
            target[field] = False
        elif field == "external_share_allowed":
            target[field] = True
        elif field == "outbound_actions":
            target[field] = "enabled"
        elif field == "content_text_mismatch":
            target["text"] = "tampered but content hash retained"
        else:
            target[field] = "f" * 16
        target["event_hash"] = canonical_event_hash(target)
        requested_head_hash = str(target["event_hash"])
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="source_event_semantic_invalid"):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=requested_head_hash,
            legacy_root_hash=canonical_event_hash(legacy_root),
        )

    assert store.read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "target_key",
        "stream_sequence",
        "private_local_only",
        "branch_root_event_hash",
        "branch_head_event_hash",
        "branch_content_hash",
    ],
)
def test_second_orphan_merge_rejects_rehashed_first_merge_tamper(tmp_path, mutation):
    store, _main_head, first_root, first_branch = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, first_root, first_branch)
    second_root, second_branch = _append_orphan_fixture(store, suffix="second")
    records = load_text_snapshots(store)
    first_merge = records[4]
    if mutation == "stream_sequence":
        first_merge[mutation] = int(first_merge[mutation]) + 10
    elif mutation == "private_local_only":
        first_merge[mutation] = False
    else:
        first_merge[mutation] = "f" * 16
    if mutation.startswith("branch_"):
        first_merge["event_id"] = stable_text_hash(
            "|".join(
                [
                    str(first_merge["event_type"]),
                    str(first_merge["branch_root_event_hash"]),
                    str(first_merge["branch_head_event_hash"]),
                    str(first_merge["branch_content_hash"]),
                ]
            )
        )
    first_merge["event_hash"] = canonical_event_hash(first_merge)
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="main_event_semantic_invalid"):
        _merge_orphan(tmp_path, store, second_root, second_branch)

    assert store.read_bytes() == before


def test_consecutive_merge_canonicalizes_alias_main_target_key(tmp_path):
    store, first_root, first_branch = _seed_alias_orphan_branch(tmp_path)

    first = _merge_orphan(tmp_path, store, first_root, first_branch)
    second_root, second_branch = _append_orphan_fixture(store, suffix="canonical-second")
    second = _merge_orphan(tmp_path, store, second_root, second_branch)
    merges = [
        record
        for record in load_text_snapshots(store)
        if record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
    ]

    assert first["saved"] is True and second["saved"] is True
    assert len(merges) == 2
    assert all(record["target_key"] == stable_text_hash(SOURCE_URL) for record in merges)
    assert merges[1]["previous_event_hash"] == merges[0]["event_hash"]


def test_alias_orphan_merge_immediate_retry_is_duplicate_and_byte_stable(tmp_path):
    store, legacy_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    first = _merge_orphan(tmp_path, store, legacy_root, branch_head)
    merged_bytes = store.read_bytes()

    replay = _merge_orphan(tmp_path, store, legacy_root, branch_head)

    assert first["saved"] is True
    assert replay["duplicate"] is True
    assert replay["saved"] is False
    assert store.read_bytes() == merged_bytes


def test_alias_orphan_merge_append_crash_recovers_as_duplicate(tmp_path, monkeypatch):
    store, legacy_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    original_append = core.append_text_snapshot
    faulted = False

    def append_then_fail(snapshot, path):
        nonlocal faulted
        original_append(snapshot, path)
        if snapshot.get("event_type") == "discord.snapshot_store.orphan_branch_merged" and not faulted:
            faulted = True
            raise OSError("private details")

    monkeypatch.setattr(core, "append_text_snapshot", append_then_fail)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        _merge_orphan(tmp_path, store, legacy_root, branch_head)
    crashed_bytes = store.read_bytes()
    monkeypatch.setattr(core, "append_text_snapshot", original_append)

    replay = _merge_orphan(tmp_path, store, legacy_root, branch_head)

    assert replay["duplicate"] is True
    assert store.read_bytes() == crashed_bytes


def test_message_observation_empty_text_and_zero_ordinal_are_valid_for_merge(tmp_path):
    store, legacy_root, branch_head = _seed_message_contract_orphan(tmp_path)

    result = _merge_orphan(tmp_path, store, legacy_root, branch_head)

    assert result["saved"] is True


@pytest.mark.parametrize("mutation", ["content_hash_mismatch", "text_type", "negative_ordinal"])
def test_message_observation_live_contract_invalid_values_fail_closed(tmp_path, mutation):
    store, legacy_root, branch_head = _seed_message_contract_orphan(tmp_path)
    records = load_text_snapshots(store)
    message = records[0]
    if mutation == "content_hash_mismatch":
        message["content_hash"] = stable_text_hash("not-empty")
    elif mutation == "text_type":
        message["text"] = 0
    else:
        message["ordinal"] = -1
    message["event_hash"] = canonical_event_hash(message)
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="source_event_semantic_invalid"):
        _merge_orphan(tmp_path, store, legacy_root, branch_head)

    assert store.read_bytes() == before


def test_alias_merge_then_three_same_store_repairs_form_one_cli_chain(tmp_path, capsys):
    store, branch_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    merge_rc = cli.main(
        [
            "merge-orphan-snapshot-branch",
            "--snapshot-root",
            str(tmp_path),
            "--source-store",
            str(store),
            "--source-target-url",
            SOURCE_URL,
            "--branch-head-event-hash",
            str(branch_head["event_hash"]),
            "--legacy-root-hash",
            canonical_event_hash(branch_root),
            "--json",
        ]
    )
    assert merge_rc == 0
    capsys.readouterr()
    legacy_rows = [
        _legacy_row(url=f"{SOURCE_URL}/{int(MESSAGE) + 100 + index}", text=f"live-{index}")
        for index in range(3)
    ]
    _write_records(store, load_text_snapshots(store) + legacy_rows)
    for legacy in legacy_rows:
        rc = cli.main(
            [
                "repair-legacy-snapshot-row",
                "--snapshot-root",
                str(tmp_path),
                "--source-store",
                str(store),
                "--source-target-url",
                SOURCE_URL,
                "--quarantined-row-hash",
                canonical_event_hash(legacy),
                "--destination-store",
                str(store),
                "--json",
            ]
        )
        assert rc == 0, capsys.readouterr().out
        capsys.readouterr()
    records = load_text_snapshots(store)
    main_events = records[:2] + [
        record
        for record in records
        if record.get("event_type")
        in {
            "discord.snapshot_store.orphan_branch_merged",
            "discord.snapshot_store.legacy_row_quarantined",
        }
        or str(record.get("source") or "").startswith("legacy_snapshot_recanonicalization:")
    ]
    assert len(main_events) == 9
    assert all(
        main_events[index]["previous_event_hash"] == main_events[index - 1]["event_hash"]
        for index in range(1, len(main_events))
    )
    repaired_bytes = store.read_bytes()

    replay = _repair(tmp_path, store, store, canonical_event_hash(legacy_rows[-1]))

    assert replay["duplicate"] is True
    assert store.read_bytes() == repaired_bytes


def _append_old_invalid_relocation(store: Path, legacy: dict[str, object]):
    records = load_text_snapshots(store)
    logical_head = next(record for record in reversed(records) if record.get("event_hash"))
    source = f"legacy_snapshot_recanonicalization:{canonical_event_hash(legacy)}"
    captured_at = "2026-09-01T01:00:00+00:00"
    target_key = stable_text_hash(SOURCE_URL)
    invalid = {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "event_id": core.snapshot_observation_event_id(
            captured_at=captured_at,
            target_key=target_key,
            content_hash=str(legacy["content_hash"]),
            source=source,
            stream_sequence=2,
        ),
        "event_type": "discord.visible_text.snapshot_observed",
        "stream_id": target_key,
        "stream_sequence": 2,
        "expected_previous_stream_sequence": int(logical_head["stream_sequence"]),
        "specversion": "1.0",
        "type": "discord.visible_text.snapshot_observed",
        "subject": target_key,
        "time": captured_at,
        "datacontenttype": "text/plain; charset=utf-8",
        "dataschema": "discord_context_bridge_text_snapshot_observation.v1",
        "captured_at": captured_at,
        "observed_at": captured_at,
        "ingested_at": captured_at,
        "source": source,
        "url": SOURCE_URL,
        "title": "",
        "target_key": target_key,
        "content_hash": legacy["content_hash"],
        "previous_content_hash": str(logical_head.get("content_hash") or ""),
        "previous_event_hash": logical_head["event_hash"],
        "changed": True,
        "duplicate_content": False,
        "observation_index_for_target": 2,
        "acquisition_context": core.acquisition_context_for_source(source),
        "text": legacy["text"],
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
    }
    invalid["event_hash"] = canonical_event_hash(invalid)
    _write_records(store, records + [invalid])
    return logical_head, invalid


def test_old_invalid_relocation_is_quarantined_and_three_repairs_recover(tmp_path):
    store, branch_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, branch_root, branch_head)
    legacy_rows = [
        _legacy_row(url=f"{SOURCE_URL}/{int(MESSAGE) + 200 + index}", text=f"recover-{index}")
        for index in range(3)
    ]
    _write_records(store, load_text_snapshots(store) + legacy_rows)
    logical_head, invalid = _append_old_invalid_relocation(store, legacy_rows[0])
    before = store.read_bytes()

    results = [
        _repair(tmp_path, store, store, canonical_event_hash(legacy)) for legacy in legacy_rows
    ]

    assert all(result["saved"] is True for result in results)
    assert store.read_bytes().startswith(before)
    records = load_text_snapshots(store)
    quarantines = [
        record
        for record in records
        if record.get("event_type") == "discord.snapshot_store.failed_relocation_quarantined"
    ]
    assert len(quarantines) == 1
    quarantine = quarantines[0]
    assert quarantine["invalid_relocation_event_hash"] == invalid["event_hash"]
    assert quarantine["quarantined_row_hash"] == canonical_event_hash(legacy_rows[0])
    assert quarantine["logical_previous_event_hash"] == logical_head["event_hash"]
    assert quarantine["previous_event_hash"] == invalid["event_hash"]
    assert quarantine["stream_sequence"] == logical_head["stream_sequence"] + 1
    repaired_bytes = store.read_bytes()
    replay = _repair(tmp_path, store, store, canonical_event_hash(legacy_rows[0]))
    assert replay["duplicate"] is True
    assert store.read_bytes() == repaired_bytes


@pytest.mark.parametrize("mutation", ["content", "privacy", "source", "position"])
def test_old_invalid_relocation_wrong_proof_fails_closed(tmp_path, mutation):
    store, branch_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, branch_root, branch_head)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL, text="recover-invalid")
    _write_records(store, load_text_snapshots(store) + [legacy])
    _logical_head, invalid = _append_old_invalid_relocation(store, legacy)
    records = load_text_snapshots(store)
    target = records[-1]
    if mutation == "content":
        target["content_hash"] = stable_text_hash("wrong")
    elif mutation == "privacy":
        target["private_local_only"] = False
    elif mutation == "source":
        target["source"] = "wrong"
    else:
        target["previous_event_hash"] = str(records[0]["event_hash"])
    target["event_hash"] = canonical_event_hash(target)
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError):
        _repair(tmp_path, store, store, canonical_event_hash(legacy))

    assert store.read_bytes() == before


def test_duplicate_failed_relocation_quarantine_fails_closed(tmp_path):
    store, branch_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, branch_root, branch_head)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL, text="recover-duplicate")
    _write_records(store, load_text_snapshots(store) + [legacy])
    _append_old_invalid_relocation(store, legacy)
    _repair(tmp_path, store, store, canonical_event_hash(legacy))
    records = load_text_snapshots(store)
    quarantine = next(
        record
        for record in records
        if record.get("event_type") == "discord.snapshot_store.failed_relocation_quarantined"
    )
    _write_records(store, records + [quarantine])
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError):
        _repair(tmp_path, store, store, canonical_event_hash(legacy))

    assert store.read_bytes() == before


def test_failed_relocation_quarantine_crash_recovers(tmp_path, monkeypatch):
    store, branch_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, branch_root, branch_head)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL, text="recover-crash")
    _write_records(store, load_text_snapshots(store) + [legacy])
    _append_old_invalid_relocation(store, legacy)
    original_append = core.append_text_snapshot
    faulted = False

    def append_then_fail(snapshot, path):
        nonlocal faulted
        original_append(snapshot, path)
        if snapshot.get("event_type") == "discord.snapshot_store.failed_relocation_quarantined" and not faulted:
            faulted = True
            raise OSError("private details")

    monkeypatch.setattr(core, "append_text_snapshot", append_then_fail)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        _repair(tmp_path, store, store, canonical_event_hash(legacy))
    monkeypatch.setattr(core, "append_text_snapshot", original_append)

    recovered = _repair(tmp_path, store, store, canonical_event_hash(legacy))

    assert recovered["saved"] is True
    assert sum(
        record.get("event_type") == "discord.snapshot_store.failed_relocation_quarantined"
        for record in load_text_snapshots(store)
    ) == 1


def test_failed_relocation_quarantine_concurrent_repair_appends_once(tmp_path):
    store, branch_root, branch_head = _seed_alias_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, branch_root, branch_head)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL, text="recover-concurrent")
    _write_records(store, load_text_snapshots(store) + [legacy])
    _append_old_invalid_relocation(store, legacy)
    row_hash = canonical_event_hash(legacy)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _n: _repair(tmp_path, store, store, row_hash), range(2)))

    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert sum(
        record.get("event_type") == "discord.snapshot_store.failed_relocation_quarantined"
        for record in load_text_snapshots(store)
    ) == 1


def test_full_hash_middle_legacy_row_recanonicalizes_after_first_repair(tmp_path):
    store = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source", url=SOURCE_URL, path=store)
    rows = [
        _legacy_row(url=SOURCE_MESSAGE_URL, text="row-1"),
        _full_hash_legacy_row(
            url=f"{SOURCE_URL}/{int(MESSAGE) + 1}",
            text="row-2-full",
        ),
        _legacy_row(url=f"{SOURCE_URL}/{int(MESSAGE) + 2}", text="row-3"),
    ]
    _write_records(store, load_text_snapshots(store) + rows)

    results = [_repair(tmp_path, store, store, canonical_event_hash(row)) for row in rows]

    assert all(result["saved"] is True for result in results)
    corrections = [
        record
        for record in load_text_snapshots(store)
        if record.get("event_type") == "discord.snapshot_store.legacy_row_quarantined"
    ]
    assert corrections[1]["legacy_content_hash"] == rows[1]["content_hash"]
    assert corrections[1]["relocated_content_hash"] == rows[1]["content_hash_short"]
    relocated = next(
        record
        for record in load_text_snapshots(store)
        if record.get("source")
        == f"legacy_snapshot_recanonicalization:{canonical_event_hash(rows[1])}"
    )
    assert relocated["content_hash"] == rows[1]["content_hash_short"]
    repaired_bytes = store.read_bytes()
    replay = _repair(tmp_path, store, store, canonical_event_hash(rows[1]))
    assert replay["duplicate"] is True
    assert store.read_bytes() == repaired_bytes


def test_existing_short_hash_correction_without_legacy_hash_keeps_compatibility(tmp_path):
    store = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source", url=SOURCE_URL, path=store)
    row = _legacy_row(url=SOURCE_MESSAGE_URL, text="old-correction")
    _write_records(store, load_text_snapshots(store) + [row])
    _repair(tmp_path, store, store, canonical_event_hash(row))
    records = load_text_snapshots(store)
    correction = records[-1]
    del correction["legacy_content_hash"]
    correction["event_hash"] = canonical_event_hash(correction)
    _write_records(store, records)
    before = store.read_bytes()

    replay = _repair(tmp_path, store, store, canonical_event_hash(row))

    assert replay["duplicate"] is True
    assert store.read_bytes() == before


def test_existing_full_hash_correction_without_legacy_hash_replays_as_duplicate(tmp_path):
    store = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source", url=SOURCE_URL, path=store)
    row = _full_hash_legacy_row(url=SOURCE_MESSAGE_URL, text="old-full-correction")
    _write_records(store, load_text_snapshots(store) + [row])
    _repair(tmp_path, store, store, canonical_event_hash(row))
    records = load_text_snapshots(store)
    correction = records[-1]
    del correction["legacy_content_hash"]
    correction["event_hash"] = canonical_event_hash(correction)
    _write_records(store, records)
    before = store.read_bytes()

    replay = _repair(tmp_path, store, store, canonical_event_hash(row))

    assert replay["duplicate"] is True
    assert store.read_bytes() == before


def test_existing_full_hash_correction_wrong_original_hash_fails_closed(tmp_path):
    store = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source", url=SOURCE_URL, path=store)
    row = _full_hash_legacy_row(url=SOURCE_MESSAGE_URL, text="wrong-full-correction")
    _write_records(store, load_text_snapshots(store) + [row])
    _repair(tmp_path, store, store, canonical_event_hash(row))
    records = load_text_snapshots(store)
    correction = records[-1]
    correction["legacy_content_hash"] = "f" * 64
    correction["event_hash"] = canonical_event_hash(correction)
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="conflicting_correction"):
        _repair(tmp_path, store, store, canonical_event_hash(row))

    assert store.read_bytes() == before


@pytest.mark.parametrize("mutation", ["full_hash", "short_hash"])
def test_full_hash_legacy_proof_mismatch_fails_closed(tmp_path, mutation):
    store = _store(tmp_path, SOURCE_THREAD)
    snapshot_visible_text(text="source", url=SOURCE_URL, path=store)
    row = _full_hash_legacy_row(url=SOURCE_MESSAGE_URL, text="full-invalid")
    if mutation == "full_hash":
        row["content_hash"] = "f" * 64
    else:
        row["content_hash_short"] = "f" * 16
    _write_records(store, load_text_snapshots(store) + [row])
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="legacy_row_content_mismatch"):
        _repair(tmp_path, store, store, canonical_event_hash(row))

    assert store.read_bytes() == before


def _seed_modern_orphan_branch(root: Path):
    store = _store(root, SOURCE_THREAD)
    main = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash="",
        sequence=1,
        text="snapshot-only-main",
    )
    branch_root = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash="",
        sequence=1,
        text="modern-branch-root",
        url=SOURCE_ALIAS_URL,
    )
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(branch_root["event_hash"]),
        sequence=2,
        text="modern-branch-head",
        url=SOURCE_ALIAS_URL,
    )
    branch_head["previous_content_hash"] = branch_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    _write_records(store, [main, branch_root, branch_head])
    return store, main, branch_root, branch_head


def test_modern_persisted_orphan_root_merges_into_snapshot_only_main(tmp_path):
    store, main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)
    before = store.read_bytes()

    result = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )

    assert result["saved"] is True
    assert store.read_bytes().startswith(before)
    merge = load_text_snapshots(store)[-1]
    assert merge["branch_root_event_hash"] == branch_root["event_hash"]
    assert merge["branch_head_event_hash"] == branch_head["event_hash"]
    assert merge["previous_event_hash"] == main["event_hash"]
    merged_bytes = store.read_bytes()
    replay = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )
    assert replay["duplicate"] is True
    assert store.read_bytes() == merged_bytes


def test_modern_orphan_cli_requires_exactly_one_root_proof(tmp_path):
    store, _main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)
    common = [
        "merge-orphan-snapshot-branch",
        "--snapshot-root",
        str(tmp_path),
        "--source-store",
        str(store),
        "--source-target-url",
        SOURCE_URL,
        "--branch-head-event-hash",
        str(branch_head["event_hash"]),
    ]
    with pytest.raises(SystemExit):
        cli.main(common)
    with pytest.raises(SystemExit):
        cli.main(
            common
            + [
                "--legacy-root-hash",
                "f" * 64,
                "--branch-root-event-hash",
                str(branch_root["event_hash"]),
            ]
        )


def test_modern_orphan_cli_accepts_persisted_root_proof(tmp_path, capsys):
    store, _main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)

    rc = cli.main(
        [
            "merge-orphan-snapshot-branch",
            "--snapshot-root",
            str(tmp_path),
            "--source-store",
            str(store),
            "--source-target-url",
            SOURCE_URL,
            "--branch-head-event-hash",
            str(branch_head["event_hash"]),
            "--branch-root-event-hash",
            str(branch_root["event_hash"]),
            "--json",
        ]
    )

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["saved"] is True


@pytest.mark.parametrize("mutation", ["root_privacy", "root_target", "fork"])
def test_modern_orphan_root_proof_is_strict_and_append_free_on_failure(tmp_path, mutation):
    store, _main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)
    records = load_text_snapshots(store)
    requested_root = str(branch_root["event_hash"])
    requested_head = str(branch_head["event_hash"])
    if mutation in {"root_privacy", "root_target"}:
        root_record = records[1]
        if mutation == "root_privacy":
            root_record["private_local_only"] = False
        else:
            root_record["target_key"] = "f" * 16
        root_record["event_hash"] = canonical_event_hash(root_record)
        requested_root = str(root_record["event_hash"])
        records[2]["previous_event_hash"] = requested_root
        records[2]["event_hash"] = canonical_event_hash(records[2])
        requested_head = str(records[2]["event_hash"])
    else:
        records.append(
            _graph_event(
                event_type="discord.visible_text.snapshot_observed",
                previous_hash=requested_root,
                sequence=2,
                text="modern-fork",
                url=SOURCE_ALIAS_URL,
            )
        )
        records[-1]["previous_content_hash"] = branch_root["content_hash"]
        records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=requested_head,
            branch_root_event_hash=requested_root,
        )

    assert store.read_bytes() == before


def test_modern_orphan_merge_concurrent_and_crash_recovery(tmp_path, monkeypatch):
    store, _main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)
    original_append = core.append_text_snapshot
    faulted = False

    def append_then_fail(snapshot, path):
        nonlocal faulted
        original_append(snapshot, path)
        if snapshot.get("event_type") == "discord.snapshot_store.orphan_branch_merged" and not faulted:
            faulted = True
            raise OSError("private details")

    monkeypatch.setattr(core, "append_text_snapshot", append_then_fail)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=str(branch_head["event_hash"]),
            branch_root_event_hash=str(branch_root["event_hash"]),
        )
    monkeypatch.setattr(core, "append_text_snapshot", original_append)
    recovered = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )
    assert recovered["duplicate"] is True


def test_modern_orphan_merge_concurrent_appends_once(tmp_path):
    store, _main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)

    def merge_once(_index):
        return merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=str(branch_head["event_hash"]),
            branch_root_event_hash=str(branch_root["event_hash"]),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(merge_once, range(2)))

    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert sum(
        record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
        for record in load_text_snapshots(store)
    ) == 1


@pytest.mark.parametrize("root_kind", ["legacy", "modern"])
def test_same_root_orphan_branch_linear_extension_appends_superseding_proof(
    tmp_path, root_kind
):
    if root_kind == "legacy":
        store, _main, root, old_head = _seed_orphan_branch(tmp_path)
        root_kwargs = {"legacy_root_hash": canonical_event_hash(root)}
    else:
        store, _main, root, old_head = _seed_modern_orphan_branch(tmp_path)
        root_kwargs = {"branch_root_event_hash": str(root["event_hash"])}
    first = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(old_head["event_hash"]),
        **root_kwargs,
    )
    extension = _append_orphan_branch_extension(
        store, old_head, text=f"{root_kind}-linear-extension"
    )

    second = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(extension["event_hash"]),
        **root_kwargs,
    )

    records = load_text_snapshots(store)
    proofs = [
        record
        for record in records
        if record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
    ]
    assert second["saved"] is True
    assert [proof["branch_head_event_hash"] for proof in proofs] == [
        old_head["event_hash"],
        extension["event_hash"],
    ]
    assert proofs[-1]["previous_event_hash"] == first["merge_event_hash"]
    assert core._validated_main_head_for_records(
        records, source_identity=(GUILD, SOURCE_THREAD)
    ) == proofs[-1]
    merged_bytes = store.read_bytes()
    replay = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(extension["event_hash"]),
        **root_kwargs,
    )
    assert replay["duplicate"] is True
    assert store.read_bytes() == merged_bytes


@pytest.mark.parametrize("root_kind", ["legacy", "modern"])
@pytest.mark.parametrize("proof_order", ["duplicate", "reversed"])
def test_same_root_orphan_proof_history_requires_strict_head_extension(
    tmp_path, root_kind, proof_order
):
    if root_kind == "legacy":
        store, _main, root, old_head = _seed_orphan_branch(tmp_path)
        root_kwargs = {"legacy_root_hash": canonical_event_hash(root)}
    else:
        store, _main, root, old_head = _seed_modern_orphan_branch(tmp_path)
        root_kwargs = {"branch_root_event_hash": str(root["event_hash"])}
    merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(old_head["event_hash"]),
        **root_kwargs,
    )
    extension = _append_orphan_branch_extension(
        store, old_head, text=f"{root_kind}-proof-order"
    )
    merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(extension["event_hash"]),
        **root_kwargs,
    )
    records = load_text_snapshots(store)
    proof_indexes = [
        index
        for index, record in enumerate(records)
        if record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
    ]
    first_index, second_index = proof_indexes
    first, second = records[first_index], records[second_index]
    if proof_order == "duplicate":
        second["branch_head_event_hash"] = first["branch_head_event_hash"]
        second["branch_content_hash"] = old_head["content_hash"]
        second["event_id"] = stable_text_hash(
            "|".join(
                [
                    str(second["event_type"]),
                    str(second["branch_root_event_hash"]),
                    str(second["branch_head_event_hash"]),
                    str(second["branch_content_hash"]),
                ]
            )
        )
    else:
        first["branch_head_event_hash"] = extension["event_hash"]
        first["branch_content_hash"] = extension["content_hash"]
        first["event_id"] = stable_text_hash(
            "|".join(
                [
                    str(first["event_type"]),
                    str(first["branch_root_event_hash"]),
                    str(first["branch_head_event_hash"]),
                    str(first["branch_content_hash"]),
                ]
            )
        )
        first["event_hash"] = canonical_event_hash(first)
        second["previous_event_hash"] = first["event_hash"]
        second["branch_head_event_hash"] = old_head["event_hash"]
        second["branch_content_hash"] = old_head["content_hash"]
        second["event_id"] = stable_text_hash(
            "|".join(
                [
                    str(second["event_type"]),
                    str(second["branch_root_event_hash"]),
                    str(second["branch_head_event_hash"]),
                    str(second["branch_content_hash"]),
                ]
            )
        )
    second["event_hash"] = canonical_event_hash(second)

    with pytest.raises(LegacySnapshotRepairError, match="orphan_merge_proof_not_extension"):
        core._validated_main_head_for_records(
            records, source_identity=(GUILD, SOURCE_THREAD)
        )


@pytest.mark.parametrize(
    "mutation", ["fork", "content", "hash", "target", "sequence", "non_descendant"]
)
def test_same_root_orphan_extension_rejects_invalid_new_head_without_merge_append(
    tmp_path, mutation
):
    store, _main, legacy_root, old_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, old_head)
    extension = _append_orphan_branch_extension(store, old_head, text="extension-proof")
    records = load_text_snapshots(store)
    requested_head = str(extension["event_hash"])
    if mutation == "fork":
        sibling = _graph_event(
            event_type="discord.visible_text.snapshot_observed",
            previous_hash=str(old_head["event_hash"]),
            sequence=int(old_head["stream_sequence"]) + 1,
            text="extension-fork",
            url=SOURCE_ALIAS_URL,
        )
        sibling["previous_content_hash"] = old_head["content_hash"]
        sibling["event_hash"] = canonical_event_hash(sibling)
        records.append(sibling)
    elif mutation == "non_descendant":
        other_root = _legacy_row(url=SOURCE_ALIAS_URL, text="other-root")
        other_hash = canonical_event_hash(other_root)
        records.append(other_root)
        extension["previous_event_hash"] = other_hash
        extension["previous_content_hash"] = other_root["content_hash"]
        extension["stream_sequence"] = 2
        extension["expected_previous_stream_sequence"] = 1
        extension["event_hash"] = canonical_event_hash(extension)
        records[-2] = extension
        requested_head = str(extension["event_hash"])
    else:
        target = records[-1]
        if mutation == "content":
            target["text"] = "tampered-text"
        elif mutation == "hash":
            target["event_hash"] = "f" * 64
            requested_head = "f" * 64
        elif mutation == "target":
            target["target_key"] = "f" * 16
        elif mutation == "sequence":
            target["stream_sequence"] = int(target["stream_sequence"]) + 1
        if mutation not in {"hash"}:
            target["event_hash"] = canonical_event_hash(target)
            requested_head = str(target["event_hash"])
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=requested_head,
            legacy_root_hash=canonical_event_hash(legacy_root),
        )

    assert store.read_bytes() == before
    assert sum(
        record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
        for record in load_text_snapshots(store)
    ) == 1


def test_same_root_orphan_extension_concurrent_replay_appends_one_proof(tmp_path):
    store, _main, legacy_root, old_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, old_head)
    extension = _append_orphan_branch_extension(store, old_head, text="concurrent-extension")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: _merge_orphan(tmp_path, store, legacy_root, extension),
                range(2),
            )
        )

    assert sorted(result["duplicate"] for result in results) == [False, True]
    assert sum(
        record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
        for record in load_text_snapshots(store)
    ) == 2


def test_same_root_orphan_extension_append_crash_recovers_as_duplicate(
    tmp_path, monkeypatch
):
    store, _main, legacy_root, old_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, old_head)
    extension = _append_orphan_branch_extension(store, old_head, text="crash-extension")
    original_append = core.append_text_snapshot
    faulted = False

    def append_then_fail(snapshot, path):
        nonlocal faulted
        original_append(snapshot, path)
        if snapshot.get("event_type") == "discord.snapshot_store.orphan_branch_merged" and not faulted:
            faulted = True
            raise OSError("private details")

    monkeypatch.setattr(core, "append_text_snapshot", append_then_fail)
    with pytest.raises(LegacySnapshotRepairError, match="store_io_error"):
        _merge_orphan(tmp_path, store, legacy_root, extension)
    monkeypatch.setattr(core, "append_text_snapshot", original_append)
    crashed_bytes = store.read_bytes()

    replay = _merge_orphan(tmp_path, store, legacy_root, extension)

    assert replay["duplicate"] is True
    assert store.read_bytes() == crashed_bytes


def test_same_root_orphan_extension_detects_source_cas_change(tmp_path, monkeypatch):
    store, _main, legacy_root, old_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, old_head)
    extension = _append_orphan_branch_extension(store, old_head, text="cas-extension")
    concurrent_row = _legacy_row(url=SOURCE_ALIAS_URL, text="concurrent-observation")

    def mutate_store_before_append():
        _write_records(store, load_text_snapshots(store) + [concurrent_row])
        return "2026-09-01T00:00:09+00:00"

    monkeypatch.setattr(core, "utc_now", mutate_store_before_append)

    with pytest.raises(LegacySnapshotRepairError, match="source_cas_mismatch"):
        _merge_orphan(tmp_path, store, legacy_root, extension)

    assert sum(
        record.get("event_type") == "discord.snapshot_store.orphan_branch_merged"
        for record in load_text_snapshots(store)
    ) == 1


@pytest.mark.parametrize("capture_url", [SOURCE_URL, SOURCE_ALIAS_URL, SOURCE_MESSAGE_URL])
def test_future_snapshot_after_orphan_merge_uses_validated_canonical_main_head(
    tmp_path, capture_url
):
    store, _main, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    merge_result = _merge_orphan(tmp_path, store, legacy_root, branch_head)

    snapshot_visible_text(
        text="future visible snapshot",
        url=capture_url,
        path=store,
    )

    records = load_text_snapshots(store)
    appended = records[-1]
    merge = next(
        record
        for record in records
        if record.get("event_hash") == merge_result["merge_event_hash"]
    )
    assert appended["url"] == SOURCE_URL
    assert appended["target_key"] == stable_text_hash(SOURCE_URL)
    assert appended["previous_event_hash"] == merge["event_hash"]
    assert appended["previous_content_hash"] == ""
    assert appended["stream_sequence"] == merge["stream_sequence"] + 1
    assert core._validated_main_head_for_records(
        records, source_identity=(GUILD, SOURCE_THREAD)
    ) == appended


def _seed_post_merge_main_fork(root: Path):
    store = _store(root, SOURCE_THREAD)
    main_root = _graph_event(
        event_type="message_observation",
        previous_hash="",
        sequence=1,
        text="fork-main-root",
    )
    ancestor = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(main_root["event_hash"]),
        sequence=2,
        text="fork-common-ancestor",
    )
    ancestor["previous_content_hash"] = main_root["content_hash"]
    ancestor["event_hash"] = canonical_event_hash(ancestor)
    main_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(ancestor["event_hash"]),
        sequence=3,
        text="fork-canonical-main",
    )
    main_head["previous_content_hash"] = ancestor["content_hash"]
    main_head["event_hash"] = canonical_event_hash(main_head)
    fork_root = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(ancestor["event_hash"]),
        sequence=3,
        text="post-merge-fork-root",
        url=SOURCE_ALIAS_URL,
    )
    fork_root["previous_content_hash"] = ancestor["content_hash"]
    fork_root["event_hash"] = canonical_event_hash(fork_root)
    fork_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(fork_root["event_hash"]),
        sequence=4,
        text="post-merge-fork-head",
        url=SOURCE_ALIAS_URL,
    )
    fork_head["previous_content_hash"] = fork_root["content_hash"]
    fork_head["event_hash"] = canonical_event_hash(fork_head)
    _write_records(store, [main_root, ancestor, main_head, fork_root, fork_head])
    return store, ancestor, main_head, fork_root, fork_head


def _append_strict_fork_from_main_head(store: Path, main_head: dict) -> dict:
    fork_root = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(main_head["event_hash"]),
        sequence=int(main_head["stream_sequence"]) + 1,
        text="fresh-post-proof-fork",
        url=SOURCE_ALIAS_URL,
    )
    fork_root["previous_content_hash"] = main_head["content_hash"]
    fork_root["event_hash"] = canonical_event_hash(fork_root)
    _write_records(store, load_text_snapshots(store) + [fork_root])
    return fork_root


def test_strict_fork_accepts_valid_orphan_merge_proof_as_remaining_main_child(tmp_path):
    store, main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    first = _merge_orphan(tmp_path, store, legacy_root, branch_head)
    fork_root = _append_strict_fork_from_main_head(store, main_head)

    second = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(fork_root["event_hash"]),
        branch_root_event_hash=str(fork_root["event_hash"]),
    )

    records = load_text_snapshots(store)
    proof = records[-1]
    assert second["saved"] is True
    assert proof["previous_event_hash"] == first["merge_event_hash"]
    assert core._validated_main_head_for_records(
        records, source_identity=(GUILD, SOURCE_THREAD)
    ) == proof


def test_strict_fork_rejects_shape_only_orphan_merge_main_child(tmp_path):
    store, main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, legacy_root, branch_head)
    records = load_text_snapshots(store)
    records[-1]["branch_content_hash"] = "f" * 16
    records[-1]["event_id"] = stable_text_hash(
        "|".join(
            [
                records[-1]["event_type"],
                records[-1]["branch_root_event_hash"],
                records[-1]["branch_head_event_hash"],
                records[-1]["branch_content_hash"],
            ]
        )
    )
    records[-1]["event_hash"] = canonical_event_hash(records[-1])
    _write_records(store, records)
    fork_root = _append_strict_fork_from_main_head(store, main_head)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=str(fork_root["event_hash"]),
            branch_root_event_hash=str(fork_root["event_hash"]),
        )

    assert store.read_bytes() == before


def test_post_merge_main_fork_requires_proof_then_projects_to_single_chain(tmp_path):
    store, _ancestor, main_head, fork_root, fork_head = _seed_post_merge_main_fork(
        tmp_path
    )
    with pytest.raises(LegacySnapshotRepairError, match="main_fork_detected"):
        core._validated_main_head_for_records(
            load_text_snapshots(store), source_identity=(GUILD, SOURCE_THREAD)
        )

    result = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(fork_head["event_hash"]),
        branch_root_event_hash=str(fork_root["event_hash"]),
    )

    assert result["saved"] is True
    merge = load_text_snapshots(store)[-1]
    assert merge["previous_event_hash"] == main_head["event_hash"]
    assert core._validated_main_head_for_records(
        load_text_snapshots(store), source_identity=(GUILD, SOURCE_THREAD)
    ) == merge


def test_disconnected_forged_merge_cannot_hide_canonical_main_head(tmp_path):
    store, _ancestor, main_head, fork_root, fork_head = _seed_post_merge_main_fork(
        tmp_path
    )
    merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(fork_head["event_hash"]),
        branch_root_event_hash=str(fork_root["event_hash"]),
    )
    records = load_text_snapshots(store)
    forged = dict(records[-1])
    forged["branch_root_event_hash"] = main_head["event_hash"]
    forged["branch_head_event_hash"] = main_head["event_hash"]
    forged["branch_content_hash"] = main_head["content_hash"]
    forged["previous_event_hash"] = ""
    forged["event_id"] = stable_text_hash(
        "|".join(
            [
                forged["event_type"],
                forged["branch_root_event_hash"],
                forged["branch_head_event_hash"],
                forged["branch_content_hash"],
            ]
        )
    )
    forged["event_hash"] = canonical_event_hash(forged)
    forged_records = [*records[:-1], forged]

    with pytest.raises(LegacySnapshotRepairError, match="orphan_merge_proof_not_on_main"):
        core._validated_main_head_for_records(
            forged_records, source_identity=(GUILD, SOURCE_THREAD)
        )


@pytest.mark.parametrize("damage", ["main_child_absent", "two_forks", "nonfork_root"])
def test_post_merge_main_fork_root_preconditions_fail_closed(tmp_path, damage):
    store, ancestor, main_head, fork_root, fork_head = _seed_post_merge_main_fork(
        tmp_path
    )
    records = load_text_snapshots(store)
    requested_root = str(fork_root["event_hash"])
    requested_head = str(fork_head["event_hash"])
    if damage == "main_child_absent":
        records.remove(main_head)
    elif damage == "two_forks":
        second_fork = _graph_event(
            event_type="discord.visible_text.snapshot_observed",
            previous_hash=str(ancestor["event_hash"]),
            sequence=3,
            text="second-unproved-fork",
            url=SOURCE_ALIAS_URL,
        )
        second_fork["previous_content_hash"] = ancestor["content_hash"]
        second_fork["event_hash"] = canonical_event_hash(second_fork)
        records.append(second_fork)
    else:
        requested_root = str(main_head["event_hash"])
        requested_head = str(main_head["event_hash"])
    _write_records(store, records)
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=requested_head,
            branch_root_event_hash=requested_root,
        )

    assert store.read_bytes() == before


def test_modern_orphan_branch_root_before_snapshot_main_is_rejected(tmp_path):
    store, _main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)
    records = load_text_snapshots(store)
    _write_records(store, [records[1], records[2], records[0]])
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="branch_is_deterministic_main"):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=str(branch_head["event_hash"]),
            branch_root_event_hash=str(branch_root["event_hash"]),
        )

    assert store.read_bytes() == before


def test_modern_branch_before_unique_message_main_keeps_message_priority(tmp_path):
    store = _store(tmp_path, SOURCE_THREAD)
    branch_root = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash="",
        sequence=1,
        text="early-modern-root",
        url=SOURCE_ALIAS_URL,
    )
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(branch_root["event_hash"]),
        sequence=2,
        text="early-modern-head",
        url=SOURCE_ALIAS_URL,
    )
    branch_head["previous_content_hash"] = branch_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    message_main = _graph_event(
        event_type="message_observation",
        previous_hash="",
        sequence=1,
        text="later-message-main",
    )
    _write_records(store, [branch_root, branch_head, message_main])

    result = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )

    assert result["saved"] is True
    assert load_text_snapshots(store)[-1]["previous_event_hash"] == message_main["event_hash"]


def _seed_message_deeplink_modern_branch(root: Path, *, branch_url: str):
    store = _store(root, SOURCE_THREAD)
    main = _graph_event(
        event_type="message_observation",
        previous_hash="",
        sequence=1,
        text="message-main",
    )
    branch_root = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash="",
        sequence=1,
        text="message-link-root",
        url=branch_url,
    )
    branch_head = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash=str(branch_root["event_hash"]),
        sequence=2,
        text="message-link-head",
        url=branch_url,
    )
    branch_head["previous_content_hash"] = branch_root["content_hash"]
    branch_head["event_hash"] = canonical_event_hash(branch_head)
    _write_records(store, [main, branch_root, branch_head])
    return store, branch_root, branch_head


def test_modern_branch_exact_message_deeplink_merges_and_replays(tmp_path):
    store, branch_root, branch_head = _seed_message_deeplink_modern_branch(
        tmp_path,
        branch_url=SOURCE_MESSAGE_URL,
    )

    first = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )
    merged_bytes = store.read_bytes()
    replay = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )

    assert first["saved"] is True
    assert replay["duplicate"] is True
    assert store.read_bytes() == merged_bytes


@pytest.mark.parametrize(
    "branch_url",
    [
        f"{SOURCE_MESSAGE_URL}?query=1",
        f"{SOURCE_MESSAGE_URL}#fragment",
        ROW_MESSAGE_URL,
    ],
)
def test_modern_branch_message_deeplink_ambiguity_or_wrong_target_is_rejected(tmp_path, branch_url):
    store, branch_root, branch_head = _seed_message_deeplink_modern_branch(
        tmp_path,
        branch_url=branch_url,
    )
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="branch_root_event_invalid"):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=str(branch_head["event_hash"]),
            branch_root_event_hash=str(branch_root["event_hash"]),
        )

    assert store.read_bytes() == before


def test_message_deeplink_snapshot_remains_invalid_as_unmerged_main(tmp_path):
    store = _store(tmp_path, SOURCE_THREAD)
    message_link_main = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash="",
        sequence=1,
        text="invalid-main",
        url=SOURCE_MESSAGE_URL,
    )
    branch_root = _graph_event(
        event_type="discord.visible_text.snapshot_observed",
        previous_hash="",
        sequence=1,
        text="canonical-branch-root",
        url=SOURCE_ALIAS_URL,
    )
    _write_records(store, [message_link_main, branch_root])

    with pytest.raises(LegacySnapshotRepairError, match="source_event_semantic_invalid"):
        merge_orphan_snapshot_branch(
            snapshot_root=tmp_path,
            source_store=store,
            source_target_url=SOURCE_URL,
            branch_head_event_hash=str(branch_root["event_hash"]),
            branch_root_event_hash=str(branch_root["event_hash"]),
        )


def test_modern_snapshot_main_merge_then_three_legacy_repairs_stay_one_chain(tmp_path):
    store, main, branch_root, branch_head = _seed_modern_orphan_branch(tmp_path)
    _merge = merge_orphan_snapshot_branch(
        snapshot_root=tmp_path,
        source_store=store,
        source_target_url=SOURCE_URL,
        branch_head_event_hash=str(branch_head["event_hash"]),
        branch_root_event_hash=str(branch_root["event_hash"]),
    )
    rows = [
        _legacy_row(url=f"{SOURCE_URL}/{int(MESSAGE) + 300 + index}", text=f"modern-repair-{index}")
        for index in range(3)
    ]
    _write_records(store, load_text_snapshots(store) + rows)

    results = [_repair(tmp_path, store, store, canonical_event_hash(row)) for row in rows]

    assert all(result["saved"] is True for result in results)
    by_hash = {
        str(record["event_hash"]): record
        for record in load_text_snapshots(store)
        if record.get("event_hash")
    }
    head = next(
        record
        for record in reversed(load_text_snapshots(store))
        if record.get("event_type") == "discord.snapshot_store.legacy_row_quarantined"
    )
    visited = set()
    current = head
    while current.get("event_hash") not in visited:
        visited.add(current["event_hash"])
        previous = str(current.get("previous_event_hash") or "")
        if not previous:
            break
        current = by_hash[previous]
    assert current["event_hash"] == main["event_hash"]
    assert len(visited) == 8


def test_second_merge_accepts_valid_legacy_correction_on_main(tmp_path):
    store, _main_head, first_root, first_branch = _seed_orphan_branch(tmp_path)
    _merge_orphan(tmp_path, store, first_root, first_branch)
    legacy = _legacy_row(url=SOURCE_MESSAGE_URL, text="repair-before-second-merge")
    _write_records(store, load_text_snapshots(store) + [legacy])
    _repair(tmp_path, store, store, canonical_event_hash(legacy))
    second_root, second_branch = _append_orphan_fixture(store, suffix="after-correction")

    result = _merge_orphan(tmp_path, store, second_root, second_branch)

    assert result["saved"] is True


def test_orphan_merge_rejects_unknown_main_event_type_without_append(tmp_path):
    store, main_head, legacy_root, branch_head = _seed_orphan_branch(tmp_path)
    unknown = _graph_event(
        event_type="discord.unknown",
        previous_hash=str(main_head["event_hash"]),
        sequence=3,
        text="unknown",
    )
    unknown["previous_content_hash"] = main_head["content_hash"]
    unknown["event_hash"] = canonical_event_hash(unknown)
    _write_records(store, load_text_snapshots(store) + [unknown])
    before = store.read_bytes()

    with pytest.raises(LegacySnapshotRepairError, match="main_event_type_unknown"):
        _merge_orphan(tmp_path, store, legacy_root, branch_head)

    assert store.read_bytes() == before
