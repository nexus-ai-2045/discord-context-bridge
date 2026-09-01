"""Metadata-only adapter for legacy parallel capture run directories.

This module does not define a second full-capture authority.  It projects a
legacy batch layout toward the canonical DCB parent-completeness certificate
and refuses to treat free-floating marker files as evidence.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from errno import EINVAL, ENOTSUP
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..completeness_store import CompletenessStore

_WORKER_SHARD = re.compile(r"worker-(\d+)\.json\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL_COMMIT_STATES = {"committed", "recorded_without_text"}
_TERMINAL_READY_STATES = {"ready", "no_message_list", "nav_thread_missing"}
_MAX_JSON_BYTES = 5_000_000
_MAX_LEDGER_BYTES = 20_000_000
_STOP_REASONS = {"completed", "producer_failed", "operator_cancelled", "superseded"}
_RUN_DEFINITION_KEYS = {
    "schema",
    "started_at",
    "browser_mode",
    "server_scope",
    "canonical_count",
    "shard_counts",
    "parent_target_key_sha256",
    "outbound_actions",
}


def _load_json(path: Path) -> object | None:
    try:
        if path.stat().st_size > _MAX_JSON_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _base_result(*, finalize: bool) -> dict[str, Any]:
    return {
        "schema": "dcb.parallel-run-operational-closeout.v1",
        "status": "partial",
        "terminal_state": "running",
        "full_capture_confirmed": False,
        "persistence_confirmed": False,
        "counts": {
            "expected_items": 0,
            "canonical_ready_items": 0,
            "canonical_receipts": 0,
            "canonical_ledger_bindings": 0,
            "canonical_artifact_hash_bindings": 0,
            "terminal_without_text_items": 0,
            "commit_ledger_rows": 0,
            "missing_ready_items": 0,
            "missing_receipts": 0,
            "missing_ledger_bindings": 0,
            "missing_artifact_hash_bindings": 0,
        },
        "blockers": [],
        "raw_text_returned": False,
        "participant_names_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def _canonical_bindings(
    run_dir: Path, shard_counts: list[int]
) -> tuple[set[tuple[int, int]], list[str]]:
    bindings: set[tuple[int, int]] = set()
    blockers: list[str] = []
    shard_dir = run_dir / "shards"
    try:
        paths = sorted(shard_dir.glob("worker-*.json"))
    except OSError:
        paths = []
    if not paths:
        return bindings, ["canonical_shards_missing"]
    expected_names = {f"worker-{index}.json" for index in range(len(shard_counts))}
    actual_names = {path.name for path in paths}
    if actual_names != expected_names:
        blockers.append("canonical_shard_topology_mismatch")
    for path in paths:
        match = _WORKER_SHARD.fullmatch(path.name)
        payload = _load_json(path)
        if match is None or not isinstance(payload, list):
            blockers.append("canonical_shard_invalid")
            continue
        worker = int(match.group(1))
        if worker >= len(shard_counts) or len(payload) != shard_counts[worker]:
            blockers.append("canonical_shard_item_count_mismatch")
        for index, item in enumerate(payload):
            if not isinstance(item, Mapping):
                blockers.append("canonical_shard_item_invalid")
                continue
            binding = (worker, index)
            if binding in bindings:
                blockers.append("canonical_shard_binding_duplicate")
            bindings.add(binding)
    return bindings, blockers


def _ready_path(run_dir: Path, worker: int, index: int) -> Path:
    return run_dir / "spool" / f"worker-{worker}" / f"{index:04d}.ready.json"


def _receipt_path(run_dir: Path, worker: int, index: int) -> Path:
    return run_dir / "committed" / f"worker-{worker}-item-{index:04d}" / "receipt.json"


def _text_path(run_dir: Path, worker: int, index: int) -> Path:
    return run_dir / "spool" / f"worker-{worker}" / f"{index:04d}.txt"


def _sha256_file(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_LEDGER_BYTES:
            return None
        digest = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _fsync_directory(path: Path) -> None:
    """Durably publish a rename where directory fsync is supported."""

    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError as exc:
        if exc.errno in {EINVAL, ENOTSUP}:
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in {EINVAL, ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def _mapping_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _publish_create_only_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish complete JSON atomically without permitting replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temp_path, path)
        except FileExistsError:
            raise ValueError("immutable receipt already exists") from None
        _fsync_directory(path.parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _run_definition_sha256(metadata: Mapping[str, Any]) -> str:
    return _mapping_sha256(
        {key: metadata[key] for key in sorted(_RUN_DEFINITION_KEYS) if key in metadata}
    )


def _evidence_snapshot_sha256(run_dir: Path) -> str | None:
    candidates: set[Path] = set()
    patterns = (
        "shards/worker-*.json",
        "spool/**/*.ready.json",
        "spool/**/*.txt",
        "committed/**/receipt.json",
        "committed/commit-ledger.ndjson",
        "orchestration/terminal/*.json",
    )
    try:
        for pattern in patterns:
            candidates.update(path for path in run_dir.glob(pattern) if path.is_file())
    except OSError:
        return None
    digest = sha256(b"dcb.parallel-evidence-snapshot.v1\0")
    for path in sorted(candidates, key=lambda item: item.as_posix()):
        file_digest = _sha256_file(path)
        if file_digest is None:
            return None
        relative = path.relative_to(run_dir).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_digest))
    return digest.hexdigest()


def _expected_worker_names(metadata: Mapping[str, Any]) -> set[str]:
    shard_counts = metadata.get("shard_counts")
    if not (
        isinstance(shard_counts, list)
        and shard_counts
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in shard_counts
        )
    ):
        return set()
    return {f"worker-{index}" for index in range(len(shard_counts))}


def build_parallel_producer_drain_receipt(
    run_dir: Path | str, *, producer: str, event_id: str
) -> dict[str, Any]:
    """Build one producer-owned drain event for create-only persistence."""

    root = Path(run_dir)
    metadata = _load_json(root / "run-metadata.json")
    allowed = (
        _expected_worker_names(metadata) | {"importer"}
        if isinstance(metadata, Mapping)
        else set()
    )
    if not (
        isinstance(metadata, Mapping)
        and metadata.get("schema") == "dcb.parallel-run.v1"
        and producer in allowed
        and isinstance(event_id, str)
        and event_id
    ):
        raise ValueError("valid producer drain evidence required")
    return {
        "schema": "dcb.parallel-producer-drain-receipt.v1",
        "event_type": "producer.drained",
        "event_id": event_id,
        "producer": producer,
        "run_definition_sha256": _run_definition_sha256(metadata),
        "emitted_by": producer,
        "outbound_actions": "disabled",
    }


def persist_parallel_producer_drain_receipt(
    run_dir: Path | str, *, producer: str, event_id: str
) -> dict[str, Any]:
    """Persist one producer terminal event exactly once."""

    root = Path(run_dir)
    receipt = build_parallel_producer_drain_receipt(
        root, producer=producer, event_id=event_id
    )
    _publish_create_only_json(
        root / "orchestration" / "terminal" / f"{producer}.json", receipt
    )
    return receipt


def _producer_drains_valid(run_dir: Path, metadata: Mapping[str, Any]) -> bool:
    expected = _expected_worker_names(metadata) | {"importer"}
    if len(expected) < 2:
        return False
    run_digest = _run_definition_sha256(metadata)
    for producer in expected:
        receipt = _load_json(
            run_dir / "orchestration" / "terminal" / f"{producer}.json"
        )
        if not (
            isinstance(receipt, Mapping)
            and receipt.get("schema") == "dcb.parallel-producer-drain-receipt.v1"
            and receipt.get("event_type") == "producer.drained"
            and isinstance(receipt.get("event_id"), str)
            and receipt.get("event_id")
            and receipt.get("producer") == producer
            and receipt.get("run_definition_sha256") == run_digest
            and receipt.get("emitted_by") == producer
            and receipt.get("outbound_actions") == "disabled"
        ):
            return False
    return True


def _producer_drains_sha256(run_dir: Path, metadata: Mapping[str, Any]) -> str | None:
    if not _producer_drains_valid(run_dir, metadata):
        return None
    receipts: dict[str, Mapping[str, Any]] = {}
    for producer in sorted(_expected_worker_names(metadata) | {"importer"}):
        receipt = _load_json(
            run_dir / "orchestration" / "terminal" / f"{producer}.json"
        )
        if not isinstance(receipt, Mapping):
            return None
        receipts[producer] = receipt
    return _mapping_sha256(receipts)


def build_parallel_run_stop_receipt(
    run_dir: Path | str, *, event_id: str, stopped_reason: str
) -> dict[str, Any]:
    """Build the producer-owned event payload; the producer must persist it create-only."""

    root = Path(run_dir)
    metadata = _load_json(root / "run-metadata.json")
    evidence_digest = _evidence_snapshot_sha256(root)
    drain_digest = (
        _producer_drains_sha256(root, metadata)
        if isinstance(metadata, Mapping)
        else None
    )
    if not (
        isinstance(metadata, Mapping)
        and metadata.get("schema") == "dcb.parallel-run.v1"
        and isinstance(event_id, str)
        and event_id
        and stopped_reason in _STOP_REASONS
        and evidence_digest is not None
        and drain_digest is not None
    ):
        raise ValueError("valid terminal producer evidence required")
    return {
        "schema": "dcb.parallel-run-stop-receipt.v1",
        "event_type": "producer.quiesced",
        "event_id": event_id,
        "stopped_reason": stopped_reason,
        "run_definition_sha256": _run_definition_sha256(metadata),
        "evidence_snapshot_sha256": evidence_digest,
        "producer_drain_receipts_sha256": drain_digest,
        "workers_drained": True,
        "importer_drained": True,
        "emitted_by": "discord-capture-event-router",
        "outbound_actions": "disabled",
    }


def persist_parallel_run_stop_receipt(
    run_dir: Path | str, *, event_id: str, stopped_reason: str
) -> dict[str, Any]:
    """Aggregate immutable producer drains into one create-only stop event."""

    root = Path(run_dir)
    receipt = build_parallel_run_stop_receipt(
        root, event_id=event_id, stopped_reason=stopped_reason
    )
    _publish_create_only_json(
        root / "audit" / "parallel-run-stop-receipt.json", receipt
    )
    return receipt


def _run_quiescence_blocker(run_dir: Path, metadata: Mapping[str, Any]) -> str | None:
    receipt = _load_json(run_dir / "audit" / "parallel-run-stop-receipt.json")
    if not isinstance(receipt, Mapping):
        return "producer_stop_receipt_missing"
    evidence_digest = _evidence_snapshot_sha256(run_dir)
    if not (
        receipt.get("schema") == "dcb.parallel-run-stop-receipt.v1"
        and receipt.get("event_type") == "producer.quiesced"
        and isinstance(receipt.get("event_id"), str)
        and receipt.get("event_id")
        and receipt.get("stopped_reason") in _STOP_REASONS
        and receipt.get("run_definition_sha256") == _run_definition_sha256(metadata)
        and evidence_digest is not None
        and receipt.get("evidence_snapshot_sha256") == evidence_digest
        and receipt.get("workers_drained") is True
        and receipt.get("importer_drained") is True
        and receipt.get("producer_drain_receipts_sha256")
        == _producer_drains_sha256(run_dir, metadata)
        and receipt.get("emitted_by") == "discord-capture-event-router"
        and receipt.get("outbound_actions") == "disabled"
    ):
        return "producer_stop_receipt_invalid_or_stale"
    return None


def _legacy_index(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _valid_ready(payload: object, worker: int, index: int) -> bool:
    return bool(
        isinstance(payload, Mapping)
        and payload.get("schema") == "dcb.browser-spool.v1"
        and payload.get("status") in _TERMINAL_READY_STATES
        and _legacy_index(payload.get("worker")) == worker
        and _legacy_index(payload.get("index")) == index
    )


def _valid_receipt(payload: object, worker: int, index: int) -> bool:
    if not (
        isinstance(payload, Mapping)
        and payload.get("schema") == "dcb-parallel-import-receipt.v1"
        and _legacy_index(payload.get("worker")) == worker
        and _legacy_index(payload.get("index")) == index
        and payload.get("commit_state") in _TERMINAL_COMMIT_STATES
        and payload.get("outbound_actions") == "disabled"
        and isinstance(payload.get("ready_sha256"), str)
        and _SHA256.fullmatch(str(payload.get("ready_sha256"))) is not None
    ):
        return False
    if payload.get("commit_state") == "committed":
        return bool(
            payload.get("source_status") == "ready"
            and isinstance(payload.get("text_sha256"), str)
            and _SHA256.fullmatch(str(payload.get("text_sha256"))) is not None
        )
    return bool(
        payload.get("source_status") in {"no_message_list", "nav_thread_missing"}
        and payload.get("text_sha256") is None
    )


def _load_commit_ledger(
    path: Path,
) -> tuple[list[Mapping[str, Any]], int, bool]:
    try:
        if path.stat().st_size > _MAX_LEDGER_BYTES:
            return [], 0, False
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], 0, False
    rows: list[Mapping[str, Any]] = []
    valid = True
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(payload, Mapping):
            valid = False
            continue
        rows.append(payload)
    return rows, len(lines), valid


def _ledger_matches_receipt(
    row: Mapping[str, Any],
    receipt: Mapping[str, Any],
    worker: int,
    index: int,
) -> bool:
    return bool(
        row.get("schema")
        in {
            "dcb-parallel-commit-ledger.v1",
            "dcb-parallel-commit-ledger-supplement.v1",
        }
        and _legacy_index(row.get("worker")) == worker
        and _legacy_index(row.get("index")) == index
        and row.get("commit_state") == receipt.get("commit_state")
        and row.get("ready_sha256") == receipt.get("ready_sha256")
        and row.get("text_sha256") == receipt.get("text_sha256")
        and row.get("outbound_actions") == "disabled"
    )


def _parent_is_full(parent_audit: Mapping[str, Any] | None) -> tuple[bool, str | None]:
    if parent_audit is None:
        return False, "parent_completeness_certificate_missing"
    if not (
        parent_audit.get("schema") == "discord_parent_completeness_certificate.v1"
        and parent_audit.get("raw_text_returned") is False
        and parent_audit.get("identifiers_returned") is False
        and parent_audit.get("outbound_actions") == "disabled"
    ):
        return False, "parent_completeness_certificate_invalid"
    if not (
        parent_audit.get("status") == "full"
        and parent_audit.get("parent_full_capture_confirmed") is True
    ):
        return False, "parent_completeness_not_full"
    return True, None


def parent_target_key_digest(parent_target_key: str) -> str:
    """Return the privacy-safe binding used by legacy run metadata."""

    return sha256(
        b"dcb.parallel-parent-target.v1\0" + parent_target_key.encode("utf-8")
    ).hexdigest()


def _evaluate_legacy_parallel_run(
    run_dir: Path | str,
    *,
    parent_audit: Mapping[str, Any] | None = None,
    parent_target_key: str | None = None,
    finalize: bool = False,
    canonical_parent_source: bool = False,
) -> dict[str, Any]:
    """Evaluate operational closure without reading or returning private content.

    ``finalize`` closes incomplete legacy execution as ``blocked_closed``.  It
    never upgrades incomplete evidence to full.  Marker files are deliberately
    not read; canonical receipts and the existing parent certificate are the
    only completion inputs.
    """

    root = Path(run_dir)
    result = _base_result(finalize=finalize)
    blockers: list[str] = []
    metadata = _load_json(root / "run-metadata.json")
    if not (
        isinstance(metadata, Mapping)
        and metadata.get("schema") == "dcb.parallel-run.v1"
        and isinstance(metadata.get("canonical_count"), int)
        and not isinstance(metadata.get("canonical_count"), bool)
        and int(metadata["canonical_count"]) >= 0
        and metadata.get("outbound_actions") == "disabled"
    ):
        result["blockers"] = ["run_metadata_invalid"]
        return result

    result["run_definition_sha256"] = _run_definition_sha256(metadata)
    result["parent_target_key_sha256"] = metadata.get("parent_target_key_sha256")
    result["evidence_snapshot_sha256"] = _evidence_snapshot_sha256(root)

    expected = int(metadata["canonical_count"])
    declared_shards = metadata.get("shard_counts")
    shard_total = (
        sum(declared_shards)
        if isinstance(declared_shards, list)
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in declared_shards
        )
        else -1
    )
    validated_shard_counts = declared_shards if shard_total >= 0 else []
    bindings, shard_blockers = _canonical_bindings(root, validated_shard_counts)
    blockers.extend(shard_blockers)
    if expected != len(bindings) or shard_total != expected:
        blockers.append("canonical_shard_count_mismatch")

    ready_payloads: dict[tuple[int, int], Mapping[str, Any]] = {}
    receipt_payloads: dict[tuple[int, int], Mapping[str, Any]] = {}
    for worker, index in bindings:
        ready = _load_json(_ready_path(root, worker, index))
        if _valid_ready(ready, worker, index):
            ready_payloads[(worker, index)] = ready
        receipt = _load_json(_receipt_path(root, worker, index))
        if _valid_receipt(receipt, worker, index):
            receipt_payloads[(worker, index)] = receipt

    ledger_rows, ledger_row_count, ledger_valid = _load_commit_ledger(
        root / "committed" / "commit-ledger.ndjson"
    )
    if not ledger_valid:
        blockers.append("commit_ledger_invalid")
    ledger_bindings = {
        binding
        for binding, receipt in receipt_payloads.items()
        if any(
            _ledger_matches_receipt(row, receipt, binding[0], binding[1])
            for row in ledger_rows
        )
    }
    artifact_hash_bindings: set[tuple[int, int]] = set()
    for binding, receipt in receipt_payloads.items():
        worker, index = binding
        if binding not in ready_payloads:
            continue
        ready_hash = _sha256_file(_ready_path(root, worker, index))
        if ready_hash != receipt.get("ready_sha256"):
            continue
        if receipt.get("commit_state") == "committed":
            text_hash = _sha256_file(_text_path(root, worker, index))
            if text_hash != receipt.get("text_sha256"):
                continue
        artifact_hash_bindings.add(binding)
    terminal_without_text = sum(
        receipt.get("commit_state") == "recorded_without_text"
        for receipt in receipt_payloads.values()
    )

    counts = result["counts"]
    counts.update(
        {
            "expected_items": expected,
            "canonical_ready_items": len(ready_payloads),
            "canonical_receipts": len(receipt_payloads),
            "canonical_ledger_bindings": len(ledger_bindings),
            "canonical_artifact_hash_bindings": len(artifact_hash_bindings),
            "terminal_without_text_items": terminal_without_text,
            "commit_ledger_rows": ledger_row_count,
            "missing_ready_items": max(expected - len(ready_payloads), 0),
            "missing_receipts": max(expected - len(receipt_payloads), 0),
            "missing_ledger_bindings": max(expected - len(ledger_bindings), 0),
            "missing_artifact_hash_bindings": max(
                expected - len(artifact_hash_bindings), 0
            ),
        }
    )
    if len(ready_payloads) != expected:
        blockers.append("canonical_ready_items_missing")
    if len(receipt_payloads) != expected:
        blockers.append("canonical_receipts_missing")
    if len(ledger_bindings) != expected:
        blockers.append("commit_ledger_binding_mismatch")
    if len(artifact_hash_bindings) != expected:
        blockers.append("artifact_hash_binding_mismatch")
    if terminal_without_text:
        blockers.append("terminal_without_text_items_present")

    parent_full, parent_blocker = _parent_is_full(parent_audit)
    if parent_blocker:
        blockers.append(parent_blocker)
    elif not canonical_parent_source:
        parent_full = False
        blockers.append("parent_completeness_source_unverified")
    elif not (
        isinstance(parent_target_key, str)
        and parent_target_key
        and metadata.get("parent_target_key_sha256")
        == parent_target_key_digest(parent_target_key)
    ):
        parent_full = False
        blockers.append("parent_target_binding_mismatch")
    else:
        result["parent_audit_sha256"] = _mapping_sha256(parent_audit)

    blockers = sorted(set(blockers))
    result["blockers"] = blockers
    if not blockers and parent_full:
        quiescence_blocker = _run_quiescence_blocker(root, metadata)
        if quiescence_blocker is None:
            result.update(
                {
                    "status": "full",
                    "terminal_state": "full_closed",
                    "full_capture_confirmed": True,
                }
            )
        else:
            result["blockers"] = [quiescence_blocker]
    elif finalize:
        quiescence_blocker = _run_quiescence_blocker(root, metadata)
        if quiescence_blocker is None:
            result.update({"status": "blocked", "terminal_state": "blocked_closed"})
        else:
            result["blockers"] = sorted(set(blockers + [quiescence_blocker]))
    return result


def evaluate_legacy_parallel_run(
    run_dir: Path | str,
    *,
    parent_audit: Mapping[str, Any] | None = None,
    parent_target_key: str | None = None,
    finalize: bool = False,
) -> dict[str, Any]:
    """Evaluate without granting completion authority to caller-provided JSON."""

    return _evaluate_legacy_parallel_run(
        run_dir,
        parent_audit=parent_audit,
        parent_target_key=parent_target_key,
        finalize=finalize,
        canonical_parent_source=False,
    )


def evaluate_legacy_parallel_run_from_store(
    run_dir: Path | str,
    *,
    completeness_db: Path | str,
    parent_target_key: str,
    finalize: bool = False,
) -> dict[str, Any]:
    """Evaluate with the canonical CompletenessStore as parent authority."""

    store = CompletenessStore(Path(completeness_db))
    store.initialize()
    return _evaluate_legacy_parallel_run(
        run_dir,
        parent_audit=store.audit_parent(parent_target_key),
        parent_target_key=parent_target_key,
        finalize=finalize,
        canonical_parent_source=True,
    )


def persist_legacy_parallel_closeout(
    run_dir: Path | str,
    *,
    completeness_db: Path | str | None = None,
    parent_target_key: str | None = None,
    finalize: bool = False,
) -> dict[str, Any]:
    """Re-evaluate canonical evidence, persist one immutable receipt, and project metadata.

    The receipt is a projection of the canonical DCB evidence.  It is not a
    replacement for a strict child or parent full-capture certificate.
    """

    root = Path(run_dir)
    parent_audit: Mapping[str, Any] | None = None
    if completeness_db is not None or parent_target_key is not None:
        if completeness_db is None or not parent_target_key:
            raise ValueError("completeness db and parent target key must be paired")
        store = CompletenessStore(Path(completeness_db))
        store.initialize()
        parent_audit = store.audit_parent(parent_target_key)
    result = (
        _evaluate_legacy_parallel_run(
            root,
            parent_audit=parent_audit,
            parent_target_key=parent_target_key,
            finalize=finalize,
            canonical_parent_source=True,
        )
        if parent_audit is not None and parent_target_key is not None
        else evaluate_legacy_parallel_run(root, finalize=finalize)
    )
    if result.get("terminal_state") not in {"full_closed", "blocked_closed"}:
        raise ValueError("terminal canonical closeout evidence required")

    metadata_path = root / "run-metadata.json"
    metadata = _load_json(metadata_path)
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("schema") != "dcb.parallel-run.v1"
    ):
        raise ValueError("valid parallel run metadata required")

    audit_dir = root / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    receipt = dict(result)
    receipt["persistence_confirmed"] = True
    receipt["recorded_by"] = "discord-context-bridge"
    report_path = audit_dir / "parallel-run-closeout.json"
    if report_path.exists():
        existing = _load_json(report_path)
        if not isinstance(existing, Mapping):
            raise ValueError("existing closeout receipt invalid")
        comparable = dict(existing)
        comparable.pop("recorded_at", None)
        if comparable != receipt:
            raise ValueError("immutable closeout receipt conflict")
        receipt = dict(existing)
    else:
        receipt["recorded_at"] = datetime.now(UTC).isoformat()
        try:
            _publish_create_only_json(report_path, receipt)
        except ValueError as exc:
            raise ValueError("closeout receipt was concurrently created") from exc

    updated_metadata = dict(metadata)
    updated_metadata.update(
        {
            "status": receipt["terminal_state"],
            "closed_at": receipt["recorded_at"],
            "closeout_schema": receipt["schema"],
            "closeout_report": "audit/parallel-run-closeout.json",
        }
    )
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=root,
        prefix=".run-metadata.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(
            updated_metadata, handle, ensure_ascii=False, indent=2, sort_keys=True
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_path, metadata_path)
        _fsync_directory(root)
    finally:
        temp_path.unlink(missing_ok=True)
    return receipt
