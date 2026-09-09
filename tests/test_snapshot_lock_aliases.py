import os

import pytest

from discord_context_bridge import core
from discord_context_bridge.capture import store as store_module
from discord_context_bridge.capture.store import (
    CheckpointCorruptError,
    SequenceConflictError,
)


@pytest.mark.parametrize("first, second", [
    ("current.ndjson", "CURRENT.NDJSON"),
    ("café.ndjson", "cafe\u0301.ndjson"),
])
def test_lock_identity_is_stable_for_aliases_before_and_after_creation(tmp_path, first, second):
    original, alias = tmp_path / first, tmp_path / second
    before = core._text_snapshot_lock_id(original)
    assert before == core._text_snapshot_lock_id(alias)
    original.touch()
    assert before == core._text_snapshot_lock_id(original)
    assert before == core._text_snapshot_lock_id(alias)


@pytest.mark.parametrize("seeded", [False, True])
def test_case_insensitive_root_and_ledger_alias_share_writer_lock(tmp_path, seeded):
    root = tmp_path / "capture-root"
    root.mkdir()
    root_alias = tmp_path / "CAPTURE-ROOT"
    if not root_alias.exists() or not root.samefile(root_alias):
        pytest.skip("case-sensitive filesystem: root spellings are distinct stores")
    original = root / "current.ndjson"
    alias = root_alias / "CURRENT.NDJSON"
    if seeded:
        core.snapshot_visible_text(text="seed", url="https://example.invalid/a", path=original)
    before = original.read_bytes() if seeded else None
    with (
        core.CaptureCheckpointStore(root).transition_lock(core._text_snapshot_lock_id(original)),
        pytest.raises(SequenceConflictError),
    ):
        core.snapshot_visible_text(text="contender", url="https://example.invalid/a", path=alias)
    assert (original.read_bytes() if seeded else None) == before
    if not seeded:
        assert not original.exists()


def test_alias_fix_does_not_follow_symlink_store_root(tmp_path):
    root = tmp_path / "real-root"
    root.mkdir()
    alias = tmp_path / "linked-root"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(CheckpointCorruptError):
        core.snapshot_visible_text(text="blocked", url="https://example.invalid/a", path=alias / "current.ndjson")
    assert not (root / "current.ndjson").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_hardlinked_ledger_rejects_both_aliases_without_write(tmp_path, monkeypatch, legacy):
    if legacy:
        monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: False)
    original = tmp_path / "current.ndjson"
    core.snapshot_visible_text(text="seed", url="https://example.invalid/a", path=original)
    alias = tmp_path / "other.ndjson"
    os.link(original, alias)
    before = original.read_bytes()
    for path in (original, alias):
        with pytest.raises(CheckpointCorruptError):
            core.snapshot_visible_text(text="blocked", url="https://example.invalid/a", path=path)
        with pytest.raises(CheckpointCorruptError):
            store_module._append_store_relative_chunks(tmp_path, path, (b"blocked\n",))
        with pytest.raises(CheckpointCorruptError):
            core._append_text_snapshots_transaction(lambda _: [], path)
        assert original.read_bytes() == alias.read_bytes() == before


@pytest.mark.parametrize("legacy", [False, True])
def test_hardlink_created_before_chunk_is_rejected_before_write(tmp_path, monkeypatch, legacy):
    if legacy:
        monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: False)
    ledger = tmp_path / "ledger.ndjson"
    ledger.write_bytes(b"seed\n")
    def chunks():
        os.link(ledger, tmp_path / "alias.ndjson")
        yield b"blocked\n"
    with pytest.raises(CheckpointCorruptError):
        store_module._append_store_relative_chunks(tmp_path, ledger, chunks())
    assert ledger.read_bytes() == b"seed\n"


@pytest.mark.parametrize("legacy", [False, True])
def test_hardlink_created_during_write_requires_recovery_without_truncation(tmp_path, monkeypatch, legacy):
    if legacy:
        monkeypatch.setattr(store_module, "_secure_store_ops_supported", lambda: False)
    ledger = tmp_path / "ledger.ndjson"
    ledger.write_bytes(b"seed\n")
    write = store_module._write_all
    def link_after_write(descriptor, content):
        write(descriptor, content)
        os.link(ledger, tmp_path / "alias.ndjson")
    monkeypatch.setattr(store_module, "_write_all", link_after_write)
    def forbid_truncate(*args):
        pytest.fail("must not truncate a newly shared inode")
    monkeypatch.setattr(store_module.os, "ftruncate", forbid_truncate)
    with pytest.raises(CheckpointCorruptError, match="exclusive"):
        store_module._append_store_relative_chunks(tmp_path, ledger, (b"pending\n",))
    assert ledger.read_bytes() == b"seed\npending\n"
