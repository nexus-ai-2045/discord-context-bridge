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
