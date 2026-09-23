from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from discord_context_bridge import core
from discord_context_bridge.capture.store import CheckpointCorruptError


IAB_URL = "https://discord.com/channels/12345678901234567/23456789012345678/threads/34567890123456789"
CHROME_URL = "https://discord.com/channels/12345678901234567/34567890123456789"


def _bad_store(path, *, bad_url=CHROME_URL, bad_stream_id=None):
    core.snapshot_visible_text(text="first", url=IAB_URL, path=path)
    core.snapshot_visible_text(text="second", url=IAB_URL, path=path)
    original = path.read_bytes()
    prior = core.load_text_snapshots(path)[-1]
    bad = copy.deepcopy(prior)
    bad.update(
        event_id="bad-foreign-event",
        url=bad_url,
        stream_id=bad_stream_id or core.target_key_for_url(bad_url),
        stream_sequence=3,
        expected_previous_stream_sequence=2,
        previous_event_hash=prior["event_hash"],
        previous_content_hash=prior["content_hash"],
        content_hash=core.stable_text_hash("foreign text"),
        text="foreign text",
    )
    for key in ("target_key", "subject", "dataschema", "type", "datacontenttype"):
        bad.pop(key, None)
    path.write_bytes(original + (json.dumps(bad, ensure_ascii=False) + "\n").encode())
    return original, bad


def test_revocation_preserves_original_and_normal_append_resumes_only_after_proof(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path)
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="new", url=IAB_URL, path=path)
    result = core.revoke_invalid_snapshot_observation(path)
    assert result == {"saved": True, "already_revoked": False, "outbound_actions": "disabled"}
    assert path.read_bytes().startswith(original)
    assert core.revoke_invalid_snapshot_observation(path)["already_revoked"] is True
    core.snapshot_visible_text(text="new", url=IAB_URL, path=path)
    rows = core.load_text_snapshots(path)
    assert rows[-2]["event_type"] == "discord.snapshot_store.invalid_observation_revoked"
    assert rows[-1]["previous_event_hash"] == rows[-2]["event_hash"]
    assert rows[-1]["previous_content_hash"] == rows[1]["content_hash"]


def test_revocation_rejects_wrong_logical_thread_without_append(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path, bad_url="https://discord.com/channels/12345678901234567/45678901234567890")
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.revoke_invalid_snapshot_observation(path)
    assert path.read_bytes() == original


def test_revocation_rejects_malformed_row_on_same_url(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path, bad_url=IAB_URL)
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.revoke_invalid_snapshot_observation(path, dry_run=True)
    assert path.read_bytes() == original


def test_revocation_rejects_different_url_with_canonical_stream_id(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path, bad_stream_id=core.target_key_for_url(IAB_URL))
    original = path.read_bytes()
    with pytest.raises(CheckpointCorruptError):
        core.revoke_invalid_snapshot_observation(path, dry_run=True)
    assert path.read_bytes() == original


def test_revocation_rejects_invalid_timestamp_even_with_matching_event_hash(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path)
    core.revoke_invalid_snapshot_observation(path)
    rows = core.load_text_snapshots(path)
    proof = rows[-1]
    for field in ("time", "captured_at", "observed_at", "ingested_at"):
        proof[field] = "not-a-timestamp"
    proof["event_hash"] = core.canonical_event_hash(proof)
    with pytest.raises(CheckpointCorruptError):
        core._validate_text_snapshot_chain(rows)


def test_revocation_rejects_physical_row_mutation_after_receipt(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path)
    core.revoke_invalid_snapshot_observation(path)
    lines = path.read_bytes().split(b"\n")
    lines[2] += b" "
    path.write_bytes(b"\n".join(lines))
    with pytest.raises(CheckpointCorruptError, match="bytes changed"):
        core.snapshot_visible_text(text="new", url=IAB_URL, path=path)


def test_revocation_rejects_unverified_standalone_event(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path)
    core.revoke_invalid_snapshot_observation(path)
    rows = core.load_text_snapshots(path)
    with pytest.raises(CheckpointCorruptError):
        core._validate_text_snapshot_chain(rows[:2] + rows[3:])


def test_recovery_cli_is_read_only_by_default_and_metadata_only(tmp_path):
    path = tmp_path / "text-snapshots.ndjson"
    _bad_store(path)
    before = path.read_bytes()
    script = Path(__file__).resolve().parents[1] / "scripts" / "revoke_invalid_snapshot_observation.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--snapshot-store", str(path)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "outbound_actions": "disabled", "ready_to_append": True, "saved": False,
    }
    assert path.read_bytes() == before
    assert str(path) not in completed.stdout
