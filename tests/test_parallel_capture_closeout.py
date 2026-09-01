import hashlib
import json
from pathlib import Path

from discord_context_bridge.capture import parallel_closeout
from discord_context_bridge.capture.parallel_closeout import (
    evaluate_legacy_parallel_run,
    evaluate_legacy_parallel_run_from_store,
    parent_target_key_digest,
    persist_legacy_parallel_closeout,
    persist_parallel_producer_drain_receipt,
    persist_parallel_run_stop_receipt,
)
from discord_context_bridge.cli import main
from discord_context_bridge.completeness_store import CompletenessStore

PARENT_TARGET = "fixture-parent"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_fixture(
    tmp_path: Path,
    *,
    item_count: int = 2,
    status: str = "running",
    stopped_reason: str | None = None,
) -> Path:
    run = tmp_path / "run"
    _write_json(
        run / "run-metadata.json",
        {
            "schema": "dcb.parallel-run.v1",
            "status": status,
            "canonical_count": item_count,
            "shard_counts": [item_count],
            "parent_target_key_sha256": parent_target_key_digest(PARENT_TARGET),
            "outbound_actions": "disabled",
            **({"stopped_reason": stopped_reason} if stopped_reason else {}),
        },
    )
    _write_json(
        run / "shards" / "worker-0.json",
        [
            {"private_url": "must-not-leak", "private_id": str(i)}
            for i in range(item_count)
        ],
    )
    return run


def _record_item(run: Path, index: int, *, supplemental: bool = False) -> None:
    ready_path = run / "spool" / "worker-0" / f"{index:04d}.ready.json"
    _write_json(
        ready_path,
        {
            "schema": "dcb.browser-spool.v1",
            "status": "ready",
            "worker": 0,
            "index": index,
            "url": "must-not-leak",
        },
    )
    text_path = run / "spool" / "worker-0" / f"{index:04d}.txt"
    text_path.write_text(f"fixture text {index}", encoding="utf-8")
    ready_digest = hashlib.sha256(ready_path.read_bytes()).hexdigest()
    text_digest = hashlib.sha256(text_path.read_bytes()).hexdigest()
    _write_json(
        run / "committed" / f"worker-0-item-{index:04d}" / "receipt.json",
        {
            "schema": "dcb-parallel-import-receipt.v1",
            "worker": 0,
            "index": index,
            "commit_state": "committed",
            "source_status": "ready",
            "ready_sha256": ready_digest,
            "text_sha256": text_digest,
            "outbound_actions": "disabled",
            "private_value": "must-not-leak",
        },
    )
    ledger = run / "committed" / "commit-ledger.ndjson"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "schema": "dcb-parallel-commit-ledger.v1",
            "worker": 0,
            "index": index,
            "commit_state": "committed",
            "ready_sha256": ready_digest,
            "text_sha256": text_digest,
            "outbound_actions": "disabled",
        }
    ]
    if supplemental:
        rows.append({**rows[0], "schema": "dcb-parallel-commit-ledger-supplement.v1"})
    with ledger.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _full_parent_audit() -> dict[str, object]:
    return {
        "schema": "discord_parent_completeness_certificate.v1",
        "status": "full",
        "parent_full_capture_confirmed": True,
        "raw_text_returned": False,
        "identifiers_returned": False,
        "outbound_actions": "disabled",
    }


def _full_certificate(capture_id: str) -> dict[str, object]:
    return {
        "schema": "discord_full_capture_completion_gate.v1",
        "capture_id": capture_id,
        "status": "full",
        "full_capture_confirmed": True,
        "counts": {
            "messages": 1,
            "attachments_discovered": 0,
            "attachments_saved": 0,
            "attachments_manifested": 0,
        },
        "attachments_consistent": True,
        "unresolved_gap_count": 0,
        "blockers": [],
    }


def _full_completeness_db(tmp_path: Path, target: str = PARENT_TARGET) -> Path:
    database = tmp_path / "capture.sqlite3"
    store = CompletenessStore(database)
    store.initialize()
    scopes = {"active": True, "archived_public": True, "archived_private": True}
    for index in (1, 2):
        store.record_inventory_scan(
            parent_target_key=target,
            scan_id=f"scan-{index}",
            observed_at=f"2026-09-01T00:0{index}:00+00:00",
            thread_ids=["thread-1"],
            scopes=scopes,
            pagination_exhausted=True,
        )
    store.record_child_certificate(target, "thread-1", _full_certificate("capture-1"))
    return database


def _record_stop_receipt(run: Path) -> None:
    for producer in ("worker-0", "importer"):
        persist_parallel_producer_drain_receipt(
            run,
            producer=producer,
            event_id=f"fixture-{producer}-drained",
        )
    persist_parallel_run_stop_receipt(
        run,
        event_id="fixture-producer-quiesced",
        stopped_reason="producer_failed",
    )


def test_arbitrary_done_markers_never_count_as_completion(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path)
    (run / "workers.done").write_text("done", encoding="utf-8")
    (run / "importer.done").write_text("done", encoding="utf-8")

    result = evaluate_legacy_parallel_run(
        run,
        parent_audit=_full_parent_audit(),
        parent_target_key=PARENT_TARGET,
        finalize=False,
    )

    assert result["terminal_state"] == "running"
    assert result["full_capture_confirmed"] is False
    assert result["counts"]["missing_receipts"] == 2
    assert "canonical_receipts_missing" in result["blockers"]
    assert "workers.done" not in json.dumps(result)
    assert "importer.done" not in json.dumps(result)


def test_supplement_rows_do_not_inflate_unique_ledger_count(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0, supplemental=True)
    _record_stop_receipt(run)

    database = _full_completeness_db(tmp_path)
    result = evaluate_legacy_parallel_run_from_store(
        run,
        completeness_db=database,
        parent_target_key=PARENT_TARGET,
        finalize=False,
    )

    assert result["counts"]["commit_ledger_rows"] == 2
    assert result["counts"]["canonical_ledger_bindings"] == 1
    assert result["terminal_state"] == "full_closed"
    assert result["full_capture_confirmed"] is True


def test_finalize_incomplete_run_is_blocked_closed_never_full(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, status="failed", stopped_reason="producer_failed")
    _record_item(run, 0)
    _record_stop_receipt(run)

    result = evaluate_legacy_parallel_run(run, finalize=True)

    assert result["terminal_state"] == "blocked_closed"
    assert result["status"] == "blocked"
    assert result["full_capture_confirmed"] is False
    assert result["counts"]["missing_receipts"] == 1
    assert "parent_completeness_certificate_missing" in result["blockers"]


def test_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    receipt = run / "committed" / "worker-0-item-0000" / "receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["text_sha256"] = "different"
    _write_json(receipt, payload)

    result = evaluate_legacy_parallel_run(
        run,
        parent_audit=_full_parent_audit(),
        parent_target_key=PARENT_TARGET,
        finalize=True,
    )

    assert result["terminal_state"] == "running"
    assert "commit_ledger_binding_mismatch" in result["blockers"]


def test_recorded_without_text_is_terminal_but_cannot_bypass_parent_gate(
    tmp_path: Path,
) -> None:
    run = _run_fixture(
        tmp_path,
        item_count=1,
        status="failed",
        stopped_reason="producer_failed",
    )
    ready_path = run / "spool" / "worker-0" / "0000.ready.json"
    _write_json(
        ready_path,
        {
            "schema": "dcb.browser-spool.v1",
            "status": "no_message_list",
            "worker": "0",
            "index": "0",
        },
    )
    ready_digest = hashlib.sha256(ready_path.read_bytes()).hexdigest()
    _write_json(
        run / "committed" / "worker-0-item-0000" / "receipt.json",
        {
            "schema": "dcb-parallel-import-receipt.v1",
            "worker": "0",
            "index": "0",
            "commit_state": "recorded_without_text",
            "source_status": "no_message_list",
            "ready_sha256": ready_digest,
            "text_sha256": None,
            "outbound_actions": "disabled",
        },
    )
    ledger = run / "committed" / "commit-ledger.ndjson"
    ledger.write_text(
        json.dumps(
            {
                "schema": "dcb-parallel-commit-ledger.v1",
                "worker": "0",
                "index": "0",
                "commit_state": "recorded_without_text",
                "ready_sha256": ready_digest,
                "text_sha256": None,
                "outbound_actions": "disabled",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _record_stop_receipt(run)

    result = evaluate_legacy_parallel_run(run, finalize=True)

    assert result["counts"]["canonical_receipts"] == 1
    assert result["counts"]["canonical_ledger_bindings"] == 1
    assert result["counts"]["terminal_without_text_items"] == 1
    assert result["terminal_state"] == "blocked_closed"
    assert result["full_capture_confirmed"] is False
    assert "parent_completeness_certificate_missing" in result["blockers"]
    assert "terminal_without_text_items_present" in result["blockers"]


def test_output_is_metadata_only_even_for_malformed_private_inputs(
    tmp_path: Path,
) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    parent = _full_parent_audit()
    parent["private_url"] = "must-not-leak"
    parent["participant"] = "must-not-leak"

    result = evaluate_legacy_parallel_run(
        run,
        parent_audit=parent,
        parent_target_key=PARENT_TARGET,
        finalize=False,
    )
    encoded = json.dumps(result)

    assert result["schema"] == "dcb.parallel-run-operational-closeout.v1"
    assert result["raw_text_returned"] is False
    assert result["participant_names_returned"] is False
    assert result["url_output"] == "omitted"
    assert result["path_output"] == "omitted"
    assert result["outbound_actions"] == "disabled"
    assert result["full_capture_confirmed"] is False
    assert "parent_completeness_source_unverified" in result["blockers"]
    assert "must-not-leak" not in encoded


def test_invalid_run_metadata_fails_closed_without_echoing_path(tmp_path: Path) -> None:
    run = tmp_path / "private-name"
    _write_json(run / "run-metadata.json", {"schema": "wrong"})

    result = evaluate_legacy_parallel_run(run, finalize=True)

    assert result["terminal_state"] == "running"
    assert "run_metadata_invalid" in result["blockers"]
    assert "private-name" not in json.dumps(result)


def test_persist_terminal_closeout_aligns_metadata_and_writes_one_receipt(
    tmp_path: Path,
) -> None:
    run = _run_fixture(
        tmp_path,
        item_count=1,
        status="failed",
        stopped_reason="producer_failed",
    )
    _record_stop_receipt(run)

    persist_legacy_parallel_closeout(run, finalize=True)

    metadata = json.loads((run / "run-metadata.json").read_text(encoding="utf-8"))
    receipt = json.loads(
        (run / "audit" / "parallel-run-closeout.json").read_text(encoding="utf-8")
    )
    assert metadata["status"] == "blocked_closed"
    assert metadata["closeout_report"] == "audit/parallel-run-closeout.json"
    assert receipt["terminal_state"] == "blocked_closed"
    assert receipt["recorded_by"] == "discord-context-bridge"
    assert receipt["full_capture_confirmed"] is False


def test_cli_finalize_writes_blocked_closeout_and_returns_two(
    tmp_path: Path, capsys
) -> None:
    run = _run_fixture(
        tmp_path,
        item_count=1,
        status="failed",
        stopped_reason="producer_failed",
    )
    _record_stop_receipt(run)

    exit_code = main(
        ["closeout-parallel-run", "--run-dir", str(run), "--finalize", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["terminal_state"] == "blocked_closed"
    assert payload["full_capture_confirmed"] is False
    assert payload["path_output"] == "omitted"
    assert payload["persistence_confirmed"] is True
    assert (run / "audit" / "parallel-run-closeout.json").exists()


def test_finalize_refuses_hand_authored_stopped_metadata(tmp_path: Path) -> None:
    run = _run_fixture(
        tmp_path,
        item_count=1,
        status="failed",
        stopped_reason="producer_failed",
    )

    result = evaluate_legacy_parallel_run(run, finalize=True)

    assert result["terminal_state"] == "running"
    assert result["persistence_confirmed"] is False
    assert "producer_stop_receipt_missing" in result["blockers"]


def test_cli_full_uses_canonical_store_and_persists_without_finalize(
    tmp_path: Path, capsys
) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    _record_stop_receipt(run)
    database = _full_completeness_db(tmp_path)

    exit_code = main(
        [
            "closeout-parallel-run",
            "--run-dir",
            str(run),
            "--completeness-db",
            str(database),
            "--parent-target-key",
            PARENT_TARGET,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["terminal_state"] == "full_closed"
    assert payload["full_capture_confirmed"] is True
    assert payload["persistence_confirmed"] is True
    assert (run / "audit" / "parallel-run-closeout.json").exists()


def test_parent_target_binding_rejects_other_canonical_parent(
    tmp_path: Path, capsys
) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    database = _full_completeness_db(tmp_path, target="other-parent")

    exit_code = main(
        [
            "closeout-parallel-run",
            "--run-dir",
            str(run),
            "--completeness-db",
            str(database),
            "--parent-target-key",
            "other-parent",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["terminal_state"] == "running"
    assert "parent_target_binding_mismatch" in payload["blockers"]
    assert not (run / "audit" / "parallel-run-closeout.json").exists()


def test_immutable_closeout_receipt_is_idempotent_and_rejects_regression(
    tmp_path: Path,
) -> None:
    run = _run_fixture(
        tmp_path,
        item_count=1,
        status="failed",
        stopped_reason="producer_failed",
    )
    _record_stop_receipt(run)
    first = persist_legacy_parallel_closeout(run, finalize=True)
    second = persist_legacy_parallel_closeout(run, finalize=True)
    assert second == first

    _record_item(run, 0)
    database = _full_completeness_db(tmp_path)
    try:
        persist_legacy_parallel_closeout(
            run,
            completeness_db=database,
            parent_target_key=PARENT_TARGET,
        )
    except ValueError as exc:
        assert "terminal canonical closeout evidence required" in str(exc)
    else:
        raise AssertionError("terminal closeout receipt must not regress or upgrade")


def test_actual_text_mutation_breaks_artifact_binding(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    (run / "spool" / "worker-0" / "0000.txt").write_text(
        "mutated after receipt", encoding="utf-8"
    )

    result = evaluate_legacy_parallel_run(
        run,
        parent_audit=_full_parent_audit(),
        parent_target_key=PARENT_TARGET,
    )

    assert result["terminal_state"] == "running"
    assert "artifact_hash_binding_mismatch" in result["blockers"]


def test_windows_directory_fsync_is_portably_skipped(
    tmp_path: Path, monkeypatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(parallel_closeout.os, "name", "nt")
        patch.setattr(
            parallel_closeout.os,
            "open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Windows directory open must be skipped")
            ),
        )
        parallel_closeout._fsync_directory(tmp_path)


def test_persistence_succeeds_when_directory_fsync_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    run = _run_fixture(
        tmp_path,
        item_count=1,
        status="failed",
        stopped_reason="producer_failed",
    )
    _record_stop_receipt(run)
    monkeypatch.setattr(parallel_closeout, "_fsync_directory", lambda _path: None)

    result = persist_legacy_parallel_closeout(run, finalize=True)

    assert result["persistence_confirmed"] is True
    assert (run / "audit" / "parallel-run-closeout.json").exists()
    metadata = json.loads((run / "run-metadata.json").read_text(encoding="utf-8"))
    assert metadata["status"] == "blocked_closed"


def test_late_artifact_after_stop_receipt_prevents_finalize(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_stop_receipt(run)
    _record_item(run, 0)

    result = evaluate_legacy_parallel_run(run, finalize=True)

    assert result["terminal_state"] == "running"
    assert "producer_stop_receipt_invalid_or_stale" in result["blockers"]


def test_full_receipt_rejects_parent_retarget_after_closeout(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    _record_stop_receipt(run)
    database = _full_completeness_db(tmp_path)
    first = persist_legacy_parallel_closeout(
        run,
        completeness_db=database,
        parent_target_key=PARENT_TARGET,
    )
    assert first["full_capture_confirmed"] is True

    metadata_path = run / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["parent_target_key_sha256"] = parent_target_key_digest("other-parent")
    _write_json(metadata_path, metadata)
    store = CompletenessStore(database)
    scopes = {"active": True, "archived_public": True, "archived_private": True}
    for index in (1, 2):
        store.record_inventory_scan(
            parent_target_key="other-parent",
            scan_id=f"other-scan-{index}",
            observed_at=f"2026-09-01T01:0{index}:00+00:00",
            thread_ids=["other-thread"],
            scopes=scopes,
            pagination_exhausted=True,
        )
    store.record_child_certificate(
        "other-parent", "other-thread", _full_certificate("other-capture")
    )

    try:
        persist_legacy_parallel_closeout(
            run,
            completeness_db=database,
            parent_target_key="other-parent",
        )
    except ValueError as exc:
        assert "terminal canonical closeout evidence required" in str(exc)
    else:
        raise AssertionError("full receipt must remain bound to its original parent")


def test_drain_and_stop_receipts_are_create_only(tmp_path: Path) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_stop_receipt(run)

    try:
        persist_parallel_producer_drain_receipt(
            run, producer="worker-0", event_id="replacement-event"
        )
    except ValueError as exc:
        assert "immutable receipt already exists" in str(exc)
    else:
        raise AssertionError("producer drain receipt must be create-only")

    try:
        persist_parallel_run_stop_receipt(
            run,
            event_id="replacement-stop",
            stopped_reason="producer_failed",
        )
    except ValueError as exc:
        assert "immutable receipt already exists" in str(exc)
    else:
        raise AssertionError("run stop receipt must be create-only")


def test_atomic_publish_failure_leaves_no_poisoned_final_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    real_link = parallel_closeout.os.link
    with monkeypatch.context() as patch:
        patch.setattr(
            parallel_closeout.os,
            "link",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected")),
        )
        try:
            persist_parallel_producer_drain_receipt(
                run, producer="worker-0", event_id="first-attempt"
            )
        except OSError as exc:
            assert "injected" in str(exc)
        else:
            raise AssertionError("injected atomic publication failure must surface")
    assert not (run / "orchestration" / "terminal" / "worker-0.json").exists()

    monkeypatch.setattr(parallel_closeout.os, "link", real_link)
    receipt = persist_parallel_producer_drain_receipt(
        run, producer="worker-0", event_id="recovery-attempt"
    )
    assert receipt["event_type"] == "producer.drained"


def test_full_evidence_without_all_producer_drains_cannot_close(
    tmp_path: Path,
) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    database = _full_completeness_db(tmp_path)

    result = evaluate_legacy_parallel_run_from_store(
        run,
        completeness_db=database,
        parent_target_key=PARENT_TARGET,
    )

    assert result["full_capture_confirmed"] is False
    assert result["terminal_state"] == "running"
    assert "producer_stop_receipt_missing" in result["blockers"]


def test_cli_event_path_records_drains_then_stop(tmp_path: Path, capsys) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    for producer in ("worker-0", "importer"):
        exit_code = main(
            [
                "record-parallel-producer-drain",
                "--run-dir",
                str(run),
                "--producer",
                producer,
                "--event-id",
                f"{producer}-terminal",
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert payload["ok"] is True
    exit_code = main(
        [
            "record-parallel-run-stop",
            "--run-dir",
            str(run),
            "--event-id",
            "router-terminal",
            "--stopped-reason",
            "producer_failed",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert (run / "audit" / "parallel-run-stop-receipt.json").exists()


def test_late_write_after_full_is_detected_by_canonical_revalidation(
    tmp_path: Path,
) -> None:
    run = _run_fixture(tmp_path, item_count=1)
    _record_item(run, 0)
    _record_stop_receipt(run)
    database = _full_completeness_db(tmp_path)
    persist_legacy_parallel_closeout(
        run,
        completeness_db=database,
        parent_target_key=PARENT_TARGET,
    )
    (run / "spool" / "worker-0" / "0000.txt").write_text(
        "late mutation", encoding="utf-8"
    )

    current = evaluate_legacy_parallel_run_from_store(
        run,
        completeness_db=database,
        parent_target_key=PARENT_TARGET,
    )

    assert current["full_capture_confirmed"] is False
    assert current["terminal_state"] == "running"
    assert "artifact_hash_binding_mismatch" in current["blockers"]
