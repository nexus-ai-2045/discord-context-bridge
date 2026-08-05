from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .knowledge_projection import export_knowledge_projection


RECEIPT_SCHEMA = "dcb.knowledge_projection_run.v1"


class ProjectionAlreadyRunning(RuntimeError):
    pass


@contextmanager
def projection_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ProjectionAlreadyRunning("projection_already_running") from exc
    acquired = False
    try:
        try:
            if lock_path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise ProjectionAlreadyRunning("projection_already_running") from exc
        yield
    finally:
        if acquired:
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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _receipt(result: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    allowed_counts = {
        key: value
        for key, value in result.items()
        if key.endswith("_count") and isinstance(value, int)
    }
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": bool(result.get("ok")),
        "reason": str(result.get("reason") or ""),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "private_local_only": True,
        "outbound_actions": "disabled",
        "paths_returned": False,
        **allowed_counts,
    }


def run_projection(
    *,
    snapshot_store: Path,
    output_root: Path,
    receipt_path: Path,
    lock_path: Path,
    person_registry: Path | None = None,
    topic_registry: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    try:
        with projection_lock(lock_path):
            result = export_knowledge_projection(
                snapshot_store=snapshot_store,
                output_root=output_root,
                person_registry=person_registry,
                topic_registry=topic_registry,
                dry_run=dry_run,
            )
            receipt = _receipt(result, dry_run=dry_run)
            if not dry_run:
                _atomic_json(receipt_path, receipt)
            return receipt
    except ProjectionAlreadyRunning:
        return {
            "schema": RECEIPT_SCHEMA,
            "ok": False,
            "reason": "projection_already_running",
            "dry_run": dry_run,
            "private_local_only": True,
            "outbound_actions": "disabled",
            "paths_returned": False,
        }
    except Exception:
        return {
            "schema": RECEIPT_SCHEMA,
            "ok": False,
            "reason": "projection_failed",
            "dry_run": dry_run,
            "private_local_only": True,
            "outbound_actions": "disabled",
            "paths_returned": False,
        }


def verify_projection_receipt(receipt_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "reason": "receipt_unreadable"}
    if payload.get("schema") != RECEIPT_SCHEMA or payload.get("ok") is not True:
        return {"ok": False, "reason": "receipt_not_successful"}
    return {"ok": True, "reason": "", "recorded_at": payload.get("recorded_at", "")}
