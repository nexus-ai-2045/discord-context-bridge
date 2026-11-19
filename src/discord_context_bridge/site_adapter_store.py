from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .site_adapter_runtime import build_manifest, capture_id, enforce_capture_budget, validate_artifact


def _safe_root(root: Path) -> Path:
    if str(root).startswith("\\\\") or root.anchor.startswith("\\\\") or root.drive.startswith("\\\\"):
        raise ValueError("network output root is not allowed")
    if ".local" not in root.parts:
        raise ValueError("output root must be under .local")
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError("symlink output root is not allowed")
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve()
    if resolved != root.absolute():
        raise ValueError("symlink or reparse output root is not allowed")
    return resolved


def _safe_artifact_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    current = root
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("nested symlink artifact path is not allowed")
        if current.exists() and not current.resolve().is_relative_to(root):
            raise ValueError("artifact path escape is not allowed")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("symlink artifact file is not allowed")
    if not candidate.parent.resolve().is_relative_to(root):
        raise ValueError("artifact path escape is not allowed")
    return candidate


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def store_capture(capture: dict[str, Any], output_root: Path, *, writer=_atomic_json) -> dict[str, Any]:
    enforce_capture_budget(capture)
    root = _safe_root(output_root)
    identifier = capture_id(capture)
    relative_raw = f"raw/{identifier}.json"
    manifest = build_manifest(capture, raw_json=relative_raw)
    validate_artifact(capture, "dcb_raw_capture.v1.schema.json")
    validate_artifact(manifest, "dcb_capture_manifest.v1.schema.json")
    raw_path = _safe_artifact_path(root, relative_raw)
    manifest_path = _safe_artifact_path(root, f"manifests/{identifier}.json")
    try:
        if not _valid_json(raw_path, capture, "dcb_raw_capture.v1.schema.json"):
            writer(raw_path, capture)
        if not _valid_json(manifest_path, manifest, "dcb_capture_manifest.v1.schema.json"):
            writer(manifest_path, manifest)
    except OSError:
        return {
            "capture_id": identifier,
            "capture_state": "blocked",
            "status": "blocked",
            "recoverable": True,
            "failure_stage": "private_persistence",
            "review_required": True,
            "outbound_actions": "disabled",
        }
    return {
        "capture_id": identifier,
        "capture_state": manifest["capture_state"],
        "status": manifest["status"],
        "drift_detected": manifest["drift"]["detected"],
        "review_required": True,
        "outbound_actions": "disabled",
    }


def _valid_json(path: Path, expected: dict[str, Any], schema_name: str) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        validate_artifact(payload, schema_name)
        return payload == expected
    except Exception:
        return False
