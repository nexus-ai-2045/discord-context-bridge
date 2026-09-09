"""親チャンネル配下の取得完全性を、正規化 SQLite 証拠から監査する。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALGORITHM_IDS = [
    "pagination_exhaustion",
    "stable_rescan",
    "set_reconciliation",
    "strict_child_full_capture",
    "attachment_manifest_reconciliation",
    "pending_work_zero",
]
LEGACY_INVENTORY_SCOPES = {"active", "archived_public", "archived_private"}
REQUIRED_SCOPES_BY_PARENT_KIND = {
    "announcement": ("active_filtered", "archived_public"),
    "forum": ("active_filtered", "archived_public"),
    "media": ("active_filtered", "archived_public"),
    "text": ("active_filtered", "archived_public", "archived_private"),
}
SCOPE_ROUTES = {
    "active_filtered": "GET /guilds/{guild_id}/threads/active",
    "archived_public": "GET /channels/{channel_id}/threads/archived/public",
    "archived_private": "GET /channels/{channel_id}/threads/archived/private",
}


def _digest_ids(values: Sequence[str]) -> str:
    framed = "".join(f"{len(value)}:{value}" for value in sorted(set(values)))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _normalized_time(value: str) -> str:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("observed_at_timezone_required")
    return parsed.astimezone(UTC).isoformat()


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field}_boolean_required")
    return value


def _stored_scope_receipts(value: object, *, parent_kind: str) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, str) or not value:
        return {}, False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}, False
    if not isinstance(parsed, dict) or any(
        not isinstance(receipt, Mapping) for receipt in parsed.values()
    ):
        return {}, False
    required = REQUIRED_SCOPES_BY_PARENT_KIND.get(parent_kind)
    if required is None or any(scope not in required for scope in parsed):
        return {}, False
    for scope, receipt in parsed.items():
        if not _normalized_scope_receipt_valid(scope, receipt):
            return {}, False
    return parsed, True


def _normalized_scope_receipt_valid(scope: str, receipt: Mapping[str, Any]) -> bool:
    """JSON構造だけでなく、保存時と同じ正規化fieldの意味を再検証する。"""
    fields = {
        "route", "parent_bound", "active_parent_filter_applied", "page_count",
        "terminal_reached", "terminal_cursor_digest", "thread_count", "thread_set_digest",
        "locked_count", "authorization_confirmed", "authorization_capability",
    }
    if not fields.issubset(receipt):
        return False
    if receipt["route"] != SCOPE_ROUTES.get(scope) or receipt["parent_bound"] is not True:
        return False
    if not isinstance(receipt["terminal_reached"], bool):
        return False
    filtered = receipt["active_parent_filter_applied"]
    if not isinstance(filtered, bool) or (scope == "active_filtered" and not filtered):
        return False
    for field, minimum in (("page_count", 1), ("thread_count", 0), ("locked_count", 0)):
        value = receipt[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            return False
    if receipt["locked_count"] > receipt["thread_count"]:
        return False
    for field in ("terminal_cursor_digest", "thread_set_digest"):
        value = receipt[field]
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            return False
    if receipt["thread_count"] == 0 and receipt["thread_set_digest"] != _digest_ids([]):
        return False
    authorization = receipt["authorization_confirmed"]
    if scope == "archived_private":
        if authorization is not None and not isinstance(authorization, bool):
            return False
    elif authorization is not None:
        return False
    return receipt["authorization_capability"] == ("manage_threads" if authorization is True else None)


def _normalize_scope_receipts(
    parent_target_key: str,
    parent_kind: str,
    receipts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    required = REQUIRED_SCOPES_BY_PARENT_KIND.get(parent_kind)
    if required is None:
        raise ValueError("parent_kind_invalid")
    if any(scope not in required for scope in receipts):
        raise ValueError("scope_receipt_not_applicable")
    normalized: dict[str, dict[str, Any]] = {}
    all_thread_ids: list[str] = []
    for scope, receipt in receipts.items():
        if not isinstance(receipt, Mapping):
            raise ValueError("scope_receipt_object_required")
        if receipt.get("route") != SCOPE_ROUTES[scope]:
            raise ValueError("scope_receipt_route_mismatch")
        if receipt.get("parent_target_key") != parent_target_key:
            raise ValueError("scope_receipt_parent_binding_mismatch")
        filtered = receipt.get("active_parent_filter_applied")
        if not isinstance(filtered, bool):
            raise ValueError("scope_receipt_parent_filter_boolean_required")
        if scope == "active_filtered" and not filtered:
            raise ValueError("scope_receipt_active_parent_filter_required")
        page_count = receipt.get("page_count")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
            raise ValueError("scope_receipt_page_count_invalid")
        terminal_reached = receipt.get("terminal_reached")
        if not isinstance(terminal_reached, bool) or "terminal_cursor" not in receipt:
            raise ValueError("scope_receipt_terminal_evidence_required")
        terminal_cursor = receipt["terminal_cursor"]
        if terminal_cursor is not None and not isinstance(terminal_cursor, str):
            raise ValueError("scope_receipt_terminal_cursor_invalid")
        raw_thread_ids = receipt.get("thread_ids")
        if not isinstance(raw_thread_ids, list) or any(
            not isinstance(value, str) or not value.strip() for value in raw_thread_ids
        ):
            raise ValueError("scope_receipt_thread_ids_invalid")
        thread_ids = list(raw_thread_ids)
        if len(thread_ids) != len(set(thread_ids)):
            raise ValueError("duplicate_thread_id")
        locked_count = receipt.get("locked_count")
        if (
            not isinstance(locked_count, int)
            or isinstance(locked_count, bool)
            or locked_count < 0
            or locked_count > len(thread_ids)
        ):
            raise ValueError("scope_receipt_locked_count_invalid")
        authorization_confirmed = None
        if scope == "archived_private":
            authorization = receipt.get("authorization")
            if isinstance(authorization, Mapping):
                authorization_confirmed = (
                    authorization.get("capability") == "manage_threads"
                    and authorization.get("confirmed") is True
                )
        normalized[scope] = {
            "route": SCOPE_ROUTES[scope],
            "parent_bound": True,
            "active_parent_filter_applied": filtered,
            "page_count": page_count,
            "terminal_reached": terminal_reached,
            "terminal_cursor_digest": hashlib.sha256(
                (terminal_cursor if terminal_cursor is not None else "<none>").encode(
                    "utf-8"
                )
            ).hexdigest(),
            "thread_count": len(thread_ids),
            "thread_set_digest": _digest_ids(thread_ids),
            "locked_count": locked_count,
            "authorization_confirmed": authorization_confirmed,
            "authorization_capability": (
                "manage_threads" if authorization_confirmed is True else None
            ),
        }
        if not _normalized_scope_receipt_valid(scope, normalized[scope]):
            raise ValueError("normalized_scope_receipt_invalid")
        all_thread_ids.extend(thread_ids)
    if len(all_thread_ids) != len(set(all_thread_ids)):
        raise ValueError("thread_scope_overlap")
    return normalized, all_thread_ids


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
                    scopes_json TEXT NOT NULL,
                    pagination_exhausted INTEGER NOT NULL CHECK(pagination_exhausted IN (0, 1)),
                    parent_kind TEXT,
                    scope_receipts_json TEXT,
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
            if "parent_kind" not in columns:
                connection.execute("ALTER TABLE inventory_scans ADD COLUMN parent_kind TEXT")
            if "scope_receipts_json" not in columns:
                connection.execute(
                    "ALTER TABLE inventory_scans ADD COLUMN scope_receipts_json TEXT"
                )

    def record_inventory_scan(
        self,
        *,
        parent_target_key: str,
        scan_id: str,
        observed_at: str,
        thread_ids: Sequence[str] | None = None,
        scopes: Mapping[str, bool] | None = None,
        pagination_exhausted: bool | None = None,
        parent_kind: str | None = None,
        scope_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if not parent_target_key.strip() or not scan_id.strip():
            raise ValueError("inventory_binding_required")
        normalized_receipts: dict[str, dict[str, Any]] | None = None
        if scope_receipts is not None or parent_kind is not None:
            if not isinstance(scope_receipts, Mapping) or parent_kind is None:
                raise ValueError("canonical_inventory_evidence_required")
            normalized_receipts, normalized_ids = _normalize_scope_receipts(
                parent_target_key, parent_kind, scope_receipts
            )
            scopes = {}
            pagination_exhausted = False
        else:
            normalized_ids = [str(value) for value in (thread_ids or [])]
            if not isinstance(pagination_exhausted, bool):
                raise ValueError("pagination_exhausted_boolean_required")
            if not isinstance(scopes, Mapping) or any(
                scope not in scopes or not isinstance(scopes[scope], bool)
                for scope in LEGACY_INVENTORY_SCOPES
            ):
                raise ValueError("inventory_scope_boolean_required")
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("duplicate_thread_id")
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
                    thread_set_digest, scopes_json, pagination_exhausted,
                    parent_kind, scope_receipts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_target_key,
                    scan_id,
                    normalized_observed_at,
                    len(normalized_ids),
                    _digest_ids(normalized_ids),
                    json.dumps(dict(scopes), sort_keys=True),
                    int(pagination_exhausted),
                    parent_kind,
                    (
                        json.dumps(normalized_receipts, sort_keys=True)
                        if normalized_receipts is not None
                        else None
                    ),
                ),
            )
            scan_row_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO inventory_threads(inventory_scan_id, thread_id) VALUES (?, ?)",
                [(scan_row_id, thread_id) for thread_id in normalized_ids],
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
        now = datetime.now(UTC).isoformat()
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
        latest_receipts: dict[str, Any] = {}
        required_scopes: tuple[str, ...] = ()
        evidence_model = "missing"
        locked_count = 0
        latest_complete = False
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

            verified_receipts: dict[int, tuple[dict[str, Any], bool]] = {}
            for scan in scans:
                receipts, valid = _stored_scope_receipts(
                    scan["scope_receipts_json"], parent_kind=scan["parent_kind"]
                )
                actual_ids = [
                    row["thread_id"] for row in connection.execute(
                        "SELECT thread_id FROM inventory_threads WHERE inventory_scan_id = ?",
                        (scan["id"],),
                    )
                ]
                reconciled = (
                    len(actual_ids) == scan["thread_count"]
                    and _digest_ids(actual_ids) == scan["thread_set_digest"]
                    and valid
                    and sum(receipt["thread_count"] for receipt in receipts.values())
                    == len(actual_ids)
                )
                if not reconciled:
                    blockers.append("inventory_saved_evidence_mismatch")
                verified_receipts[scan["id"]] = (receipts, valid and reconciled)

            if len(scans) == 2:
                parsed_complete = []
                scan_receipts_by_position: list[dict[str, Any]] = []
                for scan in scans:
                    scan_kind = scan["parent_kind"]
                    scan_receipts, receipts_valid = verified_receipts[scan["id"]]
                    scan_receipts_by_position.append(scan_receipts)
                    scan_required = REQUIRED_SCOPES_BY_PARENT_KIND.get(scan_kind, ())
                    complete = receipts_valid and bool(scan_required) and all(
                        scope in scan_receipts
                        and scan_receipts[scope].get("terminal_reached") is True
                        for scope in scan_required
                    )
                    if scan_kind == "text" and complete:
                        complete = (
                            scan_receipts["archived_private"].get("authorization_confirmed")
                            is True
                        )
                    parsed_complete.append(complete)
                both_complete = all(parsed_complete)
                if not both_complete:
                    blockers.append("inventory_rescan_incomplete")
                digest_match = (
                    scans[0]["thread_set_digest"] == scans[1]["thread_set_digest"]
                    and scans[0]["thread_count"] == scans[1]["thread_count"]
                    and scans[0]["parent_kind"] == scans[1]["parent_kind"]
                    and bool(REQUIRED_SCOPES_BY_PARENT_KIND.get(scans[0]["parent_kind"]))
                    and all(
                        scan_receipts_by_position[0].get(scope, {}).get(
                            "thread_set_digest"
                        )
                        == scan_receipts_by_position[1].get(scope, {}).get(
                            "thread_set_digest"
                        )
                        and scan_receipts_by_position[0].get(scope, {}).get(
                            "thread_count"
                        )
                        == scan_receipts_by_position[1].get(scope, {}).get(
                            "thread_count"
                        )
                        for scope in REQUIRED_SCOPES_BY_PARENT_KIND.get(
                            scans[0]["parent_kind"], ()
                        )
                    )
                )
                stable = both_complete and digest_match
                if both_complete and not digest_match:
                    blockers.append("inventory_rescan_not_stable")

            latest = scans[0] if scans else None
            thread_ids: set[str] = set()
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
                if latest["scope_receipts_json"]:
                    evidence_model = "scope_receipts_v1"
                    latest_receipts, receipts_valid = verified_receipts[latest["id"]]
                    if not receipts_valid:
                        blockers.append("inventory_scope_receipts_invalid")
                    required_scopes = REQUIRED_SCOPES_BY_PARENT_KIND.get(
                        latest["parent_kind"], ()
                    )
                    pagination_exhausted = bool(required_scopes) and all(
                        latest_receipts.get(scope, {}).get("terminal_reached") is True
                        for scope in required_scopes
                    )
                    latest_complete = receipts_valid and pagination_exhausted and all(
                        scope in latest_receipts for scope in required_scopes
                    )
                    locked_count = sum(
                        int(latest_receipts.get(scope, {}).get("locked_count") or 0)
                        for scope in required_scopes
                    )
                    if not all(scope in latest_receipts for scope in required_scopes):
                        blockers.append("inventory_scope_incomplete")
                    if (
                        latest["parent_kind"] == "text"
                        and latest_receipts.get("archived_private", {}).get(
                            "authorization_confirmed"
                        )
                        is not True
                    ):
                        blockers.append("private_archive_authorization_missing")
                        latest_complete = False
                else:
                    evidence_model = "legacy_aggregate"
                    blockers.append("inventory_scope_receipts_missing")
                if not pagination_exhausted:
                    blockers.append("inventory_pagination_not_exhausted")

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
                "stable_scan_count": 2 if stable else len(scans),
                "pagination_exhausted": pagination_exhausted,
                "required_scopes": list(required_scopes),
                "required_scopes_complete": latest_complete,
                "both_latest_scans_complete": both_complete if len(scans) == 2 else False,
                "evidence_model": evidence_model,
            },
            "counts": {
                "inventory_threads": len(thread_ids),
                "child_certificates": len(certificates),
                "full_children": full_children,
                "pending_children": len(thread_ids) - full_children,
                "retired_certificates": retired_count,
                "locked_threads": locked_count,
            },
            "blockers": blockers,
            "next_action": "context_understanding" if status == "full" else "continue_parent_capture",
            "raw_text_returned": False,
            "identifiers_returned": False,
            "url_output": "omitted",
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }
