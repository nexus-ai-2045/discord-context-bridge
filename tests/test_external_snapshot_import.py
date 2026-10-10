from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from discord_context_bridge.core import (
    _validate_text_snapshot_chain,
    canonical_event_hash,
    load_text_snapshots,
    snapshot_visible_text,
    stable_text_hash,
)
from discord_context_bridge.external_snapshot_import import import_external_snapshot

URL = "https://discord.com/channels/111111111111111111/222222222222222222"


@pytest.fixture
def sample(tmp_path: Path):
    root = tmp_path / "raw"
    store = root / "discord/servers/111111111111111111/channels/222222222222222222/text-snapshots.ndjson"
    snapshot_visible_text(text="existing", url=URL, path=store)
    source = root / "external.ndjson"
    record = {
        "url": URL, "target_key": stable_text_hash(URL), "text": "historical body",
        "content_hash": stable_text_hash("historical body"),
        "captured_at": "2026-07-01T01:02:03+00:00", "source": "saved_visible_text",
        "private_local_only": True, "external_share_allowed": False,
        "outbound_actions": "disabled",
    }
    source.write_text(json.dumps(record) + "\n")
    return root, store, source, record


def invoke(sample, **kwargs):
    root, _, source, _ = sample
    return import_external_snapshot(
        snapshot_root=root, source_file=source, target_url=URL,
        expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), **kwargs,
    )


def test_dry_run_has_no_writes(sample):
    root, _, _, _ = sample
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = invoke(sample)
    assert result["status"] == "ready"
    assert result["events_appended"] == 0
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_apply_preserves_history_and_is_idempotent(sample):
    _, store, source, original = sample
    before = store.read_bytes()
    result = invoke(sample, apply=True)
    assert result["status"] == "absorbed"
    assert result["read_back"] is True
    assert store.read_bytes().startswith(before)
    rows = load_text_snapshots(store)
    _validate_text_snapshot_chain(rows)
    event = rows[-1]
    assert event["text"] == original["text"]
    assert event["captured_at"] == original["captured_at"]
    assert event["ingested_at"] != original["captured_at"]
    assert event["event_type"] != "message_observation"
    assert hashlib.sha256(source.read_bytes()).hexdigest() in json.dumps(event)
    after = store.read_bytes()
    assert invoke(sample, apply=True)["status"] == "already_present"
    assert store.read_bytes() == after
    assert not list(store.parent.glob(".external-import-stage-*"))


def test_stale_store_cas_blocks(sample):
    _, store, _, _ = sample
    before = store.read_bytes()
    result = invoke(sample, apply=True, expected_store_sha256="0" * 64)
    assert result["status"] == "blocked"
    assert store.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("content_hash", "wrong"), ("captured_at", "missing-timezone"),
    ("private_local_only", False), ("outbound_actions", "enabled"),
    ("url", "https://discord.com/channels/111111111111111111/333333333333333333"),
])
def test_invalid_source_is_blocked_without_private_output(sample, field, value):
    _, store, source, original = sample
    before = store.read_bytes()
    original[field] = value
    source.write_text(json.dumps(original) + "\n")
    result = invoke(sample, apply=True)
    assert result["status"] == "blocked"
    output = json.dumps(result)
    assert URL not in output and original["text"] not in output
    assert str(source) not in output and "111111111111111111" not in output
    assert store.read_bytes() == before


def test_same_content_different_observation_keeps_provenance(sample):
    _, store, source, original = sample
    assert invoke(sample, apply=True)["status"] == "absorbed"
    original["captured_at"] = "2026-07-02T01:02:03+00:00"
    source.write_text(json.dumps(original) + "\n")
    result = invoke(sample, apply=True)
    assert result["status"] == "absorbed"
    assert result["duplicate_content"] is True
    _validate_text_snapshot_chain(load_text_snapshots(store))


def test_unicode_line_separator_is_body_not_ndjson_separator(sample):
    _, _, source, original = sample
    original["text"] = "before\u2028after"
    original["content_hash"] = stable_text_hash(original["text"])
    source.write_text(json.dumps(original, ensure_ascii=False) + "\n")
    assert invoke(sample)["status"] == "ready"


def test_symlink_source_is_blocked(sample):
    root, store, source, _ = sample
    target = root / "original.ndjson"
    source.rename(target)
    source.symlink_to(target)
    before = store.read_bytes()
    assert invoke(sample, apply=True)["status"] == "blocked"
    assert store.read_bytes() == before


def test_broken_chain_is_blocked(sample):
    _, store, _, _ = sample
    row = json.loads(store.read_text())
    row["event_hash"] = "wrong"
    store.write_text(json.dumps(row) + "\n")
    before = store.read_bytes()
    assert invoke(sample, apply=True)["status"] == "blocked"
    assert store.read_bytes() == before


def test_swap_failure_keeps_original(sample, monkeypatch):
    _, store, _, _ = sample
    before = store.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("private-path-must-not-leak")

    monkeypatch.setattr("discord_context_bridge.external_snapshot_import.os.replace", fail)
    result = invoke(sample, apply=True)
    assert result["status"] == "blocked"
    assert "private-path-must-not-leak" not in json.dumps(result)
    assert store.read_bytes() == before
    assert not list(store.parent.glob(".external-import-stage-*"))


def test_cli_dry_run_is_metadata_only(sample):
    root, _, source, original = sample
    completed = subprocess.run([
        sys.executable, "scripts/import_external_snapshot.py", "--snapshot-root", str(root),
        "--source-file", str(source), "--target-url", URL, "--expected-source-sha256",
        hashlib.sha256(source.read_bytes()).hexdigest(), "--json",
    ], capture_output=True, text=True, check=False)
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] == "ready"
    for private in (URL, str(source), original["text"], "111111111111111111"):
        assert private not in completed.stdout + completed.stderr


def test_stage_cleanup_does_not_follow_symlink(sample, tmp_path, monkeypatch):
    _, store, _, _ = sample
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "private.txt"
    protected.write_text("must remain")

    def fail(source, destination, **kwargs):
        os.symlink(outside, "outside-link", dir_fd=kwargs["src_dir_fd"])
        raise OSError("swap failed")

    monkeypatch.setattr("discord_context_bridge.external_snapshot_import.os.replace", fail)
    assert invoke(sample, apply=True)["status"] == "blocked"
    assert protected.read_text() == "must remain"
    assert not list(store.parent.glob(".external-import-stage-*"))


def test_stage_cleanup_failure_reports_safe_reason_and_applied_state(sample, monkeypatch):
    _, store, _, _ = sample
    original_rmdir = os.rmdir

    def fail_stage_remove(path, **kwargs):
        if str(path).startswith(".external-import-stage-"):
            raise OSError("private-cleanup-path")
        return original_rmdir(path, **kwargs)

    monkeypatch.setattr("discord_context_bridge.external_snapshot_import.os.rmdir", fail_stage_remove)
    result = invoke(sample, apply=True)
    assert result["status"] == "blocked"
    assert result["reason"] == "stage_cleanup_failed"
    assert result["events_appended"] == 1
    assert result["commit_state"] == "applied_unverified"
    assert "private-cleanup-path" not in json.dumps(result)
    assert len(load_text_snapshots(store)) == 2


def test_unopened_stage_is_preserved_without_private_data(sample, monkeypatch):
    _, store, _, _ = sample
    before = store.read_bytes()
    original_open = os.open

    def fail_stage_open(path, *args, **kwargs):
        if str(path).startswith(".external-import-stage-"):
            raise OSError("stage cannot be pinned")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("discord_context_bridge.external_snapshot_import.os.open", fail_stage_open)
    result = invoke(sample, apply=True)
    assert result["status"] == "blocked"
    assert result["reason"] == "stage_cleanup_failed"
    assert result["events_appended"] == 0
    assert store.read_bytes() == before
    stages = list(store.parent.glob(".external-import-stage-*"))
    assert len(stages) == 1
    assert not list(stages[0].iterdir())


def test_post_swap_failure_reports_applied_not_zero(sample, monkeypatch):
    _, store, _, _ = sample
    original_fsync = os.fsync

    def fail_after_swap(fd):
        if len(load_text_snapshots(store)) == 2:
            raise OSError("post-swap-fsync-failure")
        return original_fsync(fd)

    monkeypatch.setattr("discord_context_bridge.external_snapshot_import.os.fsync", fail_after_swap)
    result = invoke(sample, apply=True)
    assert result["status"] == "blocked"
    assert result["events_appended"] == 1
    assert result["commit_state"] == "applied_unverified"
    _validate_text_snapshot_chain(load_text_snapshots(store))
    assert invoke(sample)["status"] == "already_present"
    assert not list(store.parent.glob(".external-import-stage-*"))


def test_swap_uses_pinned_source_and_destination_directories(sample, monkeypatch):
    original_replace = os.replace
    seen = []

    def check(source, destination, **kwargs):
        assert kwargs.get("src_dir_fd") is not None
        assert kwargs.get("dst_dir_fd") is not None
        seen.append(True)
        return original_replace(source, destination, **kwargs)

    monkeypatch.setattr("discord_context_bridge.external_snapshot_import.os.replace", check)
    assert invoke(sample, apply=True)["status"] == "absorbed"
    assert seen


def test_existing_same_thread_message_url_uses_common_identity(sample):
    _, store, _, _ = sample
    snapshot_visible_text(text="message observation", url=URL + "/333333333333333333", path=store)
    assert invoke(sample)["status"] == "ready"


def test_existing_other_thread_record_blocks_import(sample):
    _, store, _, _ = sample
    snapshot_visible_text(text="other target", url=URL.replace("222222222222222222", "444444444444444444"), path=store)
    assert invoke(sample)["status"] == "blocked"


def test_existing_body_corruption_is_not_hidden_by_valid_event_hash(sample):
    _, store, _, _ = sample
    row = json.loads(store.read_text())
    row["text"] = "tampered without content hash update"
    row["event_hash"] = canonical_event_hash(row)
    store.write_text(json.dumps(row) + "\n")
    assert invoke(sample)["status"] == "blocked"
