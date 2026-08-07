"""Durable, fail-closed persistence for resumable capture runs."""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Any, Mapping


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_STATES = {
    "received",
    "route_preflight",
    "traversing_to_oldest",
    "traversing_to_latest",
    "attachment_inventory",
    "downloading_attachments",
    "reconciling",
    "stable_rescan",
    "gate_evaluating",
    "paused_auth",
    "paused_human_approval",
    "retry_wait",
    "full_closed",
    "blocked_closed",
}
_SAFE_TAGS = {
    "direct-message",
    "server-threads-all",
    "thread-only",
    "in-app-browser",
    "chrome-visible",
    "rest-backfill",
    "saved-artifacts",
    "desktop-accessibility",
    "refresh-check",
    "observed-full",
}
_SAFE_BLOCKERS = {
    None,
    "auth_required",
    "human_approval_required",
    "retryable_failure",
    "retry_budget_exhausted",
    "scan_pass_budget_exhausted",
}


class CaptureStoreError(RuntimeError):
    """Base error for capture persistence failures."""


class CheckpointCorruptError(CaptureStoreError):
    """Persisted state cannot be trusted or replayed."""


class SequenceConflictError(CaptureStoreError):
    """The caller's expected sequence does not match durable state."""


class EventConflictError(CaptureStoreError):
    """An event id was reused with different content."""


def _safe_capture_id(value: object) -> str:
    capture_id = str(value or "")
    if not _SAFE_ID.fullmatch(capture_id):
        raise ValueError("capture_id must be a safe opaque identifier")
    return capture_id


def _sequence_from_checkpoint(payload: Mapping[str, Any]) -> int:
    checkpoints = payload.get("checkpoints")
    if not isinstance(checkpoints, list):
        raise CheckpointCorruptError("checkpoint list is missing")
    if not checkpoints:
        return 0
    last = checkpoints[-1]
    if not isinstance(last, Mapping):
        raise CheckpointCorruptError("checkpoint entry is invalid")
    sequence = last.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise CheckpointCorruptError("checkpoint sequence is invalid")
    expected = list(range(1, sequence + 1))
    observed = [
        item.get("sequence") if isinstance(item, Mapping) else None
        for item in checkpoints
    ]
    if observed != expected:
        raise CheckpointCorruptError("checkpoint sequence is not contiguous")
    return sequence


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class CaptureCheckpointStore:
    """File-backed checkpoint and event ledger scoped by capture id."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def checkpoint_path(self, capture_id: str) -> Path:
        return self.root / "checkpoints" / f"{_safe_capture_id(capture_id)}.json"

    def ledger_path(self, capture_id: str) -> Path:
        return self.root / "events" / f"{_safe_capture_id(capture_id)}.ndjson"

    def coverage_path(self, capture_id: str) -> Path:
        return self.root / "coverage" / f"{_safe_capture_id(capture_id)}.json"

    def message_ledger_path(self, capture_id: str) -> Path:
        return self.root / "message-ledgers" / f"{_safe_capture_id(capture_id)}.json"

    def full_capture_receipt_path(self, capture_id: str) -> Path:
        return self.root / "receipts" / "full-capture" / f"{_safe_capture_id(capture_id)}.json"

    def browser_route_receipt_path(self, capture_id: str) -> Path:
        return self.root / "receipts" / "browser-route" / f"{_safe_capture_id(capture_id)}.json"

    def learning_handoff_receipt_path(self, capture_id: str) -> Path:
        return self.root / "receipts" / "learning-handoff" / f"{_safe_capture_id(capture_id)}.json"

    def _load_receipt(self, path: Path, *, capture_id: str, schema: str) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointCorruptError("receipt is unreadable") from error
        if not isinstance(payload, dict):
            raise CheckpointCorruptError("receipt root is invalid")
        if payload.get("schema") != schema or payload.get("capture_id") != capture_id:
            raise CheckpointCorruptError("receipt binding is invalid")
        if payload.get("schema_version") != "1.0":
            raise CheckpointCorruptError("receipt schema version is invalid")
        recorded_at = payload.get("recorded_at")
        try:
            parsed_recorded_at = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
        except ValueError as error:
            raise CheckpointCorruptError("receipt recorded_at is invalid") from error
        if parsed_recorded_at.tzinfo is None or payload.get("recorded_by") != "discord-context-bridge":
            raise CheckpointCorruptError("receipt provenance is invalid")
        if payload.get("raw_text_returned") is not False:
            raise CheckpointCorruptError("receipt exposes raw text")
        if payload.get("outbound_actions") != "disabled":
            raise CheckpointCorruptError("receipt enables outbound actions")
        return payload

    def load_full_capture_receipt(self, capture_id: str, *, consumer: str) -> dict[str, Any] | None:
        payload = self._load_receipt(
            self.full_capture_receipt_path(capture_id),
            capture_id=capture_id,
            schema="dcb-strict-full-capture-receipt.v1",
        )
        if payload is not None and payload.get("consumer_binding") != consumer:
            raise CheckpointCorruptError("full capture receipt consumer binding is invalid")
        if payload is not None:
            from discord_context_bridge.acquisition_gate import validate_full_capture_receipt

            if not validate_full_capture_receipt(payload)["valid"]:
                raise CheckpointCorruptError("full capture receipt evidence is invalid")
        return payload

    def load_browser_route_receipt(self, capture_id: str) -> dict[str, Any] | None:
        payload = self._load_receipt(
            self.browser_route_receipt_path(capture_id),
            capture_id=capture_id,
            schema="dcb-browser-route-observation-receipt.v1",
        )
        if payload is None:
            return None
        observations = payload.get("observations")
        if not isinstance(observations, list) or not observations or len(observations) > 256:
            raise CheckpointCorruptError("browser receipt observations are invalid")
        if [item.get("sequence") for item in observations if isinstance(item, dict)] != list(
            range(1, len(observations) + 1)
        ):
            raise CheckpointCorruptError("browser receipt sequence is not contiguous")
        if any(not isinstance(item, dict) or not item.get("route") for item in observations):
            raise CheckpointCorruptError("browser observation route binding is invalid")
        allowed_routes = {"chrome_extension", "in_app_browser", "desktop_accessibility", "unknown"}
        allowed_states = {
            "connected", "tab_inventory_ok", "claim_ok", "ready", "blocked_extension_ui",
            "extension_unavailable", "auth_required", "external_mutation_stop", "unknown",
        }
        allowed_errors = {
            "none", "popup_open", "tab_inventory_failed", "claim_failed",
            "navigation_failed", "unknown",
        }
        if any(
            item.get("route") not in allowed_routes
            or item.get("state") not in allowed_states
            or item.get("error_code") not in allowed_errors
            or not isinstance(item.get("observed_at"), str)
            for item in observations
        ):
            raise CheckpointCorruptError("browser observation value is invalid")
        for item in observations:
            try:
                observed_at = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
            except ValueError as error:
                raise CheckpointCorruptError("browser observation time is invalid") from error
            if observed_at.tzinfo is None:
                raise CheckpointCorruptError("browser observation time is invalid")
        latest = observations[-1]
        if payload.get("route") != latest.get("route") or payload.get("latest_state") != latest.get("state"):
            raise CheckpointCorruptError("browser receipt projection is inconsistent")
        return payload

    def load_learning_handoff_receipt(self, capture_id: str) -> dict[str, Any] | None:
        payload = self._load_receipt(
            self.learning_handoff_receipt_path(capture_id),
            capture_id=capture_id,
            schema="dcb-learning-handoff-receipt.v1",
        )
        if payload is None:
            return None
        status = payload.get("status")
        if status not in {"completed", "held"}:
            raise CheckpointCorruptError("learning handoff status is invalid")
        if (status == "completed") != (payload.get("completion_confirmed") is True):
            raise CheckpointCorruptError("learning handoff completion evidence is invalid")
        digest = str(payload.get("closeout_correlation_digest") or "")
        pointer_digest = str(payload.get("evidence_pointer_digest") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CheckpointCorruptError("learning handoff correlation is invalid")
        if status == "completed" and not re.fullmatch(r"[0-9a-f]{64}", pointer_digest):
            raise CheckpointCorruptError("learning handoff pointer evidence is invalid")
        if payload.get("adapter") != "absorbed-dialogue-router":
            raise CheckpointCorruptError("learning handoff adapter is invalid")
        return payload

    def save_receipt(self, path: Path, receipt: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(receipt)
        _atomic_json(path, payload)
        return payload

    @contextmanager
    def transition_lock(self, capture_id: str):
        """Use a crash-released, non-blocking OS lock for one capture."""

        safe_id = _safe_capture_id(capture_id)
        path = self.root / "locks" / f"{safe_id}.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise SequenceConflictError(
                    "capture transition is already locked"
                ) from error
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()

    def load_checkpoint(self, capture_id: str) -> dict[str, Any] | None:
        path = self.checkpoint_path(capture_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointCorruptError("checkpoint is unreadable") from error
        if not isinstance(payload, dict):
            raise CheckpointCorruptError("checkpoint root is invalid")
        if payload.get("capture_id") != capture_id:
            raise CheckpointCorruptError("checkpoint capture binding is invalid")
        if payload.get("schema") != "dcb-full-capture-orchestrator.v1":
            raise CheckpointCorruptError("checkpoint schema is invalid")
        if payload.get("state") not in _SAFE_STATES:
            raise CheckpointCorruptError("checkpoint state is invalid")
        if payload.get("blocker") not in _SAFE_BLOCKERS:
            raise CheckpointCorruptError("checkpoint blocker is invalid")
        tags = payload.get("operational_tags", [])
        if (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) or tag not in _SAFE_TAGS for tag in tags)
            or len(tags) != len(set(tags))
        ):
            raise CheckpointCorruptError("checkpoint operational tags are invalid")
        _sequence_from_checkpoint(payload)
        return payload

    def save_checkpoint(
        self, run: Mapping[str, Any], *, expected_sequence: int
    ) -> dict[str, Any]:
        capture_id = _safe_capture_id(run.get("capture_id"))
        current = self.load_checkpoint(capture_id)
        current_sequence = _sequence_from_checkpoint(current) if current else 0
        if current_sequence != expected_sequence:
            raise SequenceConflictError(
                f"checkpoint sequence conflict: expected {expected_sequence}, "
                f"found {current_sequence}"
            )
        payload = dict(run)
        next_sequence = _sequence_from_checkpoint(payload)
        if next_sequence < current_sequence:
            raise SequenceConflictError("checkpoint sequence cannot move backwards")
        _atomic_json(self.checkpoint_path(capture_id), payload)
        return payload

    def load_coverage(self, capture_id: str) -> dict[str, Any] | None:
        path = self.coverage_path(capture_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointCorruptError("coverage checkpoint is unreadable") from error
        if not isinstance(payload, dict):
            raise CheckpointCorruptError("coverage checkpoint root is invalid")
        if payload.get("schema") != "dcb-virtual-scroll-coverage.v1":
            raise CheckpointCorruptError("coverage checkpoint schema is invalid")
        if payload.get("capture_id") != capture_id:
            raise CheckpointCorruptError("coverage checkpoint binding is invalid")
        windows = payload.get("windows")
        messages = payload.get("messages")
        if not isinstance(windows, list) or not isinstance(messages, dict):
            raise CheckpointCorruptError("coverage checkpoint structure is invalid")
        if payload.get("raw_text_returned") is not False:
            raise CheckpointCorruptError("coverage checkpoint exposes raw text")
        if payload.get("outbound_actions") != "disabled":
            raise CheckpointCorruptError("coverage checkpoint enables outbound actions")
        return payload

    def save_coverage(
        self,
        coverage: Mapping[str, Any],
        *,
        expected_window_count: int,
    ) -> dict[str, Any]:
        capture_id = _safe_capture_id(coverage.get("capture_id"))
        current = self.load_coverage(capture_id)
        current_count = len(current.get("windows", [])) if current else 0
        if current_count != expected_window_count:
            raise SequenceConflictError(
                "coverage window count conflict: "
                f"expected {expected_window_count}, found {current_count}"
            )
        payload = dict(coverage)
        next_count = len(payload.get("windows", []))
        if next_count < current_count:
            raise SequenceConflictError("coverage window count cannot move backwards")
        _atomic_json(self.coverage_path(capture_id), payload)
        return payload

    def load_message_ledger(self, capture_id: str) -> dict[str, Any] | None:
        path = self.message_ledger_path(capture_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointCorruptError("message ledger is unreadable") from error
        if not isinstance(payload, dict):
            raise CheckpointCorruptError("message ledger root is invalid")
        if payload.get("schema") != "dcb-private-message-event-ledger.v1":
            raise CheckpointCorruptError("message ledger schema is invalid")
        if payload.get("capture_id") != capture_id:
            raise CheckpointCorruptError("message ledger capture binding is invalid")
        events = payload.get("events")
        if not isinstance(events, list):
            raise CheckpointCorruptError("message ledger events are invalid")
        if [event.get("sequence") for event in events if isinstance(event, dict)] != list(
            range(1, len(events) + 1)
        ):
            raise CheckpointCorruptError("message ledger sequence is not contiguous")
        if payload.get("outbound_actions") != "disabled":
            raise CheckpointCorruptError("message ledger enables outbound actions")
        return payload

    def save_message_ledger(
        self,
        ledger: Mapping[str, Any],
        *,
        expected_sequence: int,
    ) -> dict[str, Any]:
        capture_id = _safe_capture_id(ledger.get("capture_id"))
        current = self.load_message_ledger(capture_id)
        current_sequence = len(current.get("events", [])) if current else 0
        if current_sequence != expected_sequence:
            raise SequenceConflictError(
                "message ledger sequence conflict: "
                f"expected {expected_sequence}, found {current_sequence}"
            )
        payload = dict(ledger)
        next_sequence = len(payload.get("events", []))
        if next_sequence < current_sequence:
            raise SequenceConflictError("message ledger cannot move backwards")
        _atomic_json(self.message_ledger_path(capture_id), payload)
        return payload

    def load_events(self, capture_id: str) -> list[dict[str, Any]]:
        path = self.ledger_path(capture_id)
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise CheckpointCorruptError("event ledger is unreadable") from error
        for index, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise CheckpointCorruptError(
                    "event ledger contains invalid JSON"
                ) from error
            if not isinstance(event, dict):
                raise CheckpointCorruptError("event ledger entry is invalid")
            if event.get("capture_id") != capture_id:
                raise CheckpointCorruptError("event ledger capture binding is invalid")
            event_id = str(event.get("event_id") or "")
            sequence = event.get("sequence")
            if not _SAFE_ID.fullmatch(event_id):
                raise CheckpointCorruptError("event id is invalid")
            if (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence != index
            ):
                raise CheckpointCorruptError("event ledger sequence is not contiguous")
            if event_id in seen:
                raise CheckpointCorruptError(
                    "event ledger contains duplicate event ids"
                )
            seen[event_id] = event
            events.append(event)
        return events

    def append_event(
        self, event: Mapping[str, Any], *, expected_sequence: int
    ) -> dict[str, Any]:
        capture_id = _safe_capture_id(event.get("capture_id"))
        event_id = _safe_capture_id(event.get("event_id"))
        payload = dict(event)
        events = self.load_events(capture_id)
        for existing in events:
            if existing["event_id"] != event_id:
                continue
            if existing != payload:
                raise EventConflictError("event id is already bound to other content")
            return {
                "capture_id": capture_id,
                "event_id": event_id,
                "sequence": existing["sequence"],
                "appended": False,
                "duplicate": True,
            }
        current_sequence = len(events)
        if current_sequence != expected_sequence:
            raise SequenceConflictError(
                f"event sequence conflict: expected {expected_sequence}, "
                f"found {current_sequence}"
            )
        if payload.get("sequence") != current_sequence + 1:
            raise SequenceConflictError(
                "event sequence is not the next durable sequence"
            )
        path = self.ledger_path(capture_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return {
            "capture_id": capture_id,
            "event_id": event_id,
            "sequence": payload["sequence"],
            "appended": True,
            "duplicate": False,
        }
