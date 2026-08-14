"""Durable, fail-closed persistence for resumable capture runs."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
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


def _secure_store_ops_supported() -> bool:
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.unlink in os.supports_dir_fd
    )


def _legacy_path_stat(path: Path) -> os.stat_result:
    """Stat a path without accepting links or Windows reparse points."""

    result = path.lstat()
    attributes = int(getattr(result, "st_file_attributes", 0) or 0)
    if stat.S_ISLNK(result.st_mode) or attributes & 0x400:
        raise CheckpointCorruptError(
            "managed store path contains a link or reparse point"
        )
    return result


def _legacy_store_path(root: Path, path: Path, *, create_parent: bool) -> Path:
    """Validate the conservative path backend used when dir_fd is unavailable."""

    parts = _store_relative_parts(root, path)
    root_absolute = root.absolute()
    if create_parent:
        root_absolute.mkdir(parents=True, exist_ok=True)
    root_stat = _legacy_path_stat(root_absolute)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise CheckpointCorruptError("managed store root is not a directory")

    current = root_absolute
    for part in parts[:-1]:
        current = current / part
        try:
            current_stat = _legacy_path_stat(current)
        except FileNotFoundError:
            if not create_parent:
                raise
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            current_stat = _legacy_path_stat(current)
        if not stat.S_ISDIR(current_stat.st_mode):
            raise CheckpointCorruptError(
                "managed store path contains an invalid directory"
            )
    return root_absolute.joinpath(*parts)


def _legacy_read_store_relative_bytes(
    root: Path, path: Path, *, max_bytes: int | None = None
) -> bytes | None:
    try:
        checked = _legacy_store_path(root, path, create_parent=False)
    except FileNotFoundError:
        return None
    try:
        before_named = _legacy_path_stat(checked)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before_named.st_mode):
        raise CheckpointCorruptError("managed store object is not a regular file")
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        descriptor = os.open(checked, flags)
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            opened_before = os.fstat(descriptor)
            limit = max_bytes + 1 if max_bytes is not None else -1
            content = handle.read(limit)
            opened_after = os.fstat(descriptor)
        after_named = _legacy_path_stat(checked)
        _legacy_store_path(root, path, create_parent=False)
    except (OSError, FileNotFoundError) as error:
        raise CheckpointCorruptError("managed store object is unreadable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identities = (before_named, opened_before, opened_after, after_named)
    if (
        any(not stat.S_ISREG(item.st_mode) for item in identities)
        or len({(item.st_dev, item.st_ino) for item in identities}) != 1
        or opened_before.st_size != opened_after.st_size
        or opened_after.st_size != len(content)
    ):
        raise CheckpointCorruptError("managed store object changed during read")
    if max_bytes is not None and len(content) > max_bytes:
        raise CheckpointCorruptError("managed store object is too large")
    return content


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("managed store write made no progress")
        offset += written


def _legacy_append_store_relative_bytes(root: Path, path: Path, content: bytes) -> None:
    checked = _legacy_store_path(root, path, create_parent=True)
    descriptor: int | None = None
    original_size = 0
    try:
        descriptor = os.open(checked, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        opened = os.fstat(descriptor)
        named = _legacy_path_stat(checked)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise CheckpointCorruptError(
                "managed store object is not a bound regular file"
            )
        original_size = opened.st_size
        try:
            _write_all(descriptor, content)
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            named_after = _legacy_path_stat(checked)
            _legacy_store_path(root, path, create_parent=False)
            if (
                after.st_size != original_size + len(content)
                or (after.st_dev, after.st_ino)
                != (named_after.st_dev, named_after.st_ino)
            ):
                raise CheckpointCorruptError(
                    "managed store object changed during append"
                )
        except (CheckpointCorruptError, OSError) as error:
            try:
                os.ftruncate(descriptor, original_size)
                os.fsync(descriptor)
            except OSError as rollback_error:
                raise CheckpointCorruptError(
                    "managed store append rollback failed"
                ) from rollback_error
            if isinstance(error, CheckpointCorruptError):
                raise
            raise CheckpointCorruptError("managed store object is unwritable") from error
    except OSError as error:
        raise CheckpointCorruptError("managed store object is unwritable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _legacy_atomic_store_json(root: Path, path: Path, encoded: bytes) -> None:
    checked = _legacy_store_path(root, path, create_parent=True)
    temporary: str | None = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{checked.name}.", suffix=".tmp", dir=checked.parent
        )
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            written = os.fstat(descriptor)
            if not stat.S_ISREG(written.st_mode) or written.st_size != len(encoded):
                raise CheckpointCorruptError("managed store object write is incomplete")
        finally:
            os.close(descriptor)
        _legacy_store_path(root, path, create_parent=False)
        temporary_stat = _legacy_path_stat(Path(temporary))
        if not stat.S_ISREG(temporary_stat.st_mode):
            raise CheckpointCorruptError("managed store temporary is not a regular file")
        os.replace(temporary, checked)
        temporary = None
        named = _legacy_path_stat(checked)
        _legacy_store_path(root, path, create_parent=False)
        if not stat.S_ISREG(named.st_mode) or named.st_size != len(encoded):
            raise CheckpointCorruptError("managed store object changed during replace")
    except OSError as error:
        raise CheckpointCorruptError("managed store object is unwritable") from error
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink()
            except OSError:
                pass


def _legacy_unlink_store_relative(root: Path, path: Path) -> None:
    try:
        checked = _legacy_store_path(root, path, create_parent=False)
        target = _legacy_path_stat(checked)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(target.st_mode):
        raise CheckpointCorruptError("managed store object is not a regular file")
    try:
        checked.unlink()
        _legacy_store_path(root, path, create_parent=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise CheckpointCorruptError("managed store object cannot be removed") from error


def _store_relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise CheckpointCorruptError("managed store path escapes store root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise CheckpointCorruptError("managed store path is invalid")
    return relative.parts


def _store_bindings_match(
    root: Path,
    directory_fds: list[int],
    bindings: list[tuple[int, str, int]],
) -> bool:
    if not directory_fds:
        return False
    try:
        named_root = os.stat(root.absolute(), follow_symlinks=False)
        opened_root = os.fstat(directory_fds[0])
        if (
            not stat.S_ISDIR(named_root.st_mode)
            or named_root.st_dev != opened_root.st_dev
            or named_root.st_ino != opened_root.st_ino
        ):
            return False
        for parent_fd, name, child_fd in bindings:
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(child_fd)
            if (
                not stat.S_ISDIR(named.st_mode)
                or named.st_dev != opened.st_dev
                or named.st_ino != opened.st_ino
            ):
                return False
    except OSError:
        return False
    return True


def _open_store_directory_chain(
    root: Path, parent_parts: tuple[str, ...], *, create: bool
) -> tuple[list[int], list[tuple[int, str, int]]]:
    if not _secure_store_ops_supported():
        raise CheckpointCorruptError("secure store operations are unavailable")
    root_absolute = root.absolute()
    if create:
        root_absolute.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fds: list[int] = []
    bindings: list[tuple[int, str, int]] = []
    try:
        directory_fds.append(os.open(root_absolute, flags))
        for part in parent_parts:
            parent_fd = directory_fds[-1]
            try:
                child_fd = os.open(part, flags, dir_fd=parent_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                child_fd = os.open(part, flags, dir_fd=parent_fd)
            directory_fds.append(child_fd)
            bindings.append((parent_fd, part, child_fd))
        if not _store_bindings_match(root_absolute, directory_fds, bindings):
            raise CheckpointCorruptError("managed store directory binding changed")
        return directory_fds, bindings
    except Exception as error:
        for descriptor in reversed(directory_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if isinstance(error, FileNotFoundError):
            raise
        if isinstance(error, OSError):
            raise CheckpointCorruptError(
                "managed store path contains a link or invalid directory"
            ) from error
        raise


def _close_store_directory_chain(directory_fds: list[int]) -> None:
    for descriptor in reversed(directory_fds):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _read_store_relative_object(
    root: Path, path: Path, *, max_bytes: int | None = None
) -> tuple[bytes, tuple[int, int]] | None:
    parts = _store_relative_parts(root, path)
    try:
        directory_fds, bindings = _open_store_directory_chain(
            root, parts[:-1], create=False
        )
    except FileNotFoundError:
        return None
    try:
        parent_fd = directory_fds[-1]
        try:
            file_flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, "O_NONBLOCK"):
                file_flags |= os.O_NONBLOCK
            descriptor = os.open(parts[-1], file_flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise CheckpointCorruptError("managed store object is not a regular file")
            limit = max_bytes + 1 if max_bytes is not None else -1
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                content = handle.read(limit)
            after = os.fstat(descriptor)
            named = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            if (
                before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or after.st_size != len(content)
                or named.st_dev != after.st_dev
                or named.st_ino != after.st_ino
                or not _store_bindings_match(root, directory_fds, bindings)
            ):
                raise CheckpointCorruptError("managed store object changed during read")
            if max_bytes is not None and len(content) > max_bytes:
                raise CheckpointCorruptError("managed store object is too large")
            return content, (after.st_dev, after.st_ino)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CheckpointCorruptError("managed store object is unreadable") from error
    finally:
        _close_store_directory_chain(directory_fds)


def _read_store_relative_bytes(
    root: Path, path: Path, *, max_bytes: int | None = None
) -> bytes | None:
    if not _secure_store_ops_supported():
        return _legacy_read_store_relative_bytes(root, path, max_bytes=max_bytes)
    result = _read_store_relative_object(root, path, max_bytes=max_bytes)
    return result[0] if result is not None else None


def _opened_store_file_matches(
    root: Path,
    directory_fds: list[int],
    bindings: list[tuple[int, str, int]],
    name: str,
    descriptor: int,
) -> bool:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(
            name, dir_fd=directory_fds[-1], follow_symlinks=False
        )
    except OSError:
        return False
    return (
        stat.S_ISREG(opened.st_mode)
        and stat.S_ISREG(named.st_mode)
        and opened.st_dev == named.st_dev
        and opened.st_ino == named.st_ino
        and _store_bindings_match(root, directory_fds, bindings)
    )


def _open_store_relative_regular(
    root: Path,
    path: Path,
    *,
    flags: int,
) -> tuple[int, list[int], list[tuple[int, str, int]], str]:
    parts = _store_relative_parts(root, path)
    directory_fds, bindings = _open_store_directory_chain(
        root, parts[:-1], create=True
    )
    descriptor: int | None = None
    try:
        open_flags = flags | os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            open_flags |= os.O_NONBLOCK
        descriptor = os.open(
            parts[-1], open_flags, 0o600, dir_fd=directory_fds[-1]
        )
        if not _opened_store_file_matches(
            root, directory_fds, bindings, parts[-1], descriptor
        ):
            raise CheckpointCorruptError(
                "managed store object is not a bound regular file"
            )
        return descriptor, directory_fds, bindings, parts[-1]
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        _close_store_directory_chain(directory_fds)
        raise CheckpointCorruptError("managed store object is unwritable") from error
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        _close_store_directory_chain(directory_fds)
        raise


def _append_store_relative_bytes(root: Path, path: Path, content: bytes) -> None:
    if not _secure_store_ops_supported():
        _legacy_append_store_relative_bytes(root, path, content)
        return
    descriptor, directory_fds, bindings, name = _open_store_relative_regular(
        root,
        path,
        flags=os.O_WRONLY | os.O_APPEND | os.O_CREAT,
    )
    try:
        before = os.fstat(descriptor)
        try:
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    raise OSError("managed store append made no progress")
                offset += written
            os.fsync(descriptor)
            after = os.fstat(descriptor)
            if (
                after.st_size != before.st_size + len(content)
                or not _opened_store_file_matches(
                    root, directory_fds, bindings, name, descriptor
                )
            ):
                raise CheckpointCorruptError(
                    "managed store object changed during append"
                )
            os.fsync(directory_fds[-1])
        except (CheckpointCorruptError, OSError) as error:
            try:
                os.ftruncate(descriptor, before.st_size)
                os.fsync(descriptor)
            except OSError as rollback_error:
                raise CheckpointCorruptError(
                    "managed store append rollback failed"
                ) from rollback_error
            if isinstance(error, CheckpointCorruptError):
                raise
            raise CheckpointCorruptError(
                "managed store object is unwritable"
            ) from error
    finally:
        os.close(descriptor)
        _close_store_directory_chain(directory_fds)


def _atomic_store_json(root: Path, path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    if not _secure_store_ops_supported():
        _legacy_atomic_store_json(root, path, encoded)
        return
    parts = _store_relative_parts(root, path)
    directory_fds, bindings = _open_store_directory_chain(
        root, parts[:-1], create=True
    )
    temporary_name = f".{parts[-1]}.{secrets.token_hex(16)}.tmp"
    temporary_exists = False
    try:
        parent_fd = directory_fds[-1]
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        temporary_exists = True
        try:
            handle = os.fdopen(descriptor, "wb")
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if not _store_bindings_match(root, directory_fds, bindings):
            raise CheckpointCorruptError("managed store directory binding changed")
        os.rename(
            temporary_name,
            parts[-1],
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_exists = False
        os.fsync(parent_fd)
        if not _store_bindings_match(root, directory_fds, bindings):
            raise CheckpointCorruptError("managed store directory binding changed")
    except OSError as error:
        raise CheckpointCorruptError("managed store object is unwritable") from error
    finally:
        if temporary_exists:
            try:
                os.unlink(temporary_name, dir_fd=directory_fds[-1])
            except OSError:
                pass
        _close_store_directory_chain(directory_fds)


def _unlink_store_relative(root: Path, path: Path) -> None:
    if not _secure_store_ops_supported():
        _legacy_unlink_store_relative(root, path)
        return
    parts = _store_relative_parts(root, path)
    try:
        directory_fds, bindings = _open_store_directory_chain(
            root, parts[:-1], create=False
        )
    except FileNotFoundError:
        return
    try:
        parent_fd = directory_fds[-1]
        try:
            target = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(target.st_mode):
            raise CheckpointCorruptError("managed store object is not a regular file")
        os.unlink(parts[-1], dir_fd=parent_fd)
        if not _store_bindings_match(root, directory_fds, bindings):
            raise CheckpointCorruptError("managed store directory binding changed")
    except OSError as error:
        raise CheckpointCorruptError("managed store object cannot be removed") from error
    finally:
        _close_store_directory_chain(directory_fds)


def canonical_capture_digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_private_ref(value: object) -> bool:
    ref = str(value or "")
    return bool(
        ref
        and "\\" not in ref
        and ":" not in ref
        and not ref.startswith("/")
        and all(part not in {"", ".", ".."} for part in ref.split("/"))
    )


def _contained_managed_path(root: Path, managed_ref: str) -> Path:
    if not _safe_private_ref(managed_ref):
        raise CheckpointCorruptError("managed attachment ref is invalid")
    root_absolute = root.absolute()
    path = root_absolute.joinpath(*managed_ref.split("/"))
    current = root_absolute
    for part in (Path(), *Path(*managed_ref.split("/")).parts):
        current = current / part
        try:
            stat_result = current.lstat()
        except FileNotFoundError:
            continue
        attributes = int(getattr(stat_result, "st_file_attributes", 0) or 0)
        if current.is_symlink() or attributes & 0x400:
            raise CheckpointCorruptError("managed attachment path uses a link or reparse point")
    try:
        path.resolve(strict=False).relative_to(root_absolute.resolve(strict=False))
    except ValueError as error:
        raise CheckpointCorruptError("managed attachment path escapes store root") from error
    return path


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

    def attachment_save_ledger_path(self, capture_id: str) -> Path:
        return self.root / "attachment-save-ledgers" / f"{_safe_capture_id(capture_id)}.json"

    def load_attachment_save_ledger(self, capture_id: str) -> dict[str, Any] | None:
        path = self.attachment_save_ledger_path(capture_id)
        if not _secure_store_ops_supported():
            try:
                checked = _legacy_store_path(self.root, path, create_parent=False)
                _legacy_path_stat(checked)
            except FileNotFoundError:
                return None
            raise CheckpointCorruptError(
                "secure attachment ledger operations are unavailable"
            )
        content = _read_store_relative_bytes(self.root, path)
        if content is None:
            return None
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointCorruptError("attachment save ledger is unreadable") from error
        if not isinstance(payload, dict):
            raise CheckpointCorruptError("attachment save ledger root is invalid")
        if payload.get("schema") != "dcb-private-attachment-save-ledger.v1":
            raise CheckpointCorruptError("attachment save ledger schema is invalid")
        if payload.get("capture_id") != capture_id:
            raise CheckpointCorruptError("attachment save ledger binding is invalid")
        records = payload.get("records")
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise CheckpointCorruptError("attachment save ledger records are invalid")
        if [item.get("sequence") for item in records] != list(range(1, len(records) + 1)):
            raise CheckpointCorruptError("attachment save ledger sequence is invalid")
        if any(
            not str(item.get("attachment_id") or "")
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
            or type(item.get("size")) is not int
            or item["size"] < 0
            or not _safe_private_ref(item.get("managed_ref"))
            for item in records
        ):
            raise CheckpointCorruptError("attachment save ledger record is invalid")
        expected_tip = canonical_capture_digest(records)
        if payload.get("tip_hash") != expected_tip:
            raise CheckpointCorruptError("attachment save ledger tip hash is invalid")
        seal = payload.get("seal")
        if seal is not None and (
            not isinstance(seal, dict)
            or set(seal) != {
                "message_sequence", "message_tip_hash", "coverage_digest",
                "window_count", "attachment_tip_hash",
            }
            or type(seal.get("message_sequence")) is not int
            or type(seal.get("window_count")) is not int
            or any(
                not re.fullmatch(r"[0-9a-f]{64}", str(seal.get(key) or ""))
                for key in ("message_tip_hash", "coverage_digest", "attachment_tip_hash")
            )
        ):
            raise CheckpointCorruptError("attachment save ledger seal is invalid")
        return payload

    def save_attachment_save_ledger(
        self, ledger: Mapping[str, Any], *, expected_sequence: int
    ) -> dict[str, Any]:
        if not _secure_store_ops_supported():
            raise CheckpointCorruptError(
                "secure attachment ledger operations are unavailable"
            )
        capture_id = _safe_capture_id(ledger.get("capture_id"))
        current = self.load_attachment_save_ledger(capture_id)
        current_sequence = len(current.get("records", [])) if current else 0
        if current_sequence != expected_sequence:
            raise SequenceConflictError(
                f"attachment ledger sequence conflict: expected {expected_sequence}, "
                f"found {current_sequence}"
            )
        payload = dict(ledger)
        if len(payload.get("records", [])) < current_sequence:
            raise SequenceConflictError("attachment ledger cannot move backwards")
        _atomic_store_json(self.root, self.attachment_save_ledger_path(capture_id), payload)
        if current != payload:
            self.invalidate_full_capture_receipt(capture_id)
        return payload

    def full_capture_receipt_path(self, capture_id: str) -> Path:
        return self.root / "receipts" / "full-capture" / f"{_safe_capture_id(capture_id)}.json"

    def invalidate_full_capture_receipt(self, capture_id: str) -> None:
        _unlink_store_relative(self.root, self.full_capture_receipt_path(capture_id))

    def browser_route_receipt_path(self, capture_id: str) -> Path:
        return self.root / "receipts" / "browser-route" / f"{_safe_capture_id(capture_id)}.json"

    def learning_handoff_receipt_path(self, capture_id: str) -> Path:
        return self.root / "receipts" / "learning-handoff" / f"{_safe_capture_id(capture_id)}.json"

    def _load_receipt(self, path: Path, *, capture_id: str, schema: str) -> dict[str, Any] | None:
        content = _read_store_relative_bytes(self.root, path)
        if content is None:
            return None
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
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
            if "source_binding" not in payload:
                return None
            binding = payload.get("source_binding")
            if not isinstance(binding, dict) or set(binding) != {
                "checkpoint_digest", "message_ledger_digest", "coverage_digest",
                "attachment_ledger_digest",
            }:
                raise CheckpointCorruptError("full capture receipt source binding is invalid")
            checkpoint = self.load_checkpoint(capture_id)
            current = {
                "checkpoint_digest": canonical_capture_digest(checkpoint),
                "message_ledger_digest": canonical_capture_digest(
                    self.load_message_ledger(capture_id)
                ),
                "coverage_digest": canonical_capture_digest(self.load_coverage(capture_id)),
                "attachment_ledger_digest": canonical_capture_digest(
                    self.load_attachment_save_ledger(capture_id)
                ),
            }
            if binding != current:
                raise CheckpointCorruptError("full capture receipt source binding is stale")
            if checkpoint is not None and (
                checkpoint.get("blocker") is not None
                or checkpoint.get("state") == "retry_wait"
                or str(checkpoint.get("state") or "").startswith("paused_")
                or checkpoint.get("state") == "blocked_closed"
            ):
                raise CheckpointCorruptError("full capture receipt checkpoint is not usable")
            attachment_ledger = self.load_attachment_save_ledger(capture_id)
            if attachment_ledger is not None:
                for record in attachment_ledger["records"]:
                    managed_ref = str(record["managed_ref"])
                    content = self.read_managed_object(
                        managed_ref, max_bytes=100_000_000
                    )
                    if (
                        sha256(content).hexdigest() != record["sha256"]
                        or len(content) != record["size"]
                    ):
                        raise CheckpointCorruptError(
                            "full capture receipt managed object is stale"
                        )
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
        _atomic_store_json(self.root, path, payload)
        return payload

    def read_managed_object(self, managed_ref: str, *, max_bytes: int) -> bytes:
        """Read one root-relative managed object without following path links."""

        result = self.read_managed_object_if_present(
            managed_ref, max_bytes=max_bytes
        )
        if result is None:
            raise CheckpointCorruptError("managed object is missing")
        return result[0]

    def read_managed_object_if_present(
        self, managed_ref: str, *, max_bytes: int
    ) -> tuple[bytes, tuple[int, int]] | None:
        """Read a managed object and its bound identity, or return absent."""

        if not _secure_store_ops_supported():
            raise CheckpointCorruptError(
                "secure managed object operations are unavailable"
            )
        path = _contained_managed_path(self.root, managed_ref)
        return _read_store_relative_object(self.root, path, max_bytes=max_bytes)

    def remove_managed_object(self, managed_ref: str) -> None:
        """Remove one root-relative managed object without following path links."""

        if not _secure_store_ops_supported():
            raise CheckpointCorruptError(
                "secure managed object operations are unavailable"
            )
        path = _contained_managed_path(self.root, managed_ref)
        _unlink_store_relative(self.root, path)

    @contextmanager
    def transition_lock(self, capture_id: str):
        """Use a crash-released, non-blocking OS lock for one capture."""

        safe_id = _safe_capture_id(capture_id)
        path = self.root / "locks" / f"{safe_id}.lock"
        if not _secure_store_ops_supported():
            with self._legacy_transition_lock(path):
                yield
            return
        descriptor, directory_fds, bindings, name = _open_store_relative_regular(
            self.root,
            path,
            flags=os.O_RDWR | os.O_CREAT,
        )
        locked = False
        try:
            if os.fstat(descriptor).st_size == 0:
                if os.write(descriptor, b"\0") != 1:
                    raise CheckpointCorruptError(
                        "capture transition lock initialization failed"
                    )
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise SequenceConflictError(
                    "capture transition is already locked"
                ) from error
            locked = True
            if not _opened_store_file_matches(
                self.root, directory_fds, bindings, name, descriptor
            ):
                raise CheckpointCorruptError(
                    "capture transition lock binding changed"
                )
            yield
            if not _opened_store_file_matches(
                self.root, directory_fds, bindings, name, descriptor
            ):
                raise CheckpointCorruptError(
                    "capture transition lock binding changed"
                )
        finally:
            if locked:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
            _close_store_directory_chain(directory_fds)

    @contextmanager
    def _legacy_transition_lock(self, path: Path):
        """Conservative Windows-compatible lock path with identity checks."""

        checked = _legacy_store_path(self.root, path, create_parent=True)
        descriptor: int | None = None
        locked = False
        try:
            descriptor = os.open(checked, os.O_RDWR | os.O_CREAT, 0o600)
            opened = os.fstat(descriptor)
            named = _legacy_path_stat(checked)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise CheckpointCorruptError(
                    "capture transition lock binding changed"
                )
            if opened.st_size == 0:
                if os.write(descriptor, b"\0") != 1:
                    raise CheckpointCorruptError(
                        "capture transition lock initialization failed"
                    )
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise SequenceConflictError(
                    "capture transition is already locked"
                ) from error
            locked = True
            _legacy_store_path(self.root, path, create_parent=False)
            named = _legacy_path_stat(checked)
            if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                raise CheckpointCorruptError(
                    "capture transition lock binding changed"
                )
            yield
            _legacy_store_path(self.root, path, create_parent=False)
            named = _legacy_path_stat(checked)
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != (named.st_dev, named.st_ino):
                raise CheckpointCorruptError(
                    "capture transition lock binding changed"
                )
        finally:
            if descriptor is not None and locked:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            if descriptor is not None:
                os.close(descriptor)

    def load_checkpoint(self, capture_id: str) -> dict[str, Any] | None:
        path = self.checkpoint_path(capture_id)
        content = _read_store_relative_bytes(self.root, path)
        if content is None:
            return None
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
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
        _atomic_store_json(self.root, self.checkpoint_path(capture_id), payload)
        if current != payload:
            self.invalidate_full_capture_receipt(capture_id)
        return payload

    def load_coverage(self, capture_id: str) -> dict[str, Any] | None:
        path = self.coverage_path(capture_id)
        content = _read_store_relative_bytes(self.root, path)
        if content is None:
            return None
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
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
        _atomic_store_json(self.root, self.coverage_path(capture_id), payload)
        return payload

    def load_message_ledger(self, capture_id: str) -> dict[str, Any] | None:
        path = self.message_ledger_path(capture_id)
        content = _read_store_relative_bytes(self.root, path)
        if content is None:
            return None
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
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
        previous = ""
        for event in events:
            if not isinstance(event, dict) or event.get("previous_event_hash") != previous:
                raise CheckpointCorruptError("message ledger hash chain is invalid")
            canonical = {key: value for key, value in event.items() if key != "event_hash"}
            if event.get("event_hash") != canonical_capture_digest(canonical):
                raise CheckpointCorruptError("message ledger event hash is invalid")
            previous = str(event["event_hash"])
        if "tip_hash" not in payload:
            payload["tip_hash"] = previous
        elif payload.get("tip_hash") != previous:
            raise CheckpointCorruptError("message ledger tip hash is invalid")
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
        _atomic_store_json(self.root, self.message_ledger_path(capture_id), payload)
        return payload

    def load_events(self, capture_id: str) -> list[dict[str, Any]]:
        path = self.ledger_path(capture_id)
        content = _read_store_relative_bytes(self.root, path)
        if content is None:
            return []
        events: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        try:
            lines = content.decode("utf-8").splitlines()
        except UnicodeError as error:
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
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        _append_store_relative_bytes(self.root, path, encoded)
        return {
            "capture_id": capture_id,
            "event_id": event_id,
            "sequence": payload["sequence"],
            "appended": True,
            "duplicate": False,
        }
