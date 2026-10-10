"""非公開の外部 snapshot を履歴時刻を保持して正本へ取り込む。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .capture.store import CaptureCheckpointStore, CheckpointCorruptError
from .core import (
    DISCORD_MESSAGE_URL_RE,
    _append_text_snapshot_transaction,
    _validate_text_snapshot_chain,
    acquisition_context_for_source,
    canonical_event_hash,
    stable_text_hash,
)
from .url_identity import classify_discord_url, parse_effective_channel_url

SOURCE_LIMIT = 16 * 1024 * 1024
STORE_LIMIT = 256 * 1024 * 1024
SCHEMA = "discord_context_bridge_text_snapshot_observation.v1"


class _Blocked(Exception):
    pass


def _identity(url: str) -> tuple[str, str]:
    if not isinstance(url, str) or not classify_discord_url(url)["valid"]:
        raise _Blocked("invalid_target")
    parsed = urlsplit(url)
    parts = parsed.path.rstrip("/").split("/")
    identity = parse_effective_channel_url(url)
    if parsed.query or parsed.fragment or identity is None:
        raise _Blocked("ambiguous_target")
    identifiers = parts[2:4] + (parts[5:] if len(parts) == 6 else [])
    if any(not part.isascii() or not part.isdigit() or not 17 <= len(part) <= 20
           for part in identifiers):
        raise _Blocked("invalid_target")
    return identity


def _directory(path: Path) -> int:
    """祖先を含む symlink を追跡せず directory descriptor を取得する。"""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:]:
            if part in (".", ".."):
                raise _Blocked("unsafe_path")
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except (OSError, _Blocked):
        os.close(descriptor)
        raise


def _read(path: Path, limit: int) -> bytes:
    parent = _directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_nlink != 1:
                raise _Blocked("unsafe_or_oversized_file")
            data = handle.read(limit + 1)
            if len(data) > limit:
                raise _Blocked("unsafe_or_oversized_file")
            return data
    finally:
        os.close(parent)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(data: bytes) -> list[dict]:
    result = [json.loads(line) for line in data.decode("utf-8").split("\n") if line.strip()]
    if any(not isinstance(row, dict) for row in result):
        raise _Blocked("invalid_snapshot_record")
    return result


def import_external_snapshot(
    *, snapshot_root: Path, source_file: Path, target_url: str,
    expected_source_sha256: str, expected_store_sha256: str | None = None,
    apply: bool = False,
) -> dict:
    """dry-run は無書込み。apply は正本 lock/CAS 下で stage 検証後に swap。"""
    result = {"status": "blocked", "events_appended": 0, "duplicate_content": False,
              "read_back": False, "reason": "import_failed", "commit_state": "none"}
    try:
        if not hasattr(os, "O_NOFOLLOW") or os.name != "posix":
            raise _Blocked("secure_filesystem_unavailable")
        root = Path(snapshot_root).absolute()
        source = Path(source_file).absolute()
        if ".." in root.parts or ".." in source.parts or not source.is_relative_to(root):
            raise _Blocked("source_outside_root")
        guild, channel = _identity(target_url)
        canonical_url = f"https://discord.com/channels/{guild}/{channel}"
        canonical = root / "discord" / "servers" / guild / "channels" / channel / "text-snapshots.ndjson"
        if canonical == source:
            raise _Blocked("source_is_canonical")
        source_data = _read(source, SOURCE_LIMIT)
        source_sha = _sha(source_data)
        if source_sha != expected_source_sha256:
            raise _Blocked("source_cas_mismatch")
        rows = _rows(source_data)
        if len(rows) != 1:
            raise _Blocked("single_snapshot_required")
        original = rows[0]
        if original.get("schema") not in (None, SCHEMA):
            raise _Blocked("unsupported_snapshot_schema")
        if _identity(original.get("url")) != (guild, channel):
            raise _Blocked("target_mismatch")
        if original.get("target_key") != stable_text_hash(original["url"]):
            raise _Blocked("target_key_mismatch")
        text = original.get("text")
        if not isinstance(text, str) or not text or original.get("content_hash") != stable_text_hash(text):
            raise _Blocked("content_hash_mismatch")
        if "event_hash" in original and original["event_hash"] != canonical_event_hash(original):
            raise _Blocked("source_event_hash_mismatch")
        if original.get("schema") == SCHEMA and not original.get("event_hash"):
            raise _Blocked("source_event_hash_missing")
        captured = original.get("captured_at")
        if not isinstance(captured, str):
            raise _Blocked("invalid_captured_at")
        timestamp = datetime.fromisoformat(captured)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise _Blocked("invalid_captured_at")
        if timestamp > datetime.now(UTC):
            raise _Blocked("future_captured_at")
        if not isinstance(original.get("source"), str) or not original["source"]:
            raise _Blocked("source_provenance_missing")
        if (original.get("private_local_only") is not True
                or original.get("external_share_allowed") is not False
                or original.get("outbound_actions") != "disabled"):
            raise _Blocked("private_only_required")
        record_hash = canonical_event_hash(original)
        key = stable_text_hash(canonical_url)
        import_id = _sha(f"{source_sha}:{record_hash}:{guild}:{channel}".encode())

        def inspect(data: bytes) -> tuple[list[dict], bool, bool]:
            records = _rows(data)
            try:
                _validate_text_snapshot_chain(records)
            except CheckpointCorruptError as error:
                raise _Blocked("canonical_chain_invalid") from error
            for row in records:
                if isinstance(row.get("text"), str) and stable_text_hash(row["text"]) != row.get("content_hash"):
                    raise _Blocked("canonical_content_hash_mismatch")
                row_url = row.get("url")
                message_match = (DISCORD_MESSAGE_URL_RE.fullmatch(row_url)
                                 if isinstance(row_url, str) else None)
                if message_match:
                    if any(not value.isascii() for value in message_match.groupdict().values()):
                        raise _Blocked("canonical_target_mismatch")
                    row_identity = (message_match["guild"], message_match["channel"])
                else:
                    row_identity = _identity(row_url)
                if row_identity != (guild, channel):
                    raise _Blocked("canonical_target_mismatch")
                allowed_keys = {key, stable_text_hash(row["url"])}
                if row.get("target_key") not in allowed_keys:
                    raise _Blocked("canonical_target_key_mismatch")
                if "stream_id" in row and row["stream_id"] != row.get("target_key"):
                    raise _Blocked("canonical_stream_mismatch")
                if row.get("external_import_id") == import_id and (
                    row.get("source_file_sha256") != source_sha
                    or row.get("source_record_hash") != record_hash
                    or row.get("content_hash") != original["content_hash"]
                    or row.get("original_snapshot_provenance") != original
                ):
                    raise _Blocked("import_identity_conflict")
            present = any(row.get("external_import_id") == import_id for row in records)
            duplicate = any(row.get("content_hash") == original["content_hash"] for row in records)
            return records, present, duplicate

        before = _read(canonical, STORE_LIMIT)
        store_sha = _sha(before)
        if expected_store_sha256 is not None and store_sha != expected_store_sha256:
            raise _Blocked("store_cas_mismatch")
        _, present, duplicate = inspect(before)
        result.update(source_sha256=source_sha, store_sha256=store_sha, duplicate_content=duplicate)
        if present:
            return {**result, "status": "already_present", "read_back": True,
                    "commit_state": "already_present_verified", "reason": "already_present"}
        if not apply:
            return {**result, "status": "ready", "reason": "validated_dry_run"}
        with CaptureCheckpointStore(canonical.parent).transition_lock("canonical-text-snapshots"):
            if _sha(_read(source, SOURCE_LIMIT)) != source_sha:
                raise _Blocked("source_cas_mismatch")
            if _read(canonical, STORE_LIMIT) != before:
                raise _Blocked("store_cas_mismatch")
            inspect(before)
            parent_fd = _directory(canonical.parent)
            stage_fd = None
            stage_file_fd = None
            stage_created = False
            try:
                parent_stat = os.fstat(parent_fd)
                stage_name = f".external-import-stage-{uuid4().hex}"
                os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
                stage_created = True
                stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=parent_fd)
                stage_dir_stat = os.fstat(stage_fd)
                stage_dir = canonical.parent / stage_name
                stage = stage_dir / canonical.name
                descriptor = os.open(canonical.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                     | os.O_NOFOLLOW, 0o600, dir_fd=stage_fd)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(before)
                    output.flush()
                    os.fsync(output.fileno())
                stage_file_fd = os.open(canonical.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=stage_fd)
                stage_file_stat = os.fstat(stage_file_fd)

                def build(records: list[dict]) -> dict:
                    heads = _validate_text_snapshot_chain(records)
                    seq, previous = heads.get(key, (0, ""))
                    previous_record = next((row for row in reversed(records)
                                            if row.get("target_key") == key), None)
                    previous_content = previous_record.get("content_hash") if previous_record else None
                    event = {
                        "schema": SCHEMA, "event_id": import_id, "external_import_id": import_id,
                        "event_type": "discord.visible_text.snapshot_observed", "stream_id": key,
                        "stream_sequence": seq + 1, "expected_previous_stream_sequence": seq,
                        "previous_event_hash": previous, "target_key": key, "url": canonical_url,
                        "specversion": "1.0", "type": "discord.visible_text.snapshot_observed",
                        "subject": key, "time": captured,
                        "datacontenttype": "text/plain; charset=utf-8", "dataschema": SCHEMA,
                        "previous_content_hash": previous_content,
                        "changed": previous_content != original["content_hash"],
                        "observation_index_for_target": seq + 1,
                        "acquisition_context": acquisition_context_for_source(original["source"]),
                        "captured_at": captured, "observed_at": captured,
                        "ingested_at": datetime.now(UTC).isoformat(),
                        "source": original["source"], "text": text,
                        "content_hash": original["content_hash"], "duplicate_content": duplicate,
                        "private_local_only": True, "external_share_allowed": False,
                        "outbound_actions": "disabled", "source_file_sha256": source_sha,
                        "source_record_hash": record_hash, "original_snapshot_provenance": original,
                    }
                    event["event_hash"] = canonical_event_hash(event)
                    return event

                appended, event, _ = _append_text_snapshot_transaction(build, stage)
                staged = _read(stage, STORE_LIMIT + SOURCE_LIMIT * 3)
                verified, found, _ = inspect(staged)
                if not appended or not found or verified[-1] != event or not staged.startswith(before):
                    raise _Blocked("stage_read_back_failed")
                current_parent = os.stat(canonical.parent, follow_symlinks=False)
                if (current_parent.st_dev, current_parent.st_ino) != (parent_stat.st_dev, parent_stat.st_ino):
                    raise _Blocked("canonical_parent_changed")
                if _read(canonical, STORE_LIMIT) != before or _sha(_read(source, SOURCE_LIMIT)) != source_sha:
                    raise _Blocked("pre_swap_cas_mismatch")
                current_stage_dir = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
                current_stage_file = os.stat(canonical.name, dir_fd=stage_fd, follow_symlinks=False)
                if ((current_stage_dir.st_dev, current_stage_dir.st_ino)
                        != (stage_dir_stat.st_dev, stage_dir_stat.st_ino)
                        or (current_stage_file.st_dev, current_stage_file.st_ino)
                        != (stage_file_stat.st_dev, stage_file_stat.st_ino)):
                    raise _Blocked("stage_binding_changed")
                os.lseek(stage_file_fd, 0, os.SEEK_SET)
                with os.fdopen(os.dup(stage_file_fd), "rb") as staged_handle:
                    final_staged = staged_handle.read(STORE_LIMIT + SOURCE_LIMIT * 3 + 1)
                if final_staged != staged:
                    raise _Blocked("stage_cas_mismatch")
                os.replace(canonical.name, canonical.name, src_dir_fd=stage_fd, dst_dir_fd=parent_fd)
                result.update(events_appended=1, commit_state="applied_unverified")
                os.fsync(parent_fd)
                actual = _read(canonical, STORE_LIMIT + SOURCE_LIMIT * 3)
                inspect(actual)
                if actual != staged:
                    raise _Blocked("canonical_read_back_failed")
                verified_sha = _sha(actual)
            finally:
                try:
                    if stage_created:
                        if stage_fd is None:
                            raise _Blocked("stage_cleanup_failed")
                        if stage_fd is not None:
                            with os.scandir(stage_fd) as entries:
                                for entry in entries:
                                    if entry.is_dir(follow_symlinks=False):
                                        shutil.rmtree(entry.name, dir_fd=stage_fd)
                                    else:
                                        os.unlink(entry.name, dir_fd=stage_fd)
                            opened = os.fstat(stage_fd)
                            named = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
                            if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
                                raise _Blocked("stage_cleanup_binding_changed")
                        os.rmdir(stage_name, dir_fd=parent_fd)
                except OSError as error:
                    raise _Blocked("stage_cleanup_failed") from error
                finally:
                    if stage_file_fd is not None:
                        os.close(stage_file_fd)
                    if stage_fd is not None:
                        os.close(stage_fd)
                    os.close(parent_fd)
        return {**result, "status": "absorbed", "commit_state": "applied_verified",
                "read_back": True, "store_sha256": verified_sha, "reason": "import_verified"}
    except _Blocked as error:
        return {**result, "reason": str(error)}
    except (ValueError, OSError, TypeError, RuntimeError, AttributeError):
        return result
