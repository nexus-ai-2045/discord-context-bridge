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
from .service import (
    append_persisted_message_event,
    advance_persisted_capture,
    merge_capture_windows_cache_first,
    merge_persisted_capture_window,
    read_capture_loop_status,
    rebuild_persisted_capture_projections,
    start_capture_loop,
)
from .store import (
    CaptureCheckpointStore,
    CaptureStoreError,
    CheckpointCorruptError,
    EventConflictError,
    SequenceConflictError,
)
from .virtual_scroll import merge_capture_window, new_virtual_scroll_coverage
from .message_ledger import (
    append_message_event,
    build_capture_projections,
    new_message_ledger,
)

__all__ = [
    "CaptureCheckpointStore",
    "CaptureStoreError",
    "CheckpointCorruptError",
    "EventConflictError",
    "SequenceConflictError",
    "advance_capture_loop",
    "append_persisted_message_event",
    "advance_persisted_capture",
    "advance_capture_run",
    "build_capture_status_projection",
    "build_reconciliation_evidence",
    "derive_operational_tags",
    "merge_capture_window",
    "merge_capture_windows_cache_first",
    "merge_persisted_capture_window",
    "append_message_event",
    "build_capture_projections",
    "new_message_ledger",
    "new_capture_loop",
    "new_capture_run",
    "new_virtual_scroll_coverage",
    "read_capture_loop_status",
    "rebuild_persisted_capture_projections",
    "start_capture_loop",
    "validate_observed_full_receipt",
]
