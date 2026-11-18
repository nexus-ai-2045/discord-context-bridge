from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from discord_context_bridge import core
from discord_context_bridge.capture.store import CheckpointCorruptError


NESTED_URL = "https://discord.com/channels/12345678901234567/23456789012345678/threads/34567890123456789"
FLAT_URL = "https://discord.com/channels/12345678901234567/34567890123456789"
OTHER_THREAD_URL = "https://discord.com/channels/12345678901234567/45678901234567890"


def _continuation(prior: dict, *, url: str, text: str, event_id: str) -> dict:
    row = copy.deepcopy(prior)
    key = core.target_key_for_url(url)
    row.update(
        event_id=event_id,
        url=url,
        target_key=key,
        stream_id=key,
        subject=key,
        stream_sequence=prior["stream_sequence"] + 1,
        expected_previous_stream_sequence=prior["stream_sequence"],
        observation_index_for_target=prior["stream_sequence"] + 1,
        previous_event_hash=prior["event_hash"],
        previous_content_hash=prior["content_hash"],
        text=text,
        content_hash=core.stable_text_hash(text),
    )
    row["event_hash"] = core.canonical_event_hash(row)
    return row


def _append_rows(path: Path, rows: list[dict]) -> None:
    with path.open("ab") as handle:
        for row in rows:
            handle.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))


def _alias_store(path: Path, *, url: str = FLAT_URL) -> list[dict]:
    core.snapshot_visible_text(text="first", url=NESTED_URL, path=path)
    core.snapshot_visible_text(text="second", url=NESTED_URL, path=path)
    prior = core.load_text_snapshots(path)[-1]
    head = _continuation(prior, url=url, text="third", event_id="alias-head")
    tail = _continuation(head, url=url, text="fourth", event_id="alias-tail")
    _append_rows(path, [head, tail])
    return [head, tail]


def test_alias_continuation_blocks_append_until_acknowledged(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)

    original = path.read_bytes()
    assert core.acknowledge_alias_continuations(path, dry_run=True) == {
        "saved": False,
        "ready_to_append": True,
        "acknowledgements": 1,
        "streams": 1,
        "outbound_actions": "disabled",
    }
    assert path.read_bytes() == original

    result = core.acknowledge_alias_continuations(path)
    assert result["saved"] is True and result["acknowledgements"] == 1
    assert path.read_bytes().startswith(original)
    again = core.acknowledge_alias_continuations(path)
    assert again["acknowledgements"] == 0 and again["already_acknowledged"] == 1

    core.snapshot_visible_text(text="new flat", url=FLAT_URL, path=path)
    core.snapshot_visible_text(text="new nested", url=NESTED_URL, path=path)
    rows = core.load_text_snapshots(path)
    acknowledgement = rows[-3]
    assert acknowledgement["event_type"] == "discord.snapshot_store.alias_continuation_acknowledged"
    assert acknowledgement["stream_sequence"] == 5
    assert rows[-2]["stream_sequence"] == 6
    assert rows[-2]["previous_event_hash"] == acknowledgement["event_hash"]
    assert rows[-1]["stream_sequence"] == 3


def test_interleaved_alias_streams_are_acknowledged_together(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    second_nested = "https://discord.com/channels/12345678901234567/23456789012345678/threads/56789012345678901"
    second_flat = "https://discord.com/channels/12345678901234567/56789012345678901"
    core.snapshot_visible_text(text="a1", url=NESTED_URL, path=path)
    core.snapshot_visible_text(text="b1", url=second_nested, path=path)
    rows = core.load_text_snapshots(path)
    a_head = _continuation(rows[0], url=FLAT_URL, text="a2", event_id="a-head")
    b_head = _continuation(rows[1], url=second_flat, text="b2", event_id="b-head")
    a_tail = _continuation(a_head, url=FLAT_URL, text="a3", event_id="a-tail")
    _append_rows(path, [a_head, b_head, a_tail])
    original = path.read_bytes()
    result = core.acknowledge_alias_continuations(path)
    assert result["acknowledgements"] == 2 and result["streams"] == 2
    assert path.read_bytes().startswith(original)
    core.snapshot_visible_text(text="b3", url=second_flat, path=path)
    assert core.load_text_snapshots(path)[-1]["stream_sequence"] == 4


@pytest.mark.parametrize(
    "host", ["canary.discord.com", "ptb.discord.com", "discordapp.com"]
)
def test_supported_host_variant_is_acknowledged(tmp_path, host):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path, url=FLAT_URL.replace("discord.com", host, 1))
    result = core.acknowledge_alias_continuations(path)
    assert result["acknowledgements"] == 1
    core.load_content_snapshot_records(path)


def test_continuation_from_acknowledgement_head_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    rows = core.load_text_snapshots(path)
    acknowledgement, observation = rows[-1], rows[-2]
    third = _continuation(
        observation,
        url=FLAT_URL.replace("discord.com", "canary.discord.com", 1),
        text="fifth",
        event_id="third-alias",
    )
    third.update(
        stream_sequence=acknowledgement["stream_sequence"] + 1,
        expected_previous_stream_sequence=acknowledgement["stream_sequence"],
        observation_index_for_target=acknowledgement["stream_sequence"] + 1,
        previous_event_hash=acknowledgement["event_hash"],
    )
    third["event_hash"] = core.canonical_event_hash(third)
    _append_rows(path, [third])
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    assert path.read_bytes() == original


def _bad_row_after_acknowledgement(path: Path) -> dict:
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    acknowledgement = core.load_text_snapshots(path)[-1]
    bad_url = FLAT_URL.replace("discord.com", "ptb.discord.com", 1)
    bad = copy.deepcopy(core.load_text_snapshots(path)[-2])
    bad.update(
        event_id="bad-after-acknowledgement",
        url=bad_url,
        stream_id=core.target_key_for_url(bad_url),
        stream_sequence=acknowledgement["stream_sequence"] + 1,
        expected_previous_stream_sequence=acknowledgement["stream_sequence"],
        previous_event_hash=acknowledgement["event_hash"],
        previous_content_hash=acknowledgement["content_hash"],
        text="foreign text",
        content_hash=core.stable_text_hash("foreign text"),
    )
    for key in ("target_key", "subject", "dataschema", "type", "datacontenttype"):
        bad.pop(key, None)
    _append_rows(path, [bad])
    return acknowledgement


def test_malformed_row_after_acknowledgement_head_can_be_revoked(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    acknowledgement = _bad_row_after_acknowledgement(path)
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)
    original = path.read_bytes()

    result = core.revoke_invalid_snapshot_observation(path)
    assert result["saved"] is True
    assert path.read_bytes().startswith(original)
    revocation = core.load_text_snapshots(path)[-1]
    assert revocation["previous_event_hash"] == acknowledgement["event_hash"]
    assert revocation["content_hash"] == acknowledgement["content_hash"]
    core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)
    texts = [row["text"] for row in core.load_content_snapshot_records(path)]
    assert "foreign text" not in texts and texts[-1] == "new"


def test_byte_identical_acknowledgement_replay_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    last_line = path.read_bytes().rstrip(b"\n").split(b"\n")[-1]
    with path.open("ab") as handle:
        handle.write(last_line + b"\n")
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)


def test_byte_identical_revocation_replay_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_row_after_acknowledgement(path)
    core.revoke_invalid_snapshot_observation(path)
    last_line = path.read_bytes().rstrip(b"\n").split(b"\n")[-1]
    with path.open("ab") as handle:
        handle.write(last_line + b"\n")
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)


def test_acknowledgement_before_its_row_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    acknowledgement = lines[-1]
    reordered = lines[:2] + [acknowledgement] + lines[2:-1]
    path.write_bytes(b"\n".join(reordered) + b"\n")
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)


def test_acknowledgement_is_not_counted_as_content(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    records = core.load_content_snapshot_records(path)
    assert [row["text"] for row in records] == ["first", "second", "third", "fourth"]
    matches = core.matching_snapshot_records(path, url=FLAT_URL, target_key=core.target_key_for_url(FLAT_URL))
    assert [row["text"] for row in matches] == ["third", "fourth"]


def test_acknowledged_row_byte_change_is_detected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    data = path.read_bytes()
    head_line = next(line for line in data.split(b"\n") if b'"alias-head"' in line)
    changed = json.loads(head_line)
    compact = json.dumps(changed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert compact != head_line
    path.write_bytes(data.replace(head_line, compact))
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)


def test_different_thread_is_not_acknowledged(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path, url=OTHER_THREAD_URL)
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    assert path.read_bytes() == original


def test_predecessor_must_be_current_head(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url=NESTED_URL, path=path)
    core.snapshot_visible_text(text="second", url=NESTED_URL, path=path)
    stale = core.load_text_snapshots(path)[-1]
    core.snapshot_visible_text(text="later", url=NESTED_URL, path=path)
    _append_rows(path, [_continuation(stale, url=FLAT_URL, text="third", event_id="alias-head")])
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    assert path.read_bytes() == original


def test_forged_acknowledgement_for_normal_row_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    core.acknowledge_alias_continuations(path)
    rows = core.load_text_snapshots(path)
    forged = copy.deepcopy(rows[-1])
    forged["acknowledged_event_id"] = rows[0]["event_id"]
    forged["stream_sequence"] += 1
    forged["expected_previous_stream_sequence"] += 1
    forged["previous_event_hash"] = rows[-1]["event_hash"]
    forged["event_hash"] = core.canonical_event_hash(forged)
    _append_rows(path, [forged])
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)


def test_tampered_acknowledgement_binding_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    original = path.read_bytes()
    core.acknowledge_alias_continuations(path)
    acknowledgement = core.load_text_snapshots(path)[-1]
    acknowledgement["predecessor_stream_sequence"] = 1
    acknowledgement["event_hash"] = core.canonical_event_hash(acknowledgement)
    path.write_bytes(original)
    _append_rows(path, [acknowledgement])
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)


def test_cli_is_read_only_by_default_and_metadata_only(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    original = path.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts" / "acknowledge_alias_continuation.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--snapshot-store", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert path.read_bytes() == original
    output = json.loads(completed.stdout)
    assert output["ready_to_append"] is True
    assert "third" not in completed.stdout
    assert "discord.com" not in completed.stdout
    assert str(tmp_path) not in completed.stdout


def _append_foreign_bad_row(path: Path, predecessor: dict) -> None:
    bad_url = FLAT_URL.replace("discord.com", "ptb.discord.com", 1)
    bad = copy.deepcopy(predecessor)
    bad.update(
        event_id="foreign-bad-row",
        url=bad_url,
        stream_id=core.target_key_for_url(bad_url),
        stream_sequence=predecessor["stream_sequence"] + 1,
        expected_previous_stream_sequence=predecessor["stream_sequence"],
        previous_event_hash=predecessor["event_hash"],
        previous_content_hash=predecessor["content_hash"],
        text="foreign text",
        content_hash=core.stable_text_hash("foreign text"),
    )
    for key in ("target_key", "subject", "dataschema", "type", "datacontenttype"):
        bad.pop(key, None)
    _append_rows(path, [bad])


def test_unacknowledged_alias_with_trailing_bad_row_recovers_by_revoke_then_acknowledge(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _head, tail = _alias_store(path)
    _append_foreign_bad_row(path, tail)
    original = path.read_bytes()

    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    assert path.read_bytes() == original
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)

    assert core.revoke_invalid_snapshot_observation(path)["saved"] is True
    assert core.revoke_invalid_snapshot_observation(path)["already_revoked"] is True
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)

    assert core.acknowledge_alias_continuations(path)["acknowledgements"] == 1
    assert core.acknowledge_alias_continuations(path)["acknowledgements"] == 0
    recovered = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.revoke_invalid_snapshot_observation(path)
    assert path.read_bytes() == recovered
    assert recovered.startswith(original)

    core.snapshot_visible_text(text="new", url=FLAT_URL, path=path)
    texts = [row["text"] for row in core.load_content_snapshot_records(path)]
    assert texts == ["first", "second", "third", "fourth", "new"]


def test_replayed_revocation_after_alias_recovery_is_rejected(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _head, tail = _alias_store(path)
    _append_foreign_bad_row(path, tail)
    core.revoke_invalid_snapshot_observation(path)
    core.acknowledge_alias_continuations(path)
    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    with path.open("ab") as handle:
        handle.write(lines[-2] + b"\n")
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)


@pytest.mark.parametrize("field", ["content_hash", "previous_content_hash"])
def test_every_row_of_a_recovered_alias_stream_has_content_integrity(tmp_path, field):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url=NESTED_URL, path=path)
    core.snapshot_visible_text(text="second", url=NESTED_URL, path=path)
    prior = core.load_text_snapshots(path)[-1]
    head = _continuation(prior, url=FLAT_URL, text="third", event_id="alias-head")
    tail = _continuation(head, url=FLAT_URL, text="fourth", event_id="alias-tail")
    tail[field] = core.stable_text_hash("forged")
    tail["event_hash"] = core.canonical_event_hash(tail)
    _append_rows(path, [head, tail])
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "tail_url",
    [OTHER_THREAD_URL, FLAT_URL.replace("discord.com", "ptb.discord.com", 1)],
    ids=["other_thread", "same_thread_other_spelling"],
)
@pytest.mark.parametrize("acknowledge_first", [False, True], ids=["before_ack", "after_ack"])
def test_every_row_of_a_recovered_alias_stream_keeps_its_url_binding(tmp_path, tail_url, acknowledge_first):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url=NESTED_URL, path=path)
    core.snapshot_visible_text(text="second", url=NESTED_URL, path=path)
    prior = core.load_text_snapshots(path)[-1]
    head = _continuation(prior, url=FLAT_URL, text="third", event_id="alias-head")
    _append_rows(path, [head])
    if acknowledge_first:
        core.acknowledge_alias_continuations(path)
    predecessor = core.load_text_snapshots(path)[-1]
    tail = _continuation(head, url=FLAT_URL, text="fourth", event_id="alias-tail")
    tail.update(
        url=tail_url,
        stream_sequence=predecessor["stream_sequence"] + 1,
        expected_previous_stream_sequence=predecessor["stream_sequence"],
        observation_index_for_target=predecessor["stream_sequence"] + 1,
        previous_event_hash=predecessor["event_hash"],
    )
    tail["event_hash"] = core.canonical_event_hash(tail)
    _append_rows(path, [tail])
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("acknowledge_first", [False, True], ids=["before_ack", "after_ack"])
def test_legacy_row_cannot_extend_a_recovered_alias_stream(tmp_path, acknowledge_first):
    path = tmp_path / "text-snapshots.ndjson"
    core.snapshot_visible_text(text="first", url=NESTED_URL, path=path)
    core.snapshot_visible_text(text="second", url=NESTED_URL, path=path)
    prior = core.load_text_snapshots(path)[-1]
    _append_rows(path, [_continuation(prior, url=FLAT_URL, text="third", event_id="alias-head")])
    if acknowledge_first:
        core.acknowledge_alias_continuations(path)
    legacy = {
        "target_key": core.target_key_for_url(FLAT_URL),
        "url": FLAT_URL,
        "captured_at": "2026-10-15T00:00:00+00:00",
        "source": "legacy",
        "text": "legacy tail",
    }
    _append_rows(path, [legacy])
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    with pytest.raises(CheckpointCorruptError):
        core.load_content_snapshot_records(path)
    assert path.read_bytes() == original


def test_alias_continuation_requires_a_chained_predecessor_with_identity(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    legacy = {
        "target_key": core.target_key_for_url(NESTED_URL),
        "url": NESTED_URL,
        "captured_at": "2026-10-15T00:00:00+00:00",
        "source": "legacy",
        "event_type": "discord.visible_text.snapshot_observed",
        "text": "legacy head",
        "content_hash": core.stable_text_hash("legacy head"),
    }
    _append_rows(path, [legacy])
    stored = core.load_text_snapshots(path)[-1]
    template_path = tmp_path / "template.ndjson"
    core.snapshot_visible_text(text="template", url=FLAT_URL, path=template_path)
    continuation = core.load_text_snapshots(template_path)[-1]
    continuation.update(
        event_id="alias-head",
        stream_sequence=2,
        expected_previous_stream_sequence=1,
        observation_index_for_target=2,
        previous_event_hash=core._durable_snapshot_event_hash(stored),
        previous_content_hash=stored["content_hash"],
        text="third",
        content_hash=core.stable_text_hash("third"),
    )
    continuation["event_hash"] = core.canonical_event_hash(continuation)
    _append_rows(path, [continuation])
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.acknowledge_alias_continuations(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize("acknowledged", [True, False], ids=["truncated_acknowledgement", "chained_without_recovery"])
def test_unparseable_line_in_chained_ledger_is_never_read_as_content(tmp_path, acknowledged):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    if acknowledged:
        core.acknowledge_alias_continuations(path)
        lines = path.read_bytes().rstrip(b"\n").split(b"\n")
        path.write_bytes(b"\n".join(lines[:-1] + [lines[-1][: len(lines[-1]) // 2]]) + b"\n")
    else:
        with path.open("ab") as handle:
            handle.write(b'{"text": "broken\n')
    key = core.target_key_for_url(FLAT_URL)
    with pytest.raises(ValueError):
        core.matching_snapshot_records(path, url=FLAT_URL, target_key=key)
    with pytest.raises(ValueError):
        core.load_content_snapshot_records(path)


def test_plain_text_fallback_still_reads_non_ledger_caches(tmp_path):
    path = tmp_path / "raw-cache.txt"
    path.write_text(f"seen at {FLAT_URL}\n", encoding="utf-8")
    records = core.matching_snapshot_records(path, url=FLAT_URL, target_key="unused")
    assert [record["source_format"] for record in records] == ["plain_text"]


def test_canonical_store_matching_rejects_unacknowledged_alias_rows(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _alias_store(path)
    key = core.target_key_for_url(FLAT_URL)
    with pytest.raises(CheckpointCorruptError):
        core.matching_snapshot_records(path, url=FLAT_URL, target_key=key, canonical=True)
    core.acknowledge_alias_continuations(path)
    matches = core.matching_snapshot_records(path, url=FLAT_URL, target_key=key, canonical=True)
    assert [row["text"] for row in matches] == ["third", "fourth"]


def test_canonical_store_matching_never_falls_back_to_plain_text(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    path.write_text(f"seen at {FLAT_URL}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        core.matching_snapshot_records(path, url=FLAT_URL, target_key="unused", canonical=True)
    assert core.matching_snapshot_records(path, url=FLAT_URL, target_key="unused")[0]["source_format"] == "plain_text"
