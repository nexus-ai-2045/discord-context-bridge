"""Read-only archived-thread enumeration with metadata-only public results."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


ARCHIVE_SCOPES = ("public", "private", "joined_private")
_MAX_RESPONSE_BYTES = 10_000_000


class ArchiveInventoryError(ValueError):
    """Raised when archived-thread evidence is malformed or cannot converge."""


def _cursor_for(scope: str, thread: Mapping[str, Any]) -> str:
    if scope == "joined_private":
        value = thread.get("id")
    else:
        metadata = thread.get("thread_metadata")
        value = metadata.get("archive_timestamp") if isinstance(metadata, Mapping) else None
    if not isinstance(value, str) or not value:
        raise ArchiveInventoryError("archive_cursor_missing")
    return value


def enumerate_archive_pages(
    *,
    scope: str,
    fetch_page: Callable[[str | None], Mapping[str, Any]],
    max_pages: int,
) -> dict[str, Any]:
    """Enumerate one Discord archive scope to its terminal cursor."""

    if scope not in ARCHIVE_SCOPES:
        raise ArchiveInventoryError("archive_scope_invalid")
    if max_pages < 1:
        raise ArchiveInventoryError("archive_max_pages_invalid")

    cursor: str | None = None
    seen_cursors: set[str] = set()
    threads: dict[str, dict[str, Any]] = {}
    pages = 0
    exhausted = False

    while pages < max_pages:
        payload = fetch_page(cursor)
        if not isinstance(payload, Mapping):
            raise ArchiveInventoryError("archive_response_invalid")
        page_threads = payload.get("threads")
        has_more = payload.get("has_more")
        if not isinstance(page_threads, Sequence) or isinstance(page_threads, (str, bytes)):
            raise ArchiveInventoryError("archive_threads_invalid")
        if not isinstance(has_more, bool):
            raise ArchiveInventoryError("archive_has_more_invalid")

        normalized: list[dict[str, Any]] = []
        for item in page_threads:
            if not isinstance(item, Mapping):
                raise ArchiveInventoryError("archive_thread_invalid")
            thread_id = item.get("id")
            if not isinstance(thread_id, str) or not thread_id:
                raise ArchiveInventoryError("archive_thread_id_missing")
            record = dict(item)
            threads[thread_id] = record
            normalized.append(record)
        pages += 1

        if not has_more:
            exhausted = True
            break
        if not normalized:
            raise ArchiveInventoryError("archive_pagination_empty_page")
        next_cursor = _cursor_for(scope, normalized[-1])
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise ArchiveInventoryError("archive_pagination_cursor_loop")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    blockers = [] if exhausted else ["archive_pagination_page_limit_reached"]
    return {
        "scope": scope,
        "pages": pages,
        "thread_count": len(threads),
        "pagination_exhausted": exhausted,
        "blockers": blockers,
        "threads": list(threads.values()),
    }


def build_public_report(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a metadata-only report without thread IDs, names, URLs, or paths."""

    scopes: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for result in results:
        scope = str(result.get("scope") or "")
        scope_blockers = [str(item) for item in result.get("blockers") or []]
        scopes[scope] = {
            "page_count": int(result.get("pages") or 0),
            "thread_count": int(result.get("thread_count") or 0),
            "pagination_exhausted": result.get("pagination_exhausted") is True,
        }
        blockers.extend(scope_blockers)
    complete = bool(scopes) and all(
        item["pagination_exhausted"] for item in scopes.values()
    )
    return {
        "schema": "dcb.archived-thread-inventory.v1",
        "status": "complete" if complete else "partial",
        "scopes": scopes,
        "pagination_exhausted": complete,
        "blockers": sorted(set(blockers)),
        "raw_text_returned": False,
        "participant_names_returned": False,
        "identifiers_returned": False,
        "url_output": "omitted",
        "path_output": "omitted",
        "outbound_actions": "disabled",
    }


def write_private_inventory(path: Path, results: Sequence[Mapping[str, Any]]) -> None:
    """Atomically persist private thread metadata with mode 0600."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "dcb.archived-thread-inventory-private.v1",
        "scopes": {
            str(result.get("scope")): {
                "pagination_exhausted": result.get("pagination_exhausted") is True,
                "threads": list(result.get("threads") or []),
            }
            for result in results
        },
        "outbound_actions": "disabled",
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    if len(encoded) > _MAX_RESPONSE_BYTES:
        raise ArchiveInventoryError("archive_inventory_too_large")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
