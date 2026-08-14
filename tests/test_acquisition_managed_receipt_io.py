from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from discord_context_bridge import acquisition_gate
from discord_context_bridge.capture.store import CaptureCheckpointStore
from discord_context_bridge.core import _load_acquisition_full_capture_receipt


def _managed_receipt_path(tmp_path: Path) -> Path:
    path = tmp_path / "store" / "receipts" / "full-capture" / "capture-1.json"
    path.parent.mkdir(parents=True)
    return path


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_managed_receipt_fifo_bypasses_generic_path_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _managed_receipt_path(tmp_path)
    os.mkfifo(path)
    monkeypatch.setattr(
        acquisition_gate,
        "load_full_capture_receipt",
        lambda path: (_ for _ in ()).throw(
            AssertionError("managed receipt used generic path reader")
        ),
    )

    receipt, error = _load_acquisition_full_capture_receipt(path)

    assert receipt is None
    assert error == "full_capture_receipt_evidence_invalid"


def test_managed_receipt_symlink_bypasses_generic_path_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _managed_receipt_path(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema": "copied"}), encoding="utf-8")
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    monkeypatch.setattr(
        acquisition_gate,
        "load_full_capture_receipt",
        lambda path: (_ for _ in ()).throw(
            AssertionError("managed receipt used generic path reader")
        ),
    )

    receipt, error = _load_acquisition_full_capture_receipt(path)

    assert receipt is None
    assert error == "full_capture_receipt_evidence_invalid"


def test_managed_receipt_uses_store_loader_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _managed_receipt_path(tmp_path)
    expected = {"schema": "dcb-strict-full-capture-receipt.v1"}
    observed: dict[str, str] = {}

    def load_from_store(self, capture_id: str, *, consumer: str):
        observed["root"] = str(self.root)
        observed["capture_id"] = capture_id
        observed["consumer"] = consumer
        return expected

    monkeypatch.setattr(
        acquisition_gate,
        "load_full_capture_receipt",
        lambda path: (_ for _ in ()).throw(
            AssertionError("managed receipt used generic path reader")
        ),
    )
    monkeypatch.setattr(
        CaptureCheckpointStore, "load_full_capture_receipt", load_from_store
    )

    receipt, error = _load_acquisition_full_capture_receipt(path)

    assert receipt == expected
    assert error == ""
    assert observed == {
        "root": str(tmp_path / "store"),
        "capture_id": "capture-1",
        "consumer": "context_acquisition",
    }


def test_noncanonical_legacy_receipt_keeps_generic_loader(tmp_path: Path) -> None:
    path = tmp_path / "legacy-receipt.json"
    expected = {
        "schema": "discord_full_capture_completion_gate.v1",
        "capture_id": "capture-1",
    }
    path.write_text(json.dumps(expected), encoding="utf-8")

    assert _load_acquisition_full_capture_receipt(path) == (expected, "")


def test_strict_receipt_copied_outside_managed_store_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "copied-strict-receipt.json"
    path.write_text(
        json.dumps(
            {
                "schema": "dcb-strict-full-capture-receipt.v1",
                "capture_id": "capture-1",
            }
        ),
        encoding="utf-8",
    )

    assert _load_acquisition_full_capture_receipt(path) == (
        None,
        "full_capture_receipt_evidence_invalid",
    )
