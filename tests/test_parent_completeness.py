from discord_context_bridge.completeness_store import CompletenessStore
from discord_context_bridge.cli import main as cli_main


def _full_certificate(capture_id: str) -> dict:
    return {
        "schema": "discord_full_capture_completion_gate.v1",
        "capture_id": capture_id,
        "status": "full",
        "full_capture_confirmed": True,
        "counts": {
            "messages": 3,
            "attachments_discovered": 1,
            "attachments_saved": 1,
            "attachments_manifested": 1,
        },
        "attachments_consistent": True,
        "unresolved_gap_count": 0,
        "blockers": [],
    }


def _record_stable_inventory(store: CompletenessStore, target: str) -> None:
    scopes = {"active": True, "archived_public": True, "archived_private": True}
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        thread_ids=["t1", "t2"],
        scopes=scopes,
        pagination_exhausted=True,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        thread_ids=["t2", "t1"],
        scopes=scopes,
        pagination_exhausted=True,
    )


def test_parent_audit_requires_two_stable_complete_inventory_scans(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    _record_stable_inventory(store, target)

    result = store.audit_parent(target)

    assert result["status"] == "partial"
    assert "child_capture_certificate_missing" in result["blockers"]
    assert result["inventory"]["stable_scan_count"] == 2
    assert result["algorithm_ids"] == [
        "pagination_exhaustion",
        "stable_rescan",
        "set_reconciliation",
        "strict_child_full_capture",
        "attachment_manifest_reconciliation",
        "pending_work_zero",
    ]


def test_parent_audit_is_full_when_inventory_and_all_children_reconcile(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    _record_stable_inventory(store, target)
    store.record_child_certificate(target, "t1", _full_certificate("c1"))
    store.record_child_certificate(target, "t2", _full_certificate("c2"))

    result = store.audit_parent(target)

    assert result["status"] == "full"
    assert result["parent_full_capture_confirmed"] is True
    assert result["counts"]["inventory_threads"] == 2
    assert result["counts"]["full_children"] == 2
    assert result["blockers"] == []
    assert "t1" not in str(result)
    assert "t2" not in str(result)


def test_changed_second_inventory_scan_blocks_full(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    scopes = {"active": True, "archived_public": True, "archived_private": True}
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        thread_ids=["t1"],
        scopes=scopes,
        pagination_exhausted=True,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        thread_ids=["t1", "t2"],
        scopes=scopes,
        pagination_exhausted=True,
    )

    result = store.audit_parent(target)

    assert result["status"] == "partial"
    assert "inventory_rescan_not_stable" in result["blockers"]


def test_missing_archived_scope_or_pending_child_fails_closed(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    scopes = {"active": True, "archived_public": False, "archived_private": True}
    for index in (1, 2):
        store.record_inventory_scan(
            parent_target_key=target,
            scan_id=f"scan-{index}",
            observed_at=f"2026-07-28T09:0{index}:00+09:00",
            thread_ids=["t1"],
            scopes=scopes,
            pagination_exhausted=True,
        )
    certificate = _full_certificate("c1")
    certificate["pending_retry_count"] = 1
    store.record_child_certificate(target, "t1", certificate)

    result = store.audit_parent(target)

    assert "inventory_scope_incomplete" in result["blockers"]
    assert "child_pending_work_present" in result["blockers"]


def test_database_foreign_keys_reject_child_without_parent(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()

    try:
        store.record_child_certificate("missing", "t1", _full_certificate("c1"))
    except ValueError as exc:
        assert "parent_inventory_missing" in str(exc)
    else:
        raise AssertionError("missing parent inventory must fail")


def test_cli_audit_is_metadata_only(tmp_path, capsys):
    database = tmp_path / "capture.sqlite3"
    store = CompletenessStore(database)
    store.initialize()
    _record_stable_inventory(store, "private-parent-key")

    exit_code = cli_main(
        [
            "audit-parent-completeness",
            "--db",
            str(database),
            "--parent-target-key",
            "private-parent-key",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "private-parent-key" not in output
    assert str(database) not in output


def test_string_false_certificate_flags_are_rejected(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    _record_stable_inventory(store, "forum-parent")
    certificate = _full_certificate("c1")
    certificate["full_capture_confirmed"] = "false"  # truthy string must not pass

    try:
        store.record_child_certificate("forum-parent", "t1", certificate)
    except ValueError as exc:
        assert "full_capture_confirmed_boolean_required" in str(exc)
    else:
        raise AssertionError("string flags must fail closed")


def test_capture_id_cannot_be_reused_across_threads(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    _record_stable_inventory(store, "forum-parent")
    store.record_child_certificate("forum-parent", "t1", _full_certificate("same-capture"))

    try:
        store.record_child_certificate("forum-parent", "t2", _full_certificate("same-capture"))
    except ValueError as exc:
        assert "capture_id_already_bound_to_other_thread" in str(exc)
    else:
        raise AssertionError("capture_id reuse must fail closed")


def test_incomplete_older_scan_blocks_stable_full(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    scopes = {"active": True, "archived_public": True, "archived_private": True}
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        thread_ids=["t1"],
        scopes=scopes,
        pagination_exhausted=False,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        thread_ids=["t1"],
        scopes=scopes,
        pagination_exhausted=True,
    )
    store.record_child_certificate(target, "t1", _full_certificate("c1"))

    result = store.audit_parent(target)

    assert result["status"] != "full"
    assert "inventory_rescan_incomplete" in result["blockers"]


def test_absent_certificate_is_retired_after_stable_inventory(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    scopes = {"active": True, "archived_public": True, "archived_private": True}
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        thread_ids=["t1", "t2"],
        scopes=scopes,
        pagination_exhausted=True,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        thread_ids=["t1", "t2"],
        scopes=scopes,
        pagination_exhausted=True,
    )
    store.record_child_certificate(target, "t1", _full_certificate("c1"))
    store.record_child_certificate(target, "t2", _full_certificate("c2"))
    # Thread t2 disappears from later stable inventories.
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-3",
        observed_at="2026-07-28T09:02:00+09:00",
        thread_ids=["t1"],
        scopes=scopes,
        pagination_exhausted=True,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-4",
        observed_at="2026-07-28T09:03:00+09:00",
        thread_ids=["t1"],
        scopes=scopes,
        pagination_exhausted=True,
    )

    result = store.audit_parent(target)

    assert result["status"] == "full"
    assert result["counts"]["retired_certificates"] == 1
    assert "child_certificate_not_in_latest_inventory" not in result["blockers"]
