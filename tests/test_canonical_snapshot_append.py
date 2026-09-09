from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from discord_context_bridge import core
from discord_context_bridge.capture import store as capture_store
from discord_context_bridge.capture.store import (
    CheckpointCorruptError,
    SequenceConflictError,
)


def _next_event(previous: dict[str, object], *, event_id: str, text: str) -> dict[str, object]:
    event = copy.deepcopy(previous)
    sequence = int(previous["stream_sequence"]) + 1
    event.update(
        {
            "event_id": event_id,
            "stream_sequence": sequence,
            "expected_previous_stream_sequence": int(previous["stream_sequence"]),
            "previous_event_hash": previous["event_hash"],
            "text": text,
            "content_hash": core.stable_text_hash(text),
            "observation_index_for_target": sequence,
        }
    )
    event["event_hash"] = core.canonical_event_hash(event)
    return event


def test_parallel_writers_with_same_head_cannot_both_append(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    previous = core.load_text_snapshots(path)[0]
    candidates = [
        _next_event(previous, event_id="parallel-a", text="second-a"),
        _next_event(previous, event_id="parallel-b", text="second-b"),
    ]
    barrier = threading.Barrier(2)

    def append(candidate: dict[str, object]) -> str:
        barrier.wait()
        try:
            core.append_text_snapshot(candidate, path)
        except SequenceConflictError:
            return "conflict"
        return "appended"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(append, candidates))

    assert sorted(results) == ["appended", "conflict"]
    assert len(core.load_text_snapshots(path)) == 2


def test_replaying_same_event_is_idempotent(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    event = core.load_text_snapshots(path)[0]

    core.append_text_snapshot(event, path)

    assert core.load_text_snapshots(path) == [event]


def test_stale_expected_head_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    previous = core.load_text_snapshots(path)[0]
    candidate = _next_event(previous, event_id="stale-event", text="second")
    candidate["expected_previous_stream_sequence"] = 0
    candidate["event_hash"] = core.canonical_event_hash(candidate)

    with pytest.raises(SequenceConflictError):
        core.append_text_snapshot(candidate, path)

    assert core.load_text_snapshots(path) == [previous]


def test_failed_partial_append_is_rolled_back(tmp_path, monkeypatch):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    previous = core.load_text_snapshots(path)[0]
    candidate = _next_event(previous, event_id="partial-event", text="second")
    before = path.read_bytes()
    real_write = capture_store.os.write
    writes = 0

    def partial_then_fail(descriptor: int, content: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 1:
            return real_write(descriptor, content[: max(1, len(content) // 2)])
        raise OSError("injected partial append failure")

    monkeypatch.setattr(capture_store.os, "write", partial_then_fail)

    with pytest.raises(CheckpointCorruptError):
        core.append_text_snapshot(candidate, path)

    assert path.read_bytes() == before


def test_tampered_hash_chain_is_rejected_before_next_append(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    tampered = core.load_text_snapshots(path)[0]
    tampered["text"] = "tampered"
    path.write_text(core.json.dumps(tampered, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="second", url="https://example.invalid/a", path=path)

    assert len(core.load_text_snapshots(path)) == 1


@pytest.mark.parametrize("field,value", [
    ("stream_sequence", 1.0), ("stream_sequence", True),
    ("expected_previous_stream_sequence", 0.0), ("expected_previous_stream_sequence", False),
])
def test_noninteger_candidate_never_reaches_disk(tmp_path, field, value):
    seed = tmp_path / "seed.ndjson"
    core.snapshot_visible_text(text="seed", url="https://example.invalid/a", path=seed)
    candidate = core.load_text_snapshots(seed)[0]
    candidate[field] = value
    candidate["event_hash"] = core.canonical_event_hash(candidate)
    path = tmp_path / "target.ndjson"
    with pytest.raises(CheckpointCorruptError):
        core.append_text_snapshot(candidate, path)
    assert not path.exists()


def test_imported_raw_stream_is_keyed_by_canonical_target(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    url = "https://example.invalid/canonical"
    core.append_snapshot_like_record(
        path, {"schema": "dcb.incremental_visible_message.v1", "stream_id": "raw-session", "text": "raw"},
        url=url, target_key=core.stable_text_hash(url),
    )
    core.snapshot_visible_text(text="next", url=url, path=path)
    rows = core.load_text_snapshots(path)
    assert rows[0]["stream_id"] == "raw-session"
    assert rows[1]["stream_sequence"] == 2
    core._validate_text_snapshot_chain(rows)


@pytest.mark.parametrize("field", ["stream_sequence", "observation_index_for_target"])
def test_raw_upstream_sequence_does_not_override_validated_head(tmp_path, field):
    path = tmp_path / "ledger.ndjson"
    url = "https://example.invalid/raw"
    core.append_snapshot_like_record(path, {
        "schema": "dcb.incremental_visible_message.v1", "text": "raw", field: 42,
    }, url=url, target_key=core.stable_text_hash(url))
    core.snapshot_visible_text(text="next", url=url, path=path)
    rows = core.load_text_snapshots(path)
    assert rows[-1]["expected_previous_stream_sequence"] == 1
    assert rows[-1]["stream_sequence"] == 2
    core._validate_text_snapshot_chain(rows)


def test_event_bound_raw_import_without_timestamp_replays(tmp_path, monkeypatch):
    path = tmp_path / "ledger.ndjson"
    record = {"schema": "dcb.incremental_visible_message.v1", "event_id": "stable-event", "text": "raw"}
    for timestamp in ("2026-09-10T00:00:00Z", "2026-09-10T01:00:00Z"):
        monkeypatch.setattr(core, "utc_now", lambda value=timestamp: value)
        core.append_snapshot_like_record(path, record, url="https://example.invalid/raw", target_key="target")
    assert len(core.load_text_snapshots(path)) == 1


def test_identical_legacy_replay_rows_remain_appendable(tmp_path):
    path = tmp_path / "legacy.ndjson"
    url = "https://example.invalid/a"
    row = {"schema": "dcb.incremental_visible_message.v1", "event_id": "old", "target_key": core.stable_text_hash(url), "text": "legacy"}
    original = (core.json.dumps(row) + "\n") * 2
    path.write_text(original, encoding="utf-8")
    core.snapshot_visible_text(text="next", url=url, path=path)
    assert path.read_text().startswith(original)
    rows = core.load_text_snapshots(path)
    assert rows[-1]["stream_sequence"] == 3
    assert not core.append_text_snapshot(row, path)


@pytest.mark.parametrize("rebind", [False, True])
@pytest.mark.parametrize("seeded", [False, True])
def test_canonical_cache_import_rechains_against_destination(tmp_path, rebind, seeded):
    source = tmp_path / "source.ndjson"
    url = "https://example.invalid/a"
    core.snapshot_visible_text(text="one", url=url, path=source)
    core.snapshot_visible_text(text="two", url=url, path=source)
    record = core.load_text_snapshots(source)[-1]
    destination = tmp_path / "destination.ndjson"
    if rebind:
        url = "https://example.invalid/b"
    target = core.stable_text_hash(url)
    if seeded:
        core.snapshot_visible_text(text="destination", url=url, path=destination)
    core.append_snapshot_like_record(destination, record, url=url, target_key=target)
    before = destination.read_bytes()
    core.append_snapshot_like_record(destination, record, url=url, target_key=target)
    assert destination.read_bytes() == before
    rows = core.load_text_snapshots(destination)
    assert rows[-1]["stream_sequence"] == (2 if seeded else 1)
    assert rows[-1]["target_key"] == rows[-1]["stream_id"] == target
    assert rows[-1]["text"] == record["text"]
    assert rows[-1]["captured_at"] == record["captured_at"]
    core.snapshot_visible_text(text="next", url=url, path=destination)
    core._validate_text_snapshot_chain(core.load_text_snapshots(destination))


def test_partial_canonical_cache_sync_and_conflicting_import(tmp_path):
    source = tmp_path / "source.ndjson"
    url = "https://example.invalid/a"
    core.snapshot_visible_text(text="one", url=url, path=source)
    core.snapshot_visible_text(text="two", url=url, path=source)
    record = core.load_text_snapshots(source)[-1]
    partial = tmp_path / "partial.ndjson"
    partial.write_text(core.json.dumps(record) + "\n", encoding="utf-8")
    destination = tmp_path / "destination.ndjson"
    core.build_url_intake_gate(url, raw_cache_path=partial, ai_log_path=destination, sync=True)
    assert core.load_text_snapshots(destination)[0]["stream_sequence"] == 1
    before = destination.read_bytes()
    corrupted = dict(record, text="changed")
    with pytest.raises(CheckpointCorruptError):
        core.append_snapshot_like_record(destination, corrupted, url=url, target_key=core.stable_text_hash(url))
    corrupted["event_hash"] = core.canonical_event_hash(corrupted)
    with pytest.raises(capture_store.EventConflictError):
        core.append_snapshot_like_record(destination, corrupted, url=url, target_key=core.stable_text_hash(url))
    assert destination.read_bytes() == before


@pytest.mark.parametrize("updates", [
    {"stream_sequence": -10}, {"stream_sequence": True},
    {"expected_previous_stream_sequence": True}, {"expected_previous_stream_sequence": -1},
    {"stream_sequence": 5}, {"previous_event_hash": 123},
    {"previous_event_hash": ""},
])
def test_import_rejects_locally_invalid_source_envelope(tmp_path, updates):
    source = tmp_path / "source.ndjson"
    url = "https://example.invalid/a"
    core.snapshot_visible_text(text="one", url=url, path=source)
    core.snapshot_visible_text(text="two", url=url, path=source)
    row = dict(core.load_text_snapshots(source)[-1], **updates)
    row["event_hash"] = core.canonical_event_hash(row)
    destination = tmp_path / "destination.ndjson"
    with pytest.raises(CheckpointCorruptError):
        core.append_snapshot_like_record(destination, row, url=url, target_key=core.stable_text_hash(url))
    assert not destination.exists()


def test_import_accepts_successor_of_opaque_legacy_hash(tmp_path):
    source = tmp_path / "source.ndjson"
    url = "https://example.invalid/legacy"
    target = core.stable_text_hash(url)
    core.append_text_snapshot({
        "target_key": target, "event_id": "legacy", "event_hash": "upstream-opaque-hash",
        "text": "old", "content_hash": core.stable_text_hash("old"),
    }, source)
    core.snapshot_visible_text(text="new", url=url, path=source)
    rows = core.load_text_snapshots(source)
    core._validate_text_snapshot_chain(rows)
    assert rows[-1]["previous_event_hash"] == "upstream-opaque-hash"
    destination = tmp_path / "destination.ndjson"
    core.append_snapshot_like_record(destination, rows[-1], url=url, target_key=target)
    imported = core.load_text_snapshots(destination)
    assert imported[0]["import_source_event_hash"] == rows[-1]["event_hash"]
    assert imported[0]["previous_event_hash"] == ""
    core._validate_text_snapshot_chain(imported)


@pytest.mark.parametrize("source_duplicate", [False, True])
@pytest.mark.parametrize("destination_text", [None, "same", "different"])
def test_import_recomputes_content_flags_at_destination(tmp_path, source_duplicate, destination_text):
    source = tmp_path / "source.ndjson"
    url = "https://example.invalid/flags"
    core.snapshot_visible_text(text="same" if source_duplicate else "old", url=url, path=source)
    core.snapshot_visible_text(text="same", url=url, path=source)
    record = core.load_text_snapshots(source)[-1]
    assert record["duplicate_content"] is source_duplicate
    destination = tmp_path / "destination.ndjson"
    if destination_text is not None:
        core.snapshot_visible_text(text=destination_text, url=url, path=destination)
    target = core.stable_text_hash(url)
    core.append_snapshot_like_record(destination, record, url=url, target_key=target)
    rows = core.load_text_snapshots(destination)
    imported = rows[-1]
    assert imported["previous_content_hash"] == (
        core.stable_text_hash(destination_text) if destination_text is not None else None
    )
    assert imported["duplicate_content"] is (destination_text == "same")
    assert imported["changed"] is (destination_text != "same")
    assert imported["captured_at"] == record["captured_at"]
    core._validate_text_snapshot_chain(rows)
    before = destination.read_bytes()
    core.append_snapshot_like_record(destination, record, url=url, target_key=target)
    assert destination.read_bytes() == before


def test_conflicting_legacy_event_ids_still_fail_closed(tmp_path):
    path = tmp_path / "legacy.ndjson"
    row = {"event_id": "old", "target_key": "target", "text": "a"}
    path.write_text(core.json.dumps(row) + "\n" + core.json.dumps(dict(row, text="b")) + "\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.append_text_snapshot({"target_key": "target", "text": "next"}, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", [("a", "b"), {1: "a"}])
@pytest.mark.parametrize("event_bound", [False, True])
def test_json_normalized_candidate_matches_readback_and_replay(tmp_path, value, event_bound):
    path = tmp_path / "ledger.ndjson"
    candidate = {"target_key": "target", "value": value}
    if event_bound:
        candidate["event_id"] = "event"
    assert core.append_text_snapshot(candidate, path)
    normalized = core.json.loads(core.json.dumps(candidate))
    assert core.load_text_snapshots(path) == [normalized]
    if event_bound:
        assert core.append_text_snapshot(candidate, path) is False
        assert core.load_text_snapshots(path) == [normalized]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {"not-json"}])
def test_unserializable_batch_is_rejected_before_any_append(tmp_path, value):
    path = tmp_path / "ledger.ndjson"
    core.append_text_snapshot({"target_key": "target", "text": "seed"}, path)
    before = path.read_bytes()
    with pytest.raises((ValueError, TypeError)):
        core._append_text_snapshots_transaction(lambda _: [
            {"target_key": "target", "text": "valid"},
            {"target_key": "target", "value": value},
        ], path)
    assert path.read_bytes() == before


def test_invalid_later_batch_event_cannot_partially_commit(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    first = _next_event(core.load_text_snapshots(path)[0], event_id="second", text="second")
    invalid = _next_event(first, event_id="third", text="third")
    invalid["stream_sequence"] = 3.0
    invalid["event_hash"] = core.canonical_event_hash(invalid)
    before = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core._append_text_snapshots_transaction(lambda _: [first, invalid], path)
    assert path.read_bytes() == before


def test_batch_replay_is_idempotent(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    first = _next_event(core.load_text_snapshots(path)[0], event_id="second", text="second")
    second = _next_event(first, event_id="third", text="third")
    count, _, _ = core._append_text_snapshots_transaction(lambda _: [first, second], path)
    assert count == 2
    before = path.read_bytes()
    assert core._append_text_snapshots_transaction(lambda _: [first, second], path)[0] == 0
    assert path.read_bytes() == before


def test_chained_snapshot_rejects_mismatched_stream_and_target_before_write(tmp_path):
    seed_path = tmp_path / "seed.ndjson"
    core.snapshot_visible_text(text="seed", url="https://example.invalid/a", path=seed_path)
    candidate = dict(core.load_text_snapshots(seed_path)[0])
    candidate["stream_id"] = "different-stream"
    candidate["event_hash"] = core.canonical_event_hash(candidate)
    target_path = tmp_path / "target.ndjson"

    with pytest.raises(CheckpointCorruptError, match="stream binding"):
        core.append_text_snapshot(candidate, target_path)

    assert not target_path.exists()


@pytest.mark.parametrize("schema", [None, "dcb.raw_capture.v1", "unknown"])
@pytest.mark.parametrize("rehash", [False, True])
@pytest.mark.parametrize("persisted", [False, True])
def test_chain_envelope_cannot_be_downgraded_to_legacy(tmp_path, schema, rehash, persisted):
    source = tmp_path / "source.ndjson"
    url = "https://example.invalid/a"
    core.snapshot_visible_text(text="seed", url=url, path=source)
    row = core.load_text_snapshots(source)[0]
    if schema is None:
        del row["schema"]
    else:
        row["schema"] = schema
    if rehash:
        row["event_hash"] = core.canonical_event_hash(row)
    path = tmp_path / "ledger.ndjson"
    if persisted:
        path.write_text(core.json.dumps(row) + "\n", encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(CheckpointCorruptError):
        if persisted:
            core.snapshot_visible_text(text="next", url=url, path=path)
        else:
            core.append_text_snapshot(row, path)
    assert (path.read_bytes() if path.exists() else None) == before


def test_writer_lock_is_scoped_to_selected_ledger(tmp_path):
    first = tmp_path / "current.ndjson"
    second = tmp_path / "archive.ndjson"
    first_lock = core._text_snapshot_lock_id(first)
    second_lock = core._text_snapshot_lock_id(second)
    assert first_lock != second_lock

    with core.CaptureCheckpointStore(tmp_path).transition_lock(first_lock):
        core.snapshot_visible_text(text="other", url="https://example.invalid/b", path=second)
        with pytest.raises(SequenceConflictError):
            core.snapshot_visible_text(text="same", url="https://example.invalid/a", path=first)

    assert len(core.load_text_snapshots(second)) == 1
    assert not first.exists()


def test_legacy_event_hash_is_the_next_event_previous_hash(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    url = "https://example.invalid/legacy"
    legacy_hash = "legacy-upstream-event-hash"
    core.append_snapshot_like_record(
        path,
        {
            "schema": "dcb.incremental_visible_message.v1",
            "stream_id": "raw-session",
            "event_hash": legacy_hash,
            "text": "legacy",
        },
        url=url,
        target_key=core.stable_text_hash(url),
    )

    core.snapshot_visible_text(text="next", url=url, path=path)

    rows = core.load_text_snapshots(path)
    assert rows[1]["previous_event_hash"] == legacy_hash
    core._validate_text_snapshot_chain(rows)


def test_batch_append_serializes_rows_as_bounded_chunks(tmp_path, monkeypatch):
    path = tmp_path / "text-snapshots.ndjson"
    observed_chunk_counts: list[int] = []
    real_append = capture_store._append_store_relative_chunks

    def observe_chunks(root, selected, chunks):
        materialized = iter(chunks)

        def counted():
            count = 0
            for chunk in materialized:
                count += 1
                yield chunk
            observed_chunk_counts.append(count)

        return real_append(root, selected, counted())

    monkeypatch.setattr(core, "_append_store_relative_chunks", observe_chunks)
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    first_row = core.load_text_snapshots(path)[0]
    second = _next_event(first_row, event_id="second", text="second")
    third = _next_event(second, event_id="third", text="third")

    core._append_text_snapshots_transaction(lambda _: [second, third], path)

    assert observed_chunk_counts == [1, 2]


def test_failure_during_streamed_batch_rolls_back_every_chunk(tmp_path, monkeypatch):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url="https://example.invalid/a", path=path)
    first_row = core.load_text_snapshots(path)[0]
    second = _next_event(first_row, event_id="second", text="second")
    third = _next_event(second, event_id="third", text="third")
    before = path.read_bytes()
    real_write_all = capture_store._write_all
    writes = 0

    def fail_second_chunk(descriptor, content):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected second chunk failure")
        real_write_all(descriptor, content)

    monkeypatch.setattr(capture_store, "_write_all", fail_second_chunk)

    with pytest.raises(CheckpointCorruptError):
        core._append_text_snapshots_transaction(lambda _: [second, third], path)

    assert path.read_bytes() == before
