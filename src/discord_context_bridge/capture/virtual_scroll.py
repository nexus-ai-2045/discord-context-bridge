"""Merge virtualized Discord message windows into metadata-only coverage.

Discord renders only a moving window of a thread.  Counting the current DOM is
therefore not a coverage proof.  This module keeps message identity, ordering,
source overlap, edit versions, and stable rescan evidence without retaining
message bodies.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from heapq import heappop, heappush
from typing import Any, Mapping, Sequence


_SCHEMA = "dcb-virtual-scroll-coverage.v1"
_SOURCES = {
    "background_cache",
    "chrome_visible_dom",
    "discord_desktop_cache",
    "rest_backfill",
    "saved_cache",
    "saved_snapshot",
}
_DIRECTIONS = {"toward_oldest", "toward_latest", "stationary"}


def new_virtual_scroll_coverage(capture_id: str) -> dict[str, Any]:
    """Create an empty coverage envelope that contains no Discord body text."""

    if not str(capture_id).strip():
        raise ValueError("capture_id is required")
    return {
        "schema": _SCHEMA,
        "capture_id": str(capture_id),
        "messages": {},
        "windows": [],
        "ordered_message_ids": [],
        "sources": [],
        "unique_message_count": 0,
        "duplicate_observation_count": 0,
        "edited_message_count": 0,
        "invalid_observation_count": 0,
        "gap_count": 0,
        "coverage_connected": False,
        "oldest_reached": False,
        "latest_reached": False,
        "scan_passes": [],
        "stable_scan_passes": 0,
        "final_pass_new_message_count": None,
        "capture_stable_after_rescan": False,
        "blockers": [],
        "raw_text_returned": False,
        "outbound_actions": "disabled",
    }


def _safe_message_rows(value: Any) -> tuple[list[tuple[str, str]], int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return [], 1
    rows: list[tuple[str, str]] = []
    invalid = 0
    for item in value:
        if not isinstance(item, Mapping):
            invalid += 1
            continue
        message_id = str(item.get("message_id") or "").strip()
        content_hash = str(item.get("content_hash") or "").strip()
        if not message_id or not content_hash:
            invalid += 1
            continue
        rows.append((message_id, content_hash))
    return rows, invalid


def _ordered_ids(windows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return a stable topological order from consecutive window observations."""

    first_seen: dict[str, int] = {}
    edges: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    cursor = 0
    for window in windows:
        ids = [str(item) for item in window.get("message_ids", [])]
        for message_id in ids:
            if message_id not in first_seen:
                first_seen[message_id] = cursor
                cursor += 1
            edges.setdefault(message_id, set())
            indegree.setdefault(message_id, 0)
        for left, right in zip(ids, ids[1:]):
            if left == right or right in edges[left]:
                continue
            edges[left].add(right)
            indegree[right] += 1

    ready: list[tuple[int, str]] = []
    for message_id, degree in indegree.items():
        if degree == 0:
            heappush(ready, (first_seen[message_id], message_id))
    ordered: list[str] = []
    while ready:
        _, message_id = heappop(ready)
        ordered.append(message_id)
        for target in sorted(edges[message_id], key=first_seen.__getitem__):
            indegree[target] -= 1
            if indegree[target] == 0:
                heappush(ready, (first_seen[target], target))
    if len(ordered) != len(indegree):
        # Conflicting window order is not trusted. Preserve deterministic first
        # observation order so the caller can see the blocker without data loss.
        return sorted(first_seen, key=first_seen.__getitem__)
    return ordered


def _window_components(windows: Sequence[Mapping[str, Any]]) -> int:
    sets = [set(map(str, item.get("message_ids", []))) for item in windows]
    sets = [item for item in sets if item]
    if not sets:
        return 0
    remaining = set(range(len(sets)))
    components = 0
    while remaining:
        components += 1
        frontier = [remaining.pop()]
        connected_ids: set[str] = set()
        while frontier:
            index = frontier.pop()
            connected_ids.update(sets[index])
            matches = [
                candidate
                for candidate in remaining
                if connected_ids.intersection(sets[candidate])
            ]
            for candidate in matches:
                remaining.remove(candidate)
                frontier.append(candidate)
    return components


def _recompute(coverage: dict[str, Any]) -> None:
    windows = coverage["windows"]
    coverage["ordered_message_ids"] = _ordered_ids(windows)
    components = _window_components(windows)
    coverage["gap_count"] = max(components - 1, 0)
    coverage["coverage_connected"] = bool(windows) and components == 1
    coverage["unique_message_count"] = len(coverage["messages"])
    coverage["edited_message_count"] = sum(
        len(message["version_hashes"]) > 1
        for message in coverage["messages"].values()
    )
    coverage["sources"] = sorted(
        {str(window["source"]) for window in windows}
    )
    blockers = set(coverage.get("blockers") or [])
    if coverage["invalid_observation_count"]:
        blockers.add("stable_message_id_missing")
    if coverage["gap_count"]:
        blockers.add("window_overlap_gap")
    else:
        blockers.discard("window_overlap_gap")
    coverage["blockers"] = sorted(blockers)


def _ordered_digest(message_ids: Sequence[str]) -> str:
    framed = "".join(f"{len(item)}:{item}" for item in message_ids)
    return sha256(framed.encode("utf-8")).hexdigest()


def _update_scan_stability(coverage: dict[str, Any]) -> None:
    grouped: dict[int, list[Mapping[str, Any]]] = {}
    for window in coverage["windows"]:
        scan_pass = window.get("scan_pass")
        if not isinstance(scan_pass, int) or isinstance(scan_pass, bool):
            continue
        grouped.setdefault(scan_pass, []).append(window)

    passes: list[dict[str, Any]] = []
    for scan_pass, windows in sorted(grouped.items()):
        oldest_reached = any(item.get("oldest_reached") is True for item in windows)
        latest_reached = any(item.get("latest_reached") is True for item in windows)
        components = _window_components(windows)
        if not oldest_reached or not latest_reached or components != 1:
            continue
        ordered_ids = _ordered_ids(windows)
        passes.append(
            {
                "scan_pass": scan_pass,
                "first_message_id": ordered_ids[0] if ordered_ids else "",
                "last_message_id": ordered_ids[-1] if ordered_ids else "",
                "message_count": len(ordered_ids),
                "ordered_message_digest": _ordered_digest(ordered_ids),
                "new_message_count": sum(
                    int(item.get("new_message_count") or 0) for item in windows
                ),
            }
        )

    coverage["scan_passes"] = passes
    coverage["oldest_reached"] = bool(passes)
    coverage["latest_reached"] = bool(passes)
    coverage["stable_scan_passes"] = len(passes)
    coverage["final_pass_new_message_count"] = (
        passes[-1]["new_message_count"] if passes else None
    )
    if len(passes) < 2:
        coverage["capture_stable_after_rescan"] = False
        return
    previous, current = passes[-2:]
    coverage["capture_stable_after_rescan"] = bool(
        previous["ordered_message_digest"] == current["ordered_message_digest"]
        and current["new_message_count"] == 0
        and coverage["coverage_connected"]
        and not coverage["blockers"]
    )


def merge_capture_window(
    coverage: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge one browser/cache window using message IDs as canonical identity."""

    if coverage.get("schema") != _SCHEMA:
        raise ValueError("unsupported virtual scroll coverage schema")
    window_id = str(observation.get("window_id") or "").strip()
    source = str(observation.get("source") or "").strip()
    direction = str(observation.get("direction") or "").strip()
    if not window_id:
        raise ValueError("window_id is required")
    if source not in _SOURCES:
        raise ValueError("unsupported window source")
    if direction not in _DIRECTIONS:
        raise ValueError("unsupported window direction")

    updated = deepcopy(dict(coverage))
    rows, invalid = _safe_message_rows(observation.get("messages"))
    updated["invalid_observation_count"] += invalid
    existing_window = next(
        (item for item in updated["windows"] if item["window_id"] == window_id),
        None,
    )
    message_ids = [message_id for message_id, _ in rows]
    if existing_window is not None:
        if (
            existing_window["message_ids"] != message_ids
            or existing_window["source"] != source
        ):
            raise ValueError("window_id is bound to another observation")
        return updated

    new_message_count = 0
    for message_id, content_hash in rows:
        message = updated["messages"].get(message_id)
        if message is None:
            updated["messages"][message_id] = {
                "version_hashes": [content_hash],
                "sources": [source],
            }
            new_message_count += 1
            continue
        updated["duplicate_observation_count"] += 1
        if content_hash not in message["version_hashes"]:
            message["version_hashes"].append(content_hash)
        if source not in message["sources"]:
            message["sources"].append(source)
            message["sources"].sort()

    updated["windows"].append(
        {
            "window_id": window_id,
            "source": source,
            "direction": direction,
            "message_ids": message_ids,
            "scan_pass": observation.get("scan_pass"),
            "oldest_reached": observation.get("oldest_reached") is True,
            "latest_reached": observation.get("latest_reached") is True,
            "new_message_count": new_message_count,
        }
    )
    _recompute(updated)
    _update_scan_stability(updated)
    return updated
