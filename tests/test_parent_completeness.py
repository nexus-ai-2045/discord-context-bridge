import json
import sqlite3

from discord_context_bridge.completeness_store import CompletenessStore
from discord_context_bridge.cli import main as cli_main


def _scope_receipt(scope: str, target: str, thread_ids: list[str], *, authorized=True) -> dict:
    routes = {
        "active_filtered": "GET /guilds/{guild_id}/threads/active",
        "archived_public": "GET /channels/{channel_id}/threads/archived/public",
        "archived_private": "GET /channels/{channel_id}/threads/archived/private",
    }
    receipt = {
        "route": routes[scope],
        "parent_target_key": target,
        "thread_ids": thread_ids,
        "active_parent_filter_applied": scope == "active_filtered",
        "page_count": 1,
        "terminal_reached": True,
        "terminal_cursor": None,
        "locked_count": 1 if thread_ids else 0,
    }
    if scope == "archived_private":
        receipt["authorization"] = {
            "capability": "manage_threads",
            "confirmed": authorized,
        }
    return receipt


def _canonical_receipts(target: str, *, private=True, authorized=True) -> dict:
    receipts = {
        "active_filtered": _scope_receipt("active_filtered", target, ["t1"]),
        "archived_public": _scope_receipt("archived_public", target, ["t2"]),
    }
    if private:
        receipts["archived_private"] = _scope_receipt(
            "archived_private", target, [], authorized=authorized
        )
    return receipts


def _record_canonical_stable(store: CompletenessStore, target: str, parent_kind: str) -> None:
    for index in (1, 2):
        store.record_inventory_scan(
            parent_target_key=target,
            scan_id=f"canonical-{index}",
            observed_at=f"2026-07-28T10:0{index}:00+09:00",
            parent_kind=parent_kind,
            scope_receipts=_canonical_receipts(target, private=parent_kind == "text"),
        )


def test_announcement_parent_requires_no_private_archive_scope(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "announcement-parent"
    _record_canonical_stable(store, target, "announcement")
    store.record_child_certificate(target, "t1", _full_certificate("c1"))
    store.record_child_certificate(target, "t2", _full_certificate("c2"))

    result = store.audit_parent(target)

    assert result["status"] == "full"
    assert result["parent_full_capture_confirmed"] is True


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
    _record_canonical_stable(store, target, "forum")


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
    first = _canonical_receipts(target, private=False)
    first["archived_public"]["thread_ids"] = []
    first["archived_public"]["locked_count"] = 0
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        parent_kind="forum",
        scope_receipts=first,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        parent_kind="forum",
        scope_receipts=_canonical_receipts(target, private=False),
    )

    result = store.audit_parent(target)

    assert result["status"] == "partial"
    assert "inventory_rescan_not_stable" in result["blockers"]


def test_missing_archived_scope_or_pending_child_fails_closed(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    receipts = _canonical_receipts(target, private=False)
    del receipts["archived_public"]
    for index in (1, 2):
        store.record_inventory_scan(
            parent_target_key=target,
            scan_id=f"scan-{index}",
            observed_at=f"2026-07-28T09:0{index}:00+09:00",
            parent_kind="forum",
            scope_receipts=receipts,
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
    incomplete = _canonical_receipts(target, private=False)
    incomplete["active_filtered"]["terminal_reached"] = False
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        parent_kind="forum",
        scope_receipts=incomplete,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        parent_kind="forum",
        scope_receipts={
            "active_filtered": _scope_receipt("active_filtered", target, ["t1"]),
            "archived_public": _scope_receipt("archived_public", target, []),
        },
    )
    store.record_child_certificate(target, "t1", _full_certificate("c1"))

    result = store.audit_parent(target)

    assert result["status"] != "full"
    assert "inventory_rescan_incomplete" in result["blockers"]


def test_absent_certificate_is_retired_after_stable_inventory(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    two_threads = _canonical_receipts(target, private=False)
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-1",
        observed_at="2026-07-28T09:00:00+09:00",
        parent_kind="forum",
        scope_receipts=two_threads,
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-2",
        observed_at="2026-07-28T09:01:00+09:00",
        parent_kind="forum",
        scope_receipts=two_threads,
    )
    store.record_child_certificate(target, "t1", _full_certificate("c1"))
    store.record_child_certificate(target, "t2", _full_certificate("c2"))
    # Thread t2 disappears from later stable inventories.
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-3",
        observed_at="2026-07-28T09:02:00+09:00",
        parent_kind="forum",
        scope_receipts={
            "active_filtered": _scope_receipt("active_filtered", target, ["t1"]),
            "archived_public": _scope_receipt("archived_public", target, []),
        },
    )
    store.record_inventory_scan(
        parent_target_key=target,
        scan_id="scan-4",
        observed_at="2026-07-28T09:03:00+09:00",
        parent_kind="forum",
        scope_receipts={
            "active_filtered": _scope_receipt("active_filtered", target, ["t1"]),
            "archived_public": _scope_receipt("archived_public", target, []),
        },
    )

    result = store.audit_parent(target)

    assert result["status"] == "full"
    assert result["counts"]["retired_certificates"] == 1
    assert "child_certificate_not_in_latest_inventory" not in result["blockers"]


def test_forum_requires_only_active_filtered_and_archived_public_receipts(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    _record_canonical_stable(store, "forum-parent", "forum")
    store.record_child_certificate("forum-parent", "t1", _full_certificate("c1"))
    store.record_child_certificate("forum-parent", "t2", _full_certificate("c2"))

    result = store.audit_parent("forum-parent")

    assert result["status"] == "full"
    assert result["inventory"]["required_scopes"] == ["active_filtered", "archived_public"]
    assert result["counts"]["locked_threads"] == 2
    assert "archived_private" not in str(result)


def test_text_requires_authorized_private_archive_receipt(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "text-parent"
    for index in (1, 2):
        store.record_inventory_scan(
            parent_target_key=target,
            scan_id=f"scan-{index}",
            observed_at=f"2026-07-28T10:0{index}:00+09:00",
            parent_kind="text",
            scope_receipts=_canonical_receipts(target, authorized=False),
        )

    result = store.audit_parent(target)

    assert result["status"] == "partial"
    assert "private_archive_authorization_missing" in result["blockers"]


def test_stable_rescan_compares_scope_sets_not_cursor_or_locked_attributes(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    target = "forum-parent"
    first = _canonical_receipts(target, private=False)
    second = _canonical_receipts(target, private=False)
    second["active_filtered"]["locked_count"] = 0
    second["archived_public"]["page_count"] = 2
    second["archived_public"]["terminal_cursor"] = "different-terminal-cursor"
    for index, receipts in enumerate((first, second), 1):
        store.record_inventory_scan(
            parent_target_key=target,
            scan_id=f"scan-{index}",
            observed_at=f"2026-07-28T10:0{index}:00+09:00",
            parent_kind="forum",
            scope_receipts=receipts,
        )
    store.record_child_certificate(target, "t1", _full_certificate("c1"))
    store.record_child_certificate(target, "t2", _full_certificate("c2"))

    result = store.audit_parent(target)

    assert result["status"] == "full"
    assert result["inventory"]["stable_scan_count"] == 2
    assert result["counts"]["locked_threads"] == 1


def test_scope_receipt_route_parent_filter_and_terminal_are_validated(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    invalid_cases = []
    for field, value, error in (
        ("route", "GET /wrong", "scope_receipt_route_mismatch"),
        ("parent_target_key", "other", "scope_receipt_parent_binding_mismatch"),
        (
            "active_parent_filter_applied",
            False,
            "scope_receipt_active_parent_filter_required",
        ),
        ("terminal_reached", "yes", "scope_receipt_terminal_evidence_required"),
    ):
        receipts = _canonical_receipts("forum-parent", private=False)
        receipts["active_filtered"][field] = value
        invalid_cases.append((receipts, error))
    missing_cursor = _canonical_receipts("forum-parent", private=False)
    del missing_cursor["active_filtered"]["terminal_cursor"]
    invalid_cases.append((missing_cursor, "scope_receipt_terminal_evidence_required"))

    for index, (receipts, error) in enumerate(invalid_cases):
        try:
            store.record_inventory_scan(
                parent_target_key="forum-parent",
                scan_id=f"bad-{index}",
                observed_at="2026-07-28T10:01:00+09:00",
                parent_kind="forum",
                scope_receipts=receipts,
            )
        except ValueError as exc:
            assert error in str(exc)
        else:
            raise AssertionError(f"invalid scope receipt must fail: {error}")


def test_legacy_scans_remain_incomplete_after_schema_migration(tmp_path):
    database = tmp_path / "capture.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE parent_targets (target_key TEXT PRIMARY KEY, created_at TEXT);
            CREATE TABLE inventory_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_target_key TEXT NOT NULL,
                scan_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                thread_count INTEGER NOT NULL,
                thread_set_digest TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                pagination_exhausted INTEGER NOT NULL,
                UNIQUE(parent_target_key, scan_id)
            );
            CREATE TABLE inventory_threads (
                inventory_scan_id INTEGER NOT NULL,
                thread_id TEXT NOT NULL,
                PRIMARY KEY(inventory_scan_id, thread_id)
            );
            INSERT INTO parent_targets VALUES ('legacy-parent', CURRENT_TIMESTAMP);
            INSERT INTO inventory_scans VALUES
                (1, 'legacy-parent', 'legacy-1', '2026-07-28T01:01:00+00:00', 1, 'same',
                 '{"active":true,"archived_public":true,"archived_private":true}', 1),
                (2, 'legacy-parent', 'legacy-2', '2026-07-28T01:02:00+00:00', 1, 'same',
                 '{"active":true,"archived_public":true,"archived_private":true}', 1);
            INSERT INTO inventory_threads VALUES (1, 't1'), (2, 't1');
            """
        )
    store = CompletenessStore(database)
    store.initialize()

    result = store.audit_parent("legacy-parent")

    assert result["status"] == "partial"
    assert "inventory_scope_receipts_missing" in result["blockers"]
    assert result["inventory"]["evidence_model"] == "legacy_aggregate"


def test_cli_records_canonical_receipts_without_returning_private_ids(tmp_path, capsys):
    target = "private-parent-key"
    evidence = {
        "parent_target_key": target,
        "scan_id": "private-scan-id",
        "observed_at": "2026-07-28T10:01:00+09:00",
        "parent_kind": "media",
        "scope_receipts": _canonical_receipts(target, private=False),
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    exit_code = cli_main(
        [
            "record-parent-inventory",
            "--db",
            str(tmp_path / "capture.sqlite3"),
            "--evidence",
            str(evidence_path),
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert target not in output
    assert "private-scan-id" not in output
    assert "t1" not in output
    assert "t2" not in output


def test_scope_receipt_requires_explicit_list_of_nonempty_thread_ids(tmp_path):
    store = CompletenessStore(tmp_path / "capture.sqlite3")
    store.initialize()
    invalid_values = (None, "t1", [""], [None])
    for index, invalid_value in enumerate(invalid_values):
        receipts = _canonical_receipts("forum-parent", private=False)
        if invalid_value is None:
            del receipts["active_filtered"]["thread_ids"]
        else:
            receipts["active_filtered"]["thread_ids"] = invalid_value
        try:
            store.record_inventory_scan(
                parent_target_key="forum-parent",
                scan_id=f"invalid-threads-{index}",
                observed_at="2026-07-28T10:01:00+09:00",
                parent_kind="forum",
                scope_receipts=receipts,
            )
        except ValueError as exc:
            assert "scope_receipt_thread_ids_invalid" in str(exc)
        else:
            raise AssertionError("missing or malformed thread_ids must fail")


def test_corrupt_stored_scope_receipt_fails_closed_without_exception(tmp_path):
    database = tmp_path / "capture.sqlite3"
    store = CompletenessStore(database)
    store.initialize()
    _record_canonical_stable(store, "forum-parent", "forum")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE inventory_scans SET scope_receipts_json = ? WHERE scan_id = ?",
            ('{"active_filtered":true}', "canonical-2"),
        )

    result = store.audit_parent("forum-parent")

    assert result["status"] == "partial"
    assert "inventory_scope_receipts_invalid" in result["blockers"]
    assert result["parent_full_capture_confirmed"] is False
