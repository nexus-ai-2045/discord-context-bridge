"""Autonomous, metadata-only full-capture orchestration primitives."""

from .orchestrator import advance_capture_run, new_capture_run
from .loop import (
    advance_capture_loop,
    build_capture_status_projection,
    derive_operational_tags,
    new_capture_loop,
    validate_observed_full_receipt,
)
from .reconcile import build_reconciliation_evidence
from .service import advance_persisted_capture, start_capture_loop
from .store import (
    CaptureCheckpointStore,
    CaptureStoreError,
    CheckpointCorruptError,
    EventConflictError,
    SequenceConflictError,
)

__all__ = [
    "CaptureCheckpointStore",
    "CaptureStoreError",
    "CheckpointCorruptError",
    "EventConflictError",
    "SequenceConflictError",
    "advance_capture_loop",
    "advance_persisted_capture",
    "advance_capture_run",
    "build_capture_status_projection",
    "build_reconciliation_evidence",
    "derive_operational_tags",
    "new_capture_loop",
    "new_capture_run",
    "start_capture_loop",
    "validate_observed_full_receipt",
]
