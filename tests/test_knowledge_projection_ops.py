import json
import subprocess
import sys
from pathlib import Path

from discord_context_bridge.knowledge_projection_ops import (
    projection_lock,
    run_projection,
    verify_projection_receipt,
)


def _snapshot(path):
    path.write_text(
        json.dumps({"target_key": "abc", "captured_at": "2026-08-06T00:00:00+09:00", "source": "test", "text": "Alice\nToday at 10:00\nhello"}) + "\n",
        encoding="utf-8",
    )


def test_run_writes_sanitized_receipt_and_is_idempotent(tmp_path):
    source = tmp_path / "snapshots.ndjson"
    _snapshot(source)
    output = tmp_path / "wiki"
    receipt = tmp_path / "state" / "latest.json"
    lock = tmp_path / "state" / "run.lock"
    first = run_projection(snapshot_store=source, output_root=output, receipt_path=receipt, lock_path=lock)
    second = run_projection(snapshot_store=source, output_root=output, receipt_path=receipt, lock_path=lock)
    assert first["ok"] is True
    assert second["ok"] is True
    assert second["written_file_count"] == 0
    assert verify_projection_receipt(receipt)["ok"] is True
    raw = receipt.read_text(encoding="utf-8")
    assert str(tmp_path) not in raw
    assert "hello" not in raw


def test_dry_run_writes_nothing(tmp_path):
    source = tmp_path / "snapshots.ndjson"
    _snapshot(source)
    receipt = tmp_path / "latest.json"
    result = run_projection(snapshot_store=source, output_root=tmp_path / "wiki", receipt_path=receipt, lock_path=tmp_path / "run.lock", dry_run=True)
    assert result["ok"] is True
    assert not receipt.exists()
    assert not (tmp_path / "wiki").exists()


def test_concurrent_run_stops_safely(tmp_path):
    source = tmp_path / "snapshots.ndjson"
    _snapshot(source)
    lock = tmp_path / "run.lock"
    with projection_lock(lock):
        result = run_projection(snapshot_store=source, output_root=tmp_path / "wiki", receipt_path=tmp_path / "latest.json", lock_path=lock)
    assert result["ok"] is False
    assert result["reason"] == "projection_already_running"


def test_failure_is_sanitized(tmp_path):
    result = run_projection(snapshot_store=tmp_path / "missing", output_root=tmp_path / "wiki", receipt_path=tmp_path / "latest.json", lock_path=tmp_path / "run.lock")
    assert result["ok"] is False
    assert result["reason"] == "snapshot_store_missing"


def test_raised_failure_replaces_previous_success_receipt(tmp_path, monkeypatch):
    source = tmp_path / "snapshots.ndjson"
    _snapshot(source)
    receipt = tmp_path / "latest.json"
    lock = tmp_path / "run.lock"
    successful = run_projection(
        snapshot_store=source,
        output_root=tmp_path / "wiki",
        receipt_path=receipt,
        lock_path=lock,
    )
    assert successful["ok"] is True

    def fail_projection(**_kwargs):
        raise ValueError("private input must not be reflected")

    monkeypatch.setattr(
        "discord_context_bridge.knowledge_projection_ops.export_knowledge_projection",
        fail_projection,
    )
    failed = run_projection(
        snapshot_store=source,
        output_root=tmp_path / "wiki",
        receipt_path=receipt,
        lock_path=lock,
    )
    assert failed["reason"] == "projection_failed"
    assert verify_projection_receipt(receipt) == {
        "ok": False,
        "reason": "receipt_not_successful",
    }
    assert "private input" not in receipt.read_text(encoding="utf-8")


def test_runner_works_directly_from_source_checkout(tmp_path):
    source = tmp_path / "snapshots.ndjson"
    _snapshot(source)
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "run_knowledge_wiki_projection.py"),
            "--snapshot-store",
            str(source),
            "--output-root",
            str(tmp_path / "wiki"),
            "--receipt",
            str(tmp_path / "latest.json"),
            "--lock",
            str(tmp_path / "run.lock"),
            "--dry-run",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["ok"] is True
    assert str(tmp_path) not in completed.stdout
