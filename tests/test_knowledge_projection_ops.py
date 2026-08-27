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


def _init_git_repo(path):
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "--all"], check=True)
    subprocess.run(
        [
            "git", "-C", str(path),
            "-c", "user.name=DCB Test",
            "-c", "user.email=dcb-test@example.invalid",
            "commit", "--quiet", "-m", "test fixture",
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


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
        "ready_to_apply",
        "snapshot_store_present",
        "person_registry_present",
        "topic_registry_present",
        "stable_checkout",
        "git_present",
        "expected_commit_format",
        "expected_commit_present",
        "expected_commit_in_head_history",
        "direct_exit_propagation",
        "data_paths_outside_repo",
    ):
        assert expected in script


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler dry-run")
def test_task_setup_dry_run_reports_preflight_detectors(tmp_path):
    repo_root = tmp_path / "stable-repo"
    private_root = tmp_path / "private-data"
    (repo_root / "scripts").mkdir(parents=True)
    (repo_root / "scripts" / "run_knowledge_wiki_projection.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    expected_commit = _init_git_repo(repo_root)
    private_root.mkdir()
    snapshot = private_root / "text-snapshots.ndjson"
    _snapshot(snapshot)
    script = Path(__file__).resolve().parents[1] / "scripts" / "setup_knowledge_wiki_projection_task.ps1"
    completed = subprocess.run(
        [
            "pwsh", "-NoProfile", "-File", str(script),
            "-PythonPath", sys.executable,
            "-RepoRoot", str(repo_root),
            "-SnapshotStore", str(snapshot),
            "-OutputRoot", str(private_root / "wiki"),
            "-ReceiptPath", str(private_root / "ops" / "latest.json"),
            "-LockPath", str(private_root / "ops" / "run.lock"),
            "-ExpectedCommit", expected_commit,
            "-TaskName", "DCB-Knowledge-Wiki-Projection-Test-DryRun",
            "-Json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["action"] == "dry_run"
    assert payload["changed"] is False
    assert payload["ready_to_apply"] is True
    assert all(payload["detectors"].values())


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler dry-run")
def test_task_setup_resolves_relative_data_paths_against_repo_root_and_checks_registries(tmp_path):
    repo_root = tmp_path / "stable-repo"
    private_root = tmp_path / "private-data"
    invocation_root = tmp_path / "unrelated-cwd"
    (repo_root / "scripts").mkdir(parents=True)
    (repo_root / "scripts" / "run_knowledge_wiki_projection.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    expected_commit = _init_git_repo(repo_root)
    private_root.mkdir()
    invocation_root.mkdir()
    _snapshot(private_root / "text-snapshots.ndjson")
    (private_root / "people.json").write_text("{}\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "setup_knowledge_wiki_projection_task.ps1"
    command = [
        "pwsh", "-NoProfile", "-File", str(script),
        "-PythonPath", sys.executable,
        "-RepoRoot", str(repo_root),
        "-SnapshotStore", r"..\private-data\text-snapshots.ndjson",
        "-OutputRoot", r"..\private-data\wiki",
        "-ReceiptPath", r"..\private-data\ops\latest.json",
        "-LockPath", r"..\private-data\ops\run.lock",
        "-ExpectedCommit", expected_commit,
        "-PersonRegistry", r"..\private-data\people.json",
        "-TopicRegistry", r"..\private-data\missing-topics.json",
        "-TaskName", "DCB-Knowledge-Wiki-Projection-Test-Relative",
        "-Json",
    ]

    blocked = subprocess.run(command, cwd=invocation_root, capture_output=True, text=True, check=False)
    assert blocked.returncode == 0
    blocked_payload = json.loads(blocked.stdout)
    assert blocked_payload["ready_to_apply"] is False
    assert blocked_payload["detectors"]["person_registry_present"] is True
    assert blocked_payload["detectors"]["topic_registry_present"] is False
    assert blocked_payload["detectors"]["snapshot_store_present"] is True
    assert blocked_payload["detectors"]["data_paths_outside_repo"] is True

    apply_blocked = subprocess.run(
        [*command, "-Apply"], cwd=invocation_root, capture_output=True, text=True, check=False
    )
    assert apply_blocked.returncode == 2
    apply_blocked_payload = json.loads(apply_blocked.stdout)
    assert apply_blocked_payload["action"] == "apply_blocked"
    assert apply_blocked_payload["changed"] is False

    (private_root / "missing-topics.json").write_text("{}\n", encoding="utf-8")
    ready = subprocess.run(command, cwd=invocation_root, capture_output=True, text=True, check=False)
    assert ready.returncode == 0
    ready_payload = json.loads(ready.stdout)
    assert ready_payload["ready_to_apply"] is True
    assert all(ready_payload["detectors"].values())


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler dry-run")
def test_task_setup_blocks_when_expected_commit_is_not_in_checkout(tmp_path):
    repo_root = tmp_path / "stable-repo"
    private_root = tmp_path / "private-data"
    (repo_root / "scripts").mkdir(parents=True)
    (repo_root / "scripts" / "run_knowledge_wiki_projection.py").write_text(
        "raise SystemExit(0)\n", encoding="utf-8"
    )
    _init_git_repo(repo_root)
    private_root.mkdir()
    snapshot = private_root / "text-snapshots.ndjson"
    _snapshot(snapshot)
    script = Path(__file__).resolve().parents[1] / "scripts" / "setup_knowledge_wiki_projection_task.ps1"
    command = [
        "pwsh", "-NoProfile", "-File", str(script),
        "-PythonPath", sys.executable,
        "-RepoRoot", str(repo_root),
        "-SnapshotStore", str(snapshot),
        "-OutputRoot", str(private_root / "wiki"),
        "-ReceiptPath", str(private_root / "ops" / "latest.json"),
        "-LockPath", str(private_root / "ops" / "run.lock"),
        "-ExpectedCommit", "0" * 40,
        "-TaskName", "DCB-Knowledge-Wiki-Projection-Test-Commit",
        "-Json",
    ]
    dry_run = subprocess.run(command, capture_output=True, text=True, check=False)
    assert dry_run.returncode == 0
    payload = json.loads(dry_run.stdout)
    assert payload["ready_to_apply"] is False
    assert payload["detectors"]["expected_commit_format"] is True
    assert payload["detectors"]["expected_commit_present"] is False
    assert payload["detectors"]["expected_commit_in_head_history"] is False

    apply = subprocess.run([*command, "-Apply"], capture_output=True, text=True, check=False)
    assert apply.returncode == 2
    assert json.loads(apply.stdout)["action"] == "apply_blocked"


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
