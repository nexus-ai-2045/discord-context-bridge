"""親チャンネル配下の取得完全性を、正規化 SQLite 証拠から監査する。"""

from __future__ import annotations

import hashlib
import json
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
REQUIRED_INVENTORY_SCOPES = {"active", "archived_public", "archived_private"}


def _digest_ids(values: Sequence[str]) -> str:
    framed = "".join(f"{len(value)}:{value}" for value in sorted(set(values)))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


def _normalized_time(value: str) -> str:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("observed_at_timezone_required")
    return parsed.astimezone(timezone.utc).isoformat()


class CompletenessStore:
    """取得証拠を local SQLite に保持し、本文・IDなしの監査結果を返す。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                """
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
    ) -> None:
        normalized_ids = [str(value) for value in thread_ids]
        if not parent_target_key.strip() or not scan_id.strip():
            raise ValueError("inventory_binding_required")
        if not isinstance(pagination_exhausted, bool):
            raise ValueError("pagination_exhausted_boolean_required")
        if any(
            scope not in scopes or not isinstance(scopes[scope], bool)
            for scope in REQUIRED_INVENTORY_SCOPES
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
                    thread_set_digest, scopes_json, pagination_exhausted
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parent_target_key,
                    scan_id,
                    normalized_observed_at,
                    len(normalized_ids),
                    _digest_ids(normalized_ids),
                    json.dumps(dict(scopes), sort_keys=True),
                    int(pagination_exhausted),
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
        with self._connect() as connection:
            parent_exists = connection.execute(
                "SELECT 1 FROM parent_targets WHERE target_key = ?",
                (parent_target_key,),
            ).fetchone()
            if parent_exists is None:
                raise ValueError("parent_inventory_missing")
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
                    str(certificate.get("capture_id") or ""),
                    str(certificate.get("schema") or ""),
                    str(certificate.get("status") or "blocked"),
                    int(bool(certificate.get("full_capture_confirmed"))),
                    int(counts.get("messages") or 0),
                    int(counts.get("attachments_discovered") or 0),
                    int(counts.get("attachments_saved") or 0),
                    int(counts.get("attachments_manifested") or 0),
                    int(bool(certificate.get("attachments_consistent"))),
                    int(certificate.get("unresolved_gap_count") or 0),
                    int(certificate.get("pending_retry_count") or 0),
                    json.dumps(certificate.get("blockers") or [], ensure_ascii=False),
                ),
            )

    def audit_parent(self, parent_target_key: str) -> dict[str, Any]:
        blockers: list[str] = []
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
            stable = (
                len(scans) == 2
                and scans[0]["thread_set_digest"] == scans[1]["thread_set_digest"]
                and scans[0]["thread_count"] == scans[1]["thread_count"]
            )
            if len(scans) == 2 and not stable:
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
                "required_scopes_complete": all(
                    bool(scopes.get(scope)) for scope in REQUIRED_INVENTORY_SCOPES
                ),
            },
            "counts": {
                "inventory_threads": len(thread_ids),
                "child_certificates": len(certificates),
                "full_children": full_children,
                "pending_children": len(thread_ids) - full_children,
            },
            "blockers": blockers,
            "next_action": "context_understanding" if status == "full" else "continue_parent_capture",
            "raw_text_returned": False,
            "identifiers_returned": False,
            "url_output": "omitted",
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }
