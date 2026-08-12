import errno
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from discord_context_bridge.knowledge_projection_ops import (
    ProjectionLockError,
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


def test_lock_setup_failure_replaces_previous_success_receipt(tmp_path, monkeypatch):
    source = tmp_path / "snapshots.ndjson"
    _snapshot(source)
    receipt = tmp_path / "latest.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "dcb.knowledge_projection_run.v1",
                "ok": True,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    def fail_lock(_lock_path):
        raise ProjectionLockError("projection_lock_unavailable")

    monkeypatch.setattr(
        "discord_context_bridge.knowledge_projection_ops.projection_lock",
        fail_lock,
    )
    failed = run_projection(
        snapshot_store=source,
        output_root=tmp_path / "wiki",
        receipt_path=receipt,
        lock_path=tmp_path / "run.lock",
    )
    assert failed["reason"] == "projection_failed"
    assert verify_projection_receipt(receipt)["reason"] == "receipt_not_successful"


def test_lock_open_failure_is_not_reported_as_contention(tmp_path, monkeypatch):
    lock = tmp_path / "run.lock"
    original_open = Path.open

    def fail_lock_open(path, *args, **kwargs):
        if path == lock:
            raise PermissionError("lock path is unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_lock_open)
    with pytest.raises(ProjectionLockError, match="projection_lock_unavailable"):
        with projection_lock(lock):
            pass


def test_lock_acquisition_non_contention_error_is_operational_failure(tmp_path, monkeypatch):
    def fail_lock(*_args, **_kwargs):
        raise OSError(errno.EINVAL, "locking is unsupported")

    if os.name == "nt":
        import msvcrt

        monkeypatch.setattr(msvcrt, "locking", fail_lock)
    else:
        import fcntl

        monkeypatch.setattr(fcntl, "flock", fail_lock)

    with pytest.raises(ProjectionLockError, match="projection_lock_unavailable"):
        with projection_lock(tmp_path / "run.lock"):
            pass


@pytest.mark.parametrize("recorded_at", [None, "not-a-timestamp", "2026-08-06T00:00:00"])
def test_verify_rejects_invalid_timestamp(tmp_path, recorded_at):
    receipt = tmp_path / "latest.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "dcb.knowledge_projection_run.v1",
                "ok": True,
                "recorded_at": recorded_at,
            }
        ),
        encoding="utf-8",
    )
    assert verify_projection_receipt(receipt)["reason"] == "receipt_timestamp_invalid"


def test_verify_rejects_stale_and_future_receipts(tmp_path):
    receipt = tmp_path / "latest.json"
    now = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)
    for recorded_at, reason in (
        (now - timedelta(hours=37), "receipt_stale"),
        (now + timedelta(seconds=1), "receipt_timestamp_invalid"),
    ):
        receipt.write_text(
            json.dumps(
                {
                    "schema": "dcb.knowledge_projection_run.v1",
                    "ok": True,
                    "recorded_at": recorded_at.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        assert verify_projection_receipt(receipt, now=now)["reason"] == reason


def test_verify_rejects_non_positive_max_age(tmp_path):
    with pytest.raises(ValueError, match="max_age must be greater than zero"):
        verify_projection_receipt(tmp_path / "latest.json", max_age=timedelta(0))


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_runner_rejects_invalid_max_receipt_age(tmp_path, value):
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "run_knowledge_wiki_projection.py"),
            "--snapshot-store",
            str(tmp_path / "snapshots.ndjson"),
            "--output-root",
            str(tmp_path / "wiki"),
            "--receipt",
            str(tmp_path / "latest.json"),
            "--lock",
            str(tmp_path / "run.lock"),
            "--verify",
            f"--max-receipt-age-hours={value}",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "finite number greater than zero" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_runner_rejects_overflowing_max_receipt_age(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "run_knowledge_wiki_projection.py"),
            "--snapshot-store",
            str(tmp_path / "snapshots.ndjson"),
            "--output-root",
            str(tmp_path / "wiki"),
            "--receipt",
            str(tmp_path / "latest.json"),
            "--lock",
            str(tmp_path / "run.lock"),
            "--verify",
            "--max-receipt-age-hours=1e308",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "too large" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_task_verifier_checks_operational_settings():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "setup_knowledge_wiki_projection_task.ps1"
    ).read_text(encoding="utf-8")
    for expected in (
        "working_directory",
        "existing.State",
        "enabledTriggers.Count -eq 1",
        "DaysInterval",
        "StartBoundary",
        "execution_time_limit",
        "match_details",
    ):
        assert expected in script


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
