"""親チャンネル配下の取得完全性を、正規化 SQLite 証拠から監査する。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ALGORITHM_IDS = [
    "pagination_exhaustion",
    "stable_rescan",
    "set_reconciliation",
    "strict_child_full_capture",
    "attachment_manifest_reconciliation",
    "pending_work_zero",
]
REQUIRED_INVENTORY_SCOPES = {"active_filtered", "archived_public", "archived_private"}


def _digest_ids(values: Sequence[str]) -> str:
    framed = "".join(f"{len(value)}:{value}" for value in sorted(set(values)))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _normalized_time(value: str) -> str:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("observed_at_timezone_required")
    return parsed.astimezone(timezone.utc).isoformat()


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}_boolean_required")
    return value


def _inventory_complete(scopes: Mapping[str, Any], pagination_exhausted: bool) -> bool:
    if not pagination_exhausted:
        return False
    return all(bool(scopes.get(scope)) for scope in REQUIRED_INVENTORY_SCOPES)


def _canonical_scopes(scopes: Mapping[str, bool]) -> dict[str, bool]:
    """Accept the legacy ``active`` spelling but persist the canonical scope name."""

    normalized = dict(scopes)
    if "active_filtered" not in normalized and "active" in normalized:
        normalized["active_filtered"] = normalized.pop("active")
    return normalized


def _thread_records(scope: Mapping[str, Any]) -> tuple[list[str], dict[str, bool]]:
    threads = scope.get("threads")
    if not isinstance(threads, Sequence) or isinstance(threads, (str, bytes)):
        raise ValueError("inventory_threads_invalid")
    ids: list[str] = []
    locked_by_id: dict[str, bool] = {}
    for item in threads:
        if not isinstance(item, Mapping):
            raise ValueError("inventory_thread_invalid")
        thread_id = item.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("inventory_thread_id_missing")
        metadata = item.get("thread_metadata")
        locked = False
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise ValueError("inventory_thread_metadata_invalid")
            locked_value = metadata.get("locked", False)
            if not isinstance(locked_value, bool):
                raise ValueError("inventory_locked_boolean_required")
            locked = locked_value
        if thread_id in locked_by_id:
            raise ValueError("duplicate_thread_id")
        ids.append(thread_id)
        locked_by_id[thread_id] = locked
    return ids, locked_by_id


class CompletenessStore:
    """取得証拠を local SQLite に保持し、本文・IDなしの監査結果を返す。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _harden_path(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        if self.path.exists():
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        self._harden_path()
        created = not self.path.exists()
        if created:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            try:
                fd = os.open(self.path, flags, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
            except OSError:
                pass
        connection = sqlite3.connect(self.path)
        if self.path.exists():
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS parent_targets (
                    target_key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS inventory_scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_target_key TEXT NOT NULL
                        REFERENCES parent_targets(target_key) ON DELETE CASCADE,
                    scan_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    thread_count INTEGER NOT NULL CHECK(thread_count >= 0),
                    thread_set_digest TEXT NOT NULL,
                    scope_set_digests_json TEXT NOT NULL DEFAULT '{}',
                    locked_count INTEGER NOT NULL DEFAULT 0 CHECK(locked_count >= 0),
                    scopes_json TEXT NOT NULL,
                    pagination_exhausted INTEGER NOT NULL CHECK(pagination_exhausted IN (0, 1)),
                    UNIQUE(parent_target_key, scan_id)
                );
                CREATE TABLE IF NOT EXISTS inventory_threads (
                    inventory_scan_id INTEGER NOT NULL
                        REFERENCES inventory_scans(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL,
                    PRIMARY KEY(inventory_scan_id, thread_id)
                );
                CREATE TABLE IF NOT EXISTS child_capture_certificates (
                    parent_target_key TEXT NOT NULL
                        REFERENCES parent_targets(target_key) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    gate_schema TEXT NOT NULL,
                    status TEXT NOT NULL,
                    full_capture_confirmed INTEGER NOT NULL CHECK(full_capture_confirmed IN (0, 1)),
                    message_count INTEGER NOT NULL CHECK(message_count >= 0),
                    attachment_discovered_count INTEGER NOT NULL CHECK(attachment_discovered_count >= 0),
                    attachment_saved_count INTEGER NOT NULL CHECK(attachment_saved_count >= 0),
                    attachment_manifested_count INTEGER NOT NULL CHECK(attachment_manifested_count >= 0),
                    attachments_consistent INTEGER NOT NULL CHECK(attachments_consistent IN (0, 1)),
                    unresolved_gap_count INTEGER NOT NULL CHECK(unresolved_gap_count >= 0),
                    pending_retry_count INTEGER NOT NULL CHECK(pending_retry_count >= 0),
                    blockers_json TEXT NOT NULL,
                    PRIMARY KEY(parent_target_key, thread_id)
                );
                CREATE TABLE IF NOT EXISTS child_certificate_retirements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_target_key TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    capture_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    retired_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(inventory_scans)").fetchall()
            }
            if "scope_set_digests_json" not in columns:
                connection.execute(
                    "ALTER TABLE inventory_scans "
                    "ADD COLUMN scope_set_digests_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "locked_count" not in columns:
                connection.execute(
                    "ALTER TABLE inventory_scans "
                    "ADD COLUMN locked_count INTEGER NOT NULL DEFAULT 0"
                )

    def record_inventory_scan(
        self,
        *,
        parent_target_key: str,
        scan_id: str,
        observed_at: str,
        thread_ids: Sequence[str],
        scopes: Mapping[str, bool],
        pagination_exhausted: bool,
        scope_thread_ids: Mapping[str, Sequence[str]] | None = None,
        locked_count: int = 0,
    ) -> None:
        normalized_ids = [str(value) for value in thread_ids]
        normalized_scopes = _canonical_scopes(scopes)
        if not parent_target_key.strip() or not scan_id.strip():
            raise ValueError("inventory_binding_required")
        if not isinstance(pagination_exhausted, bool):
            raise ValueError("pagination_exhausted_boolean_required")
        if any(
            scope not in normalized_scopes or not isinstance(normalized_scopes[scope], bool)
            for scope in REQUIRED_INVENTORY_SCOPES
        ):
            raise ValueError("inventory_scope_boolean_required")
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("duplicate_thread_id")
        if not isinstance(locked_count, int) or isinstance(locked_count, bool) or locked_count < 0:
            raise ValueError("locked_count_nonnegative_integer_required")
        if locked_count > len(normalized_ids):
            raise ValueError("locked_count_exceeds_thread_count")
        if scope_thread_ids is None:
            normalized_scope_ids = {
                scope: list(normalized_ids) for scope in REQUIRED_INVENTORY_SCOPES
            }
        else:
            normalized_scope_ids = {}
            canonical_scope_ids = dict(scope_thread_ids)
            if "active_filtered" not in canonical_scope_ids and "active" in canonical_scope_ids:
                canonical_scope_ids["active_filtered"] = canonical_scope_ids.pop("active")
            for scope in REQUIRED_INVENTORY_SCOPES:
                values = canonical_scope_ids.get(scope)
                if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                    raise ValueError("inventory_scope_thread_ids_required")
                normalized_values = [str(value) for value in values]
                if len(normalized_values) != len(set(normalized_values)):
                    raise ValueError("duplicate_thread_id")
                normalized_scope_ids[scope] = normalized_values
            if set().union(*(set(values) for values in normalized_scope_ids.values())) != set(
                normalized_ids
            ):
                raise ValueError("inventory_scope_thread_set_mismatch")
        scope_set_digests = {
            scope: _digest_ids(values) for scope, values in normalized_scope_ids.items()
        }
        normalized_observed_at = _normalized_time(observed_at)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO parent_targets(target_key) VALUES (?)",
                (parent_target_key,),
            )
            cursor = connection.execute(
                """
                INSERT INTO inventory_scans(
                    parent_target_key, scan_id, observed_at, thread_count,
                    thread_set_digest, scope_set_digests_json, locked_count,
                    scopes_json, pagination_exhausted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_target_key,
                    scan_id,
                    normalized_observed_at,
                    len(normalized_ids),
                    _digest_ids(normalized_ids),
                    json.dumps(scope_set_digests, sort_keys=True),
                    locked_count,
                    json.dumps(normalized_scopes, sort_keys=True),
                    int(pagination_exhausted),
                ),
            )
            scan_row_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO inventory_threads(inventory_scan_id, thread_id) VALUES (?, ?)",
                [(scan_row_id, thread_id) for thread_id in normalized_ids],
            )

    def record_archive_inventory_scan(
        self,
        *,
        parent_target_key: str,
        scan_id: str,
        observed_at: str,
        active_filtered: Mapping[str, Any],
        archive_inventory: Mapping[str, Any],
    ) -> None:
        """Bind archive enumeration output to the existing completeness scan ledger."""

        if archive_inventory.get("schema") != "dcb.archived-thread-inventory-private.v1":
            raise ValueError("archive_inventory_schema_invalid")
        if any(
            str(payload.get("parent_target_key") or "") != parent_target_key
            for payload in (active_filtered, archive_inventory)
        ):
            raise ValueError("inventory_parent_binding_mismatch")
        archive_scopes = archive_inventory.get("scopes")
        if not isinstance(archive_scopes, Mapping):
            raise ValueError("inventory_required_scope_missing")
        required_archive_scopes = ("public", "private", "joined_private")
        if any(not isinstance(archive_scopes.get(scope), Mapping) for scope in required_archive_scopes):
            raise ValueError("inventory_required_scope_missing")
        if not isinstance(active_filtered, Mapping):
            raise ValueError("inventory_required_scope_missing")

        source_scopes: dict[str, Mapping[str, Any]] = {
            "active_filtered": active_filtered,
            **{scope: archive_scopes[scope] for scope in required_archive_scopes},
        }
        terminal: dict[str, bool] = {}
        ids_by_source: dict[str, list[str]] = {}
        locked_by_id: dict[str, bool] = {}
        for scope, payload in source_scopes.items():
            exhausted = payload.get("pagination_exhausted")
            if not isinstance(exhausted, bool):
                raise ValueError("pagination_exhausted_boolean_required")
            terminal[scope] = exhausted
            ids, scope_locked = _thread_records(payload)
            ids_by_source[scope] = ids
            for thread_id, locked in scope_locked.items():
                previous = locked_by_id.get(thread_id)
                if previous is not None and previous != locked:
                    raise ValueError("inventory_locked_state_conflict")
                locked_by_id[thread_id] = locked

        scope_thread_ids = {
            "active_filtered": ids_by_source["active_filtered"],
            "archived_public": ids_by_source["public"],
            "archived_private": sorted(
                set(ids_by_source["private"]) | set(ids_by_source["joined_private"])
            ),
        }
        all_thread_ids = sorted(set().union(*(set(values) for values in scope_thread_ids.values())))
        scopes = {
            "active_filtered": terminal["active_filtered"],
            "archived_public": terminal["public"],
            "archived_private": terminal["private"] and terminal["joined_private"],
        }
        self.record_inventory_scan(
            parent_target_key=parent_target_key,
            scan_id=scan_id,
            observed_at=observed_at,
            thread_ids=all_thread_ids,
            scopes=scopes,
            pagination_exhausted=all(terminal.values()),
            scope_thread_ids=scope_thread_ids,
            locked_count=sum(1 for value in locked_by_id.values() if value),
        )

    def record_child_certificate(
        self,
        parent_target_key: str,
        thread_id: str,
        certificate: Mapping[str, Any],
    ) -> None:
        if not str(thread_id).strip():
            raise ValueError("thread_id_required")
        capture_id = str(certificate.get("capture_id") or "").strip()
        if not capture_id:
            raise ValueError("capture_id_required")
        # Optional explicit thread binding inside the certificate must match CLI thread_id.
        bound_thread = certificate.get("thread_id") or certificate.get("target_thread_id")
        if bound_thread is not None and str(bound_thread) != str(thread_id):
            raise ValueError("certificate_thread_binding_mismatch")
        full_capture_confirmed = _require_bool(
            certificate.get("full_capture_confirmed"), "full_capture_confirmed"
        )
        attachments_consistent = _require_bool(
            certificate.get("attachments_consistent"), "attachments_consistent"
        )
        with self._connect() as connection:
            parent_exists = connection.execute(
                "SELECT 1 FROM parent_targets WHERE target_key = ?",
                (parent_target_key,),
            ).fetchone()
            if parent_exists is None:
                raise ValueError("parent_inventory_missing")
            # Reject reusing one capture_id for multiple child threads under the same parent.
            conflict = connection.execute(
                """
                SELECT thread_id FROM child_capture_certificates
                WHERE parent_target_key = ? AND capture_id = ? AND thread_id != ?
                """,
                (parent_target_key, capture_id, thread_id),
            ).fetchone()
            if conflict is not None:
                raise ValueError("capture_id_already_bound_to_other_thread")
            counts = certificate.get("counts")
            if not isinstance(counts, Mapping):
                counts = {}
            connection.execute(
                """
                INSERT INTO child_capture_certificates(
                    parent_target_key, thread_id, capture_id, gate_schema, status,
                    full_capture_confirmed, message_count,
                    attachment_discovered_count, attachment_saved_count,
                    attachment_manifested_count, attachments_consistent,
                    unresolved_gap_count, pending_retry_count, blockers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(parent_target_key, thread_id) DO UPDATE SET
                    capture_id=excluded.capture_id,
                    gate_schema=excluded.gate_schema,
                    status=excluded.status,
                    full_capture_confirmed=excluded.full_capture_confirmed,
                    message_count=excluded.message_count,
                    attachment_discovered_count=excluded.attachment_discovered_count,
                    attachment_saved_count=excluded.attachment_saved_count,
                    attachment_manifested_count=excluded.attachment_manifested_count,
                    attachments_consistent=excluded.attachments_consistent,
                    unresolved_gap_count=excluded.unresolved_gap_count,
                    pending_retry_count=excluded.pending_retry_count,
                    blockers_json=excluded.blockers_json
                """,
                (
                    parent_target_key,
                    thread_id,
                    capture_id,
                    str(certificate.get("schema") or ""),
                    str(certificate.get("status") or "blocked"),
                    int(full_capture_confirmed),
                    int(counts.get("messages") or 0),
                    int(counts.get("attachments_discovered") or 0),
                    int(counts.get("attachments_saved") or 0),
                    int(counts.get("attachments_manifested") or 0),
                    int(attachments_consistent),
                    int(certificate.get("unresolved_gap_count") or 0),
                    int(certificate.get("pending_retry_count") or 0),
                    json.dumps(certificate.get("blockers") or [], ensure_ascii=False),
                ),
            )

    def _retire_absent_certificates(
        self,
        connection: sqlite3.Connection,
        *,
        parent_target_key: str,
        latest_thread_ids: set[str],
        inventory_complete_and_stable: bool,
    ) -> int:
        if not inventory_complete_and_stable:
            return 0
        rows = connection.execute(
            """
            SELECT thread_id, capture_id FROM child_capture_certificates
            WHERE parent_target_key = ?
            """,
            (parent_target_key,),
        ).fetchall()
        retired = 0
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            if row["thread_id"] in latest_thread_ids:
                continue
            connection.execute(
                """
                INSERT INTO child_certificate_retirements(
                    parent_target_key, thread_id, capture_id, reason, retired_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    parent_target_key,
                    row["thread_id"],
                    row["capture_id"],
                    "absent_from_latest_stable_inventory",
                    now,
                ),
            )
            connection.execute(
                """
                DELETE FROM child_capture_certificates
                WHERE parent_target_key = ? AND thread_id = ?
                """,
                (parent_target_key, row["thread_id"]),
            )
            retired += 1
        return retired

    def audit_parent(self, parent_target_key: str) -> dict[str, Any]:
        blockers: list[str] = []
        retired_count = 0
        both_complete = False
        stable = False
        with self._connect() as connection:
            scans = connection.execute(
                """
                SELECT * FROM inventory_scans
                WHERE parent_target_key = ?
                ORDER BY observed_at DESC, id DESC LIMIT 2
                """,
                (parent_target_key,),
            ).fetchall()
            if len(scans) < 2:
                blockers.append("stable_inventory_scan_count_insufficient")

            if len(scans) == 2:
                parsed_complete = []
                parsed_scope_digests = []
                for scan in scans:
                    scopes_probe = json.loads(scan["scopes_json"])
                    complete = _inventory_complete(scopes_probe, bool(scan["pagination_exhausted"]))
                    parsed_complete.append(complete)
                    digests_probe = json.loads(scan["scope_set_digests_json"])
                    parsed_scope_digests.append(digests_probe)
                both_complete = all(parsed_complete)
                if not both_complete:
                    blockers.append("inventory_rescan_incomplete")
                per_scope_digests_valid = all(
                    isinstance(digests, Mapping)
                    and set(digests) == REQUIRED_INVENTORY_SCOPES
                    and all(isinstance(value, str) and value for value in digests.values())
                    for digests in parsed_scope_digests
                )
                digest_match = (
                    scans[0]["thread_set_digest"] == scans[1]["thread_set_digest"]
                    and scans[0]["thread_count"] == scans[1]["thread_count"]
                    and per_scope_digests_valid
                    and parsed_scope_digests[0] == parsed_scope_digests[1]
                )
                stable = both_complete and digest_match
                if both_complete and not digest_match:
                    blockers.append("inventory_rescan_not_stable")

            latest = scans[0] if scans else None
            thread_ids: set[str] = set()
            scopes: dict[str, bool] = {}
            pagination_exhausted = False
            if latest is None:
                blockers.append("parent_inventory_missing")
            else:
                thread_ids = {
                    row["thread_id"]
                    for row in connection.execute(
                        "SELECT thread_id FROM inventory_threads WHERE inventory_scan_id = ?",
                        (latest["id"],),
                    )
                }
                scopes = json.loads(latest["scopes_json"])
                pagination_exhausted = bool(latest["pagination_exhausted"])
                if not pagination_exhausted:
                    blockers.append("inventory_pagination_not_exhausted")
                if not all(bool(scopes.get(scope)) for scope in REQUIRED_INVENTORY_SCOPES):
                    blockers.append("inventory_scope_incomplete")

            retired_count = self._retire_absent_certificates(
                connection,
                parent_target_key=parent_target_key,
                latest_thread_ids=thread_ids,
                inventory_complete_and_stable=stable,
            )

            certificates = connection.execute(
                """
                SELECT * FROM child_capture_certificates
                WHERE parent_target_key = ?
                """,
                (parent_target_key,),
            ).fetchall()
            certificates_by_thread = {row["thread_id"]: row for row in certificates}

        certificate_ids = set(certificates_by_thread)
        if thread_ids - certificate_ids:
            blockers.append("child_capture_certificate_missing")
        if certificate_ids - thread_ids:
            blockers.append("child_certificate_not_in_latest_inventory")

        full_children = 0
        for thread_id in thread_ids:
            certificate = certificates_by_thread.get(thread_id)
            if certificate is None:
                continue
            attachment_counts_equal = (
                certificate["attachment_discovered_count"]
                == certificate["attachment_saved_count"]
                == certificate["attachment_manifested_count"]
            )
            if (
                certificate["status"] == "full"
                and certificate["gate_schema"] == "discord_full_capture_completion_gate.v1"
                and bool(certificate["full_capture_confirmed"])
                and certificate["capture_id"]
                and certificate["message_count"] > 0
                and certificate["unresolved_gap_count"] == 0
                and bool(certificate["attachments_consistent"])
                and attachment_counts_equal
                and certificate["pending_retry_count"] == 0
                and json.loads(certificate["blockers_json"]) == []
            ):
                full_children += 1
            else:
                if certificate["pending_retry_count"] != 0:
                    blockers.append("child_pending_work_present")
                if not bool(certificate["attachments_consistent"]) or not attachment_counts_equal:
                    blockers.append("child_attachment_reconciliation_failed")
                blockers.append("child_strict_full_capture_failed")

        blockers = list(dict.fromkeys(blockers))
        status = "full" if not blockers else "blocked" if latest is None else "partial"
        return {
            "language": "ja",
            "schema": "discord_parent_completeness_certificate.v1",
            "status": status,
            "parent_full_capture_confirmed": status == "full",
            "algorithm_ids": ALGORITHM_IDS,
            "inventory": {
                "stable_scan_count": 2 if stable else 0,
                "pagination_exhausted": pagination_exhausted,
                "required_scopes_complete": all(
                    bool(scopes.get(scope)) for scope in REQUIRED_INVENTORY_SCOPES
                ),
                "both_latest_scans_complete": both_complete if len(scans) == 2 else False,
            },
            "counts": {
                "inventory_threads": len(thread_ids),
                "child_certificates": len(certificates),
                "full_children": full_children,
                "pending_children": len(thread_ids) - full_children,
                "retired_certificates": retired_count,
                "locked_threads": int(latest["locked_count"]) if latest is not None else 0,
            },
            "blockers": blockers,
            "next_action": "context_understanding" if status == "full" else "continue_parent_capture",
            "raw_text_returned": False,
            "identifiers_returned": False,
            "url_output": "omitted",
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }
