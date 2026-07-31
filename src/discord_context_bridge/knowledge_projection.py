from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core import (
    AUTHOR_WITH_TIMESTAMP_RE,
    COLON_MESSAGE_RE,
    TIMESTAMP_METADATA_RE,
    TIMESTAMP_RE,
    DiscordEvent,
    load_text_snapshots,
)


PROJECTION_SCHEMA = "discord_context_bridge_knowledge_projection.v1"
GENERATED_MARKER = "dcb_knowledge_generated: true"

_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_HASHTAG_PATTERN = re.compile(r"(?<![\w/])#([\w][\w./-]*)", re.UNICODE)


def _yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _latest_by_target(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        target = str(
            record.get("target_key") or record.get("stream_id") or f"unknown-{index}"
        )
        sequence = int(
            record.get("stream_sequence")
            or record.get("observation_index_for_target")
            or index + 1
        )
        previous = latest.get(target)
        if previous is None or sequence >= previous[0]:
            latest[target] = (sequence, record)
    return [entry[1] for entry in latest.values()]


def _projection_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    structured: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    legacy: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if record.get("event_type") != "message_observation":
            legacy.append(record)
            continue
        target = str(record.get("target_key") or record.get("stream_id") or "unknown")
        message_identity = str(
            record.get("message_id")
            or "|".join(
                [
                    str(record.get("ordinal") or index),
                    str(record.get("author_label") or ""),
                    str(record.get("visible_timestamp") or ""),
                ]
            )
        )
        sequence = int(record.get("stream_sequence") or index + 1)
        key = (target, message_identity)
        previous = structured.get(key)
        if previous is None or sequence >= previous[0]:
            structured[key] = (sequence, record)
    ordered_structured = [
        item[1]
        for item in sorted(
            structured.values(),
            key=lambda item: (
                str(item[1].get("target_key") or item[1].get("stream_id") or ""),
                item[0],
            ),
        )
    ]
    return [*_latest_by_target(legacy), *ordered_structured]


def _extract_topics(text: str) -> list[str]:
    candidates = [
        *(match.group(1).strip() for match in _WIKILINK_PATTERN.finditer(text)),
        *(match.group(1).strip() for match in _HASHTAG_PATTERN.finditer(text)),
    ]
    topics: dict[str, str] = {}
    for candidate in candidates:
        candidate = candidate.rstrip("./-")
        if not candidate:
            continue
        topics.setdefault(candidate.casefold(), candidate)
    return sorted(topics.values(), key=str.casefold)


def _is_person_candidate(label: str) -> bool:
    candidate = label.strip()
    if not candidate or len(candidate) > 40:
        return False
    if re.fullmatch(r"[\d\s:/.+\-]+", candidate):
        return False
    if candidate.casefold() in {"http", "https"}:
        return False
    if re.match(r"(?i)^(?:https?://|www\.)", candidate):
        return False
    if candidate.startswith(("#", "[[", "-", "*", ">")):
        return False
    return any(
        unicodedata.category(char).startswith("L")
        or unicodedata.category(char) == "So"
        for char in candidate
    )


def _wikilink_alias(value: str) -> str:
    return value.replace("|", "¦").replace("]", "］")


def _time_key(value: str) -> tuple[int, datetime, str]:
    if not value:
        return (0, datetime.min.replace(tzinfo=timezone.utc), "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (1, parsed.astimezone(timezone.utc), value)
    except ValueError:
        return (0, datetime.min.replace(tzinfo=timezone.utc), value)


def _latest_observed_at(events: Iterable[dict[str, str]]) -> str:
    values = [event["observed_at"] for event in events if event["observed_at"]]
    return max(values, key=_time_key) if values else "unknown"


def _parse_knowledge_events(
    text: str, *, source: str, observed_at: str
) -> list[DiscordEvent]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    events: list[DiscordEvent] = []
    current_author = ""
    pending: list[str] = []

    def flush() -> None:
        nonlocal pending
        if not current_author or not pending:
            pending = []
            return
        events.append(
            DiscordEvent.from_dict(
                {
                    "observed_at": observed_at,
                    "source": source,
                    "guild_label": "private-local",
                    "channel_label": "knowledge-projection",
                    "author_label": current_author,
                    "text_snippet": " ".join(pending),
                    "confidence": "visible",
                    "private_surface": True,
                }
            )
        )
        pending = []

    index = 0
    while index < len(lines):
        line = TIMESTAMP_RE.sub("", lines[index]).strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        author_with_timestamp = AUTHOR_WITH_TIMESTAMP_RE.match(line)
        if author_with_timestamp:
            candidate = author_with_timestamp.group("author").strip()
            if not _is_person_candidate(candidate):
                index += 1
                continue
            flush()
            current_author = candidate
            index += 1
            continue
        if next_line and TIMESTAMP_METADATA_RE.match(next_line):
            if not _is_person_candidate(line):
                index += 2
                continue
            flush()
            current_author = line
            index += 2
            continue
        colon_message = COLON_MESSAGE_RE.match(line)
        if colon_message:
            candidate = colon_message.group("author").strip()
            if not _is_person_candidate(candidate):
                if current_author:
                    pending.append(line)
                index += 1
                continue
            flush()
            current_author = candidate
            pending.append(colon_message.group("text").strip())
            index += 1
            continue
        if TIMESTAMP_METADATA_RE.match(line):
            index += 1
            continue
        if current_author:
            pending.append(line)
        index += 1
    flush()
    return events


def _write_if_changed(path: Path, content: str) -> str:
    encoded = content.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as temporary:
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
    return "written"


def _ensure_human_note(path: Path, content: str) -> str:
    if path.exists():
        return "preserved"
    return _write_if_changed(path, content)


def _project_file(
    path: Path,
    content: str,
    *,
    dry_run: bool,
    preserve_existing: bool = False,
) -> str:
    if dry_run:
        if preserve_existing and path.exists():
            return "preserved"
        if path.exists() and path.read_bytes() == content.encode("utf-8"):
            return "unchanged"
        return "planned"
    if preserve_existing:
        return _ensure_human_note(path, content)
    return _write_if_changed(path, content)


def _remove_stale_generated(
    directory: Path, active_names: set[str], *, dry_run: bool = False
) -> int:
    removed = 0
    if not directory.exists():
        return removed
    for path in directory.glob("*.generated.md"):
        if path.name in active_names:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if GENERATED_MARKER not in content:
            continue
        if not dry_run:
            path.unlink()
        removed += 1
    return removed


def _render_event(event: dict[str, str]) -> list[str]:
    topics = event["topics"].split("\0") if event["topics"] else []
    topic_links = ", ".join(f"[[../Topics/{topic_id}.generated|{_wikilink_alias(label)}]]" for topic_id, label in (
        topic.split("\t", 1) for topic in topics
    ))
    return [
        f"### {event['observed_at'] or 'unknown'}",
        "",
        event["text"],
        "",
        f"- 話題: {topic_links or '未分類'}",
        f"- actor: `{event['person_id']}`",
        "- event_time: `unknown`",
        f"- observed_at: `{event['observed_at'] or 'unknown'}`",
        f"- source: `{event['source']}`",
        f"- source_stream_ref: `{event['stream_ref']}`",
        f"- 観測ID: `{event['event_id']}`",
        "",
    ]


def _render_person(
    *, person_id: str, label: str, events: list[dict[str, str]]
) -> str:
    notes_name = f"{person_id}.notes"
    recorded_at = _latest_observed_at(events)
    lines = [
        "---",
        f"title: {_yaml_string(label)}",
        "type: person-timeline",
        GENERATED_MARKER,
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}",
        "schema_version: knowledge-wiki/v1",
        f"recorded_at: {_yaml_string(recorded_at)}",
        "recorded_by: dcb-knowledge-projector",
        f"person_id: {_yaml_string(person_id)}",
        f"event_count: {len(events)}",
        "private_local_only: true",
        "---",
        "",
        f"# {label}",
        "",
        "> [!info] 自動投影",
        "> DCBの最新観測から生成した人物タイムラインです。人物同一性は未確認です。",
        "",
        f"- 人間メモ: [[{notes_name}]]",
        "",
        "## タイムライン",
        "",
    ]
    for event in sorted(events, key=lambda item: _time_key(item["observed_at"]), reverse=True):
        lines.extend(_render_event(event))
    return "\n".join(lines).rstrip() + "\n"


def _render_topic(
    *,
    topic_id: str,
    label: str,
    events: list[dict[str, str]],
    people: dict[str, str],
) -> str:
    recorded_at = _latest_observed_at(events)
    person_links = "\n".join(
        f"- [[../People/{person_id}.generated|{_wikilink_alias(people[person_id])}]]"
        for person_id in sorted({event["person_id"] for event in events})
    )
    lines = [
        "---",
        f"title: {_yaml_string(label)}",
        "type: topic-wiki",
        GENERATED_MARKER,
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}",
        "schema_version: knowledge-wiki/v1",
        f"recorded_at: {_yaml_string(recorded_at)}",
        "recorded_by: dcb-knowledge-projector",
        f"topic_id: {_yaml_string(topic_id)}",
        f"event_count: {len(events)}",
        "private_local_only: true",
        "---",
        "",
        f"# {label}",
        "",
        "> [!info] 自動投影",
        "> 明示的なハッシュタグまたはWikiリンクだけから生成しています。",
        "",
        f"- 人間メモ: [[{topic_id}.notes]]",
        "",
        "## 関連人物",
        "",
        person_links or "- まだありません",
        "",
        "## イベント",
        "",
    ]
    for event in sorted(events, key=lambda item: _time_key(item["observed_at"]), reverse=True):
        lines.extend(
            [
                f"### {event['observed_at'] or 'unknown'} — {people[event['person_id']]}",
                "",
                event["text"],
                "",
                f"- actor: `{event['person_id']}`",
                "- event_time: `unknown`",
                f"- observed_at: `{event['observed_at'] or 'unknown'}`",
                f"- source: `{event['source']}`",
                f"- source_stream_ref: `{event['stream_ref']}`",
                f"- 観測ID: `{event['event_id']}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_generated_top(
    *,
    people: dict[str, str],
    topics: dict[str, str],
    event_count: int,
    unclassified_count: int,
    recorded_at: str,
) -> str:
    person_links = "\n".join(
        f"- [[People/{person_id}.generated|{_wikilink_alias(label)}]]"
        for person_id, label in sorted(people.items(), key=lambda item: item[1].casefold())
    )
    topic_links = "\n".join(
        f"- [[Topics/{topic_id}.generated|{_wikilink_alias(label)}]]"
        for topic_id, label in sorted(topics.items(), key=lambda item: item[1].casefold())
    )
    return (
        "---\n"
        'title: "Knowledge TOP 自動一覧"\n'
        "type: knowledge-top-projection\n"
        f"{GENERATED_MARKER}\n"
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}\n"
        "schema_version: knowledge-wiki/v1\n"
        f"recorded_at: {_yaml_string(recorded_at or 'unknown')}\n"
        "recorded_by: dcb-knowledge-projector\n"
        f"event_count: {event_count}\n"
        f"unclassified_event_count: {unclassified_count}\n"
        "private_local_only: true\n"
        "---\n\n"
        "# Knowledge TOP 自動一覧\n\n"
        "> [!info] 自動投影\n"
        "> この一覧は再生成されます。意味付けや重要導線は `Knowledge TOP.md` に記述します。\n\n"
        "## 人物\n\n"
        f"{person_links or '- まだありません'}\n\n"
        "## 話題\n\n"
        f"{topic_links or '- まだありません'}\n\n"
        "## 要確認\n\n"
        f"- 話題未分類イベント: {unclassified_count}\n"
    )


def _render_review_queue(
    *, unclassified_count: int, person_count: int, recorded_at: str
) -> str:
    return (
        "---\n"
        'title: "Review Queue 自動一覧"\n'
        "type: knowledge-review-queue-projection\n"
        f"{GENERATED_MARKER}\n"
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}\n"
        "schema_version: knowledge-wiki/v1\n"
        f"recorded_at: {_yaml_string(recorded_at or 'unknown')}\n"
        "recorded_by: dcb-knowledge-projector\n"
        f"review_item_count: {unclassified_count + person_count}\n"
        "private_local_only: true\n"
        "---\n\n"
        "# Review Queue 自動一覧\n\n"
        "> [!warning] 人間レビュー境界\n"
        "> 人物統合、話題改名、fact昇格は自動確定しません。\n\n"
        "## 人物同一性\n\n"
        f"- 未確認の人物候補: {person_count}\n\n"
        "## 話題ガバナンス\n\n"
        f"- 話題未分類イベント: {unclassified_count}\n\n"
        "## 事実・推論・不明\n\n"
        "- 判断を残す場合は `Templates/Review Decision.md` を使用します。\n"
    )


def _templater_files() -> dict[str, str]:
    return {
        "Person Notes.md": (
            "---\n"
            'title: "<% tp.file.title %>"\n'
            "type: person-notes\n"
            "relationship_status: unknown\n"
            "identity_status: unverified\n"
            "private_local_only: true\n"
            "---\n\n"
            "# <% tp.file.title %>\n\n"
            "## 関係性\n\n"
            "## 重要な文脈\n\n"
            "## 確認事項\n"
        ),
        "Topic Notes.md": (
            "---\n"
            'title: "<% tp.file.title %>"\n'
            "type: topic-notes\n"
            "topic_status: active\n"
            "private_local_only: true\n"
            "---\n\n"
            "# <% tp.file.title %>\n\n"
            "## 意味\n\n"
            "## 重要な結論\n\n"
            "## 読む順番\n"
        ),
        "Review Decision.md": (
            "---\n"
            'title: "<% tp.file.title %>"\n'
            "type: knowledge-review-decision\n"
            "decision: hold\n"
            "classification: unknown\n"
            "recorded_at: <% tp.date.now(\"YYYY-MM-DDTHH:mm:ssZ\") %>\n"
            "recorded_by: human\n"
            "private_local_only: true\n"
            "---\n\n"
            "# <% tp.file.title %>\n\n"
            "分類: fact / non_fact / unknown\n\n"
            "## 根拠\n\n"
            "## 判断\n\n"
            "## 次の確認\n"
        ),
    }


def export_knowledge_projection(
    *, snapshot_store: Path, output_root: Path, dry_run: bool = False
) -> dict[str, Any]:
    started = time.perf_counter()
    if not snapshot_store.is_file():
        return {
            "schema": PROJECTION_SCHEMA,
            "ok": False,
            "reason": "snapshot_store_missing",
            "message": "snapshot台帳を確認できないため投影を中止しました。",
            "outbound_actions": "disabled",
            "private_local_only": True,
            "paths_returned": False,
            "dry_run": dry_run,
        }
    records = load_text_snapshots(snapshot_store)
    latest_records = _projection_records(records)
    people: dict[str, str] = {}
    topics: dict[str, str] = {}
    person_events: dict[str, list[dict[str, str]]] = defaultdict(list)
    topic_events: dict[str, list[dict[str, str]]] = defaultdict(list)
    statuses: list[str] = []
    unclassified_count = 0

    for record in latest_records:
        target = str(record.get("target_key") or record.get("stream_id") or "unknown")
        stream_ref = _stable_id("stream", target)
        observed_at = str(
            record.get("captured_at")
            or record.get("observed_at")
            or record.get("time")
            or ""
        )
        source = str(record.get("source") or "unknown")
        content_hash = str(record.get("content_hash") or "")
        if record.get("event_type") == "message_observation":
            parsed_events = [
                DiscordEvent.from_dict(
                    {
                        "observed_at": observed_at,
                        "source": source,
                        "guild_label": "private-local",
                        "channel_label": "knowledge-projection",
                        "author_label": str(record.get("author_label") or "unknown"),
                        "text_snippet": str(record.get("text") or ""),
                        "confidence": "structured",
                        "private_surface": True,
                    }
                )
            ]
        else:
            parsed_events = _parse_knowledge_events(
                str(record.get("text") or ""),
                source=source,
                observed_at=observed_at,
            )
        for index, parsed in enumerate(parsed_events, start=1):
            person_label = parsed.author_label.strip() or "unknown"
            person_id = _stable_id(
                "person", stream_ref, person_label.casefold()
            )
            people.setdefault(person_id, person_label)
            topic_labels = _extract_topics(parsed.text_snippet)
            topic_pairs: list[tuple[str, str]] = []
            for topic_label in topic_labels:
                topic_id = _stable_id("topic", topic_label.casefold())
                topics.setdefault(topic_id, topic_label)
                topic_pairs.append((topic_id, topic_label))
            if not topic_pairs:
                unclassified_count += 1
            event = {
                "event_id": _stable_id(
                    "event", target, content_hash, str(index), person_id
                ),
                "observed_at": observed_at,
                "source": source,
                "person_id": person_id,
                "stream_ref": stream_ref,
                "text": parsed.text_snippet,
                "topics": "\0".join(
                    f"{topic_id}\t{topic_label}"
                    for topic_id, topic_label in topic_pairs
                ),
            }
            person_events[person_id].append(event)
            for topic_id, _ in topic_pairs:
                topic_events[topic_id].append(event)

    for person_id, events in person_events.items():
        statuses.append(
            _project_file(
                output_root / "People" / f"{person_id}.generated.md",
                _render_person(
                    person_id=person_id,
                    label=people[person_id],
                    events=events,
                ),
                dry_run=dry_run,
            )
        )
        statuses.append(
            _project_file(
                output_root / "People" / f"{person_id}.notes.md",
                (
                    "---\n"
                    f"title: {_yaml_string(f'{people[person_id]} 人間メモ')}\n"
                    "type: person-notes\n"
                    f"person_id: {_yaml_string(person_id)}\n"
                    "private_local_only: true\n"
                    "---\n\n"
                    f"# {people[person_id]} 人間メモ\n\n"
                    "人物同一性、関係性、重要な文脈を人間が記述します。\n"
                ),
                dry_run=dry_run,
                preserve_existing=True,
            )
        )

    for topic_id, events in topic_events.items():
        statuses.append(
            _project_file(
                output_root / "Topics" / f"{topic_id}.generated.md",
                _render_topic(
                    topic_id=topic_id,
                    label=topics[topic_id],
                    events=events,
                    people=people,
                ),
                dry_run=dry_run,
            )
        )
        statuses.append(
            _project_file(
                output_root / "Topics" / f"{topic_id}.notes.md",
                (
                    "---\n"
                    f"title: {_yaml_string(f'{topics[topic_id]} 話題メモ')}\n"
                    "type: topic-notes\n"
                    f"topic_id: {_yaml_string(topic_id)}\n"
                    "private_local_only: true\n"
                    "---\n\n"
                    f"# {topics[topic_id]} 話題メモ\n\n"
                    "話題の意味、重要な結論、読む順番を人間が記述します。\n"
                ),
                dry_run=dry_run,
                preserve_existing=True,
            )
        )

    removed_file_count = _remove_stale_generated(
        output_root / "People",
        {f"{person_id}.generated.md" for person_id in people},
        dry_run=dry_run,
    )
    removed_file_count += _remove_stale_generated(
        output_root / "Topics",
        {f"{topic_id}.generated.md" for topic_id in topics},
        dry_run=dry_run,
    )

    statuses.append(
        _project_file(
            output_root / "Knowledge TOP.generated.md",
            _render_generated_top(
                people=people,
                topics=topics,
                event_count=sum(len(events) for events in person_events.values()),
                unclassified_count=unclassified_count,
                recorded_at=_latest_observed_at(
                    event
                    for events in person_events.values()
                    for event in events
                ),
            ),
            dry_run=dry_run,
        )
    )
    statuses.append(
        _project_file(
            output_root / "Knowledge TOP.md",
            (
                "---\n"
                'title: "Knowledge TOP"\n'
                "type: knowledge-top\n"
                "private_local_only: true\n"
                "---\n\n"
                "# Knowledge TOP\n\n"
                "このページの説明と重要導線は人間が編集します。\n\n"
                "## 自動一覧\n\n"
                "![[Knowledge TOP.generated]]\n"
            ),
            dry_run=dry_run,
            preserve_existing=True,
        )
    )
    recorded_at = _latest_observed_at(
        event
        for events in person_events.values()
        for event in events
    )
    statuses.append(
        _project_file(
            output_root / "Review Queue.generated.md",
            _render_review_queue(
                unclassified_count=unclassified_count,
                person_count=len(people),
                recorded_at=recorded_at,
            ),
            dry_run=dry_run,
        )
    )
    statuses.append(
        _project_file(
            output_root / "Review Queue.md",
            (
                "---\n"
                'title: "Review Queue"\n'
                "type: knowledge-review-queue\n"
                "private_local_only: true\n"
                "---\n\n"
                "# Review Queue\n\n"
                "人間が判断する項目と判断記録への導線を編集します。\n\n"
                "## 自動一覧\n\n"
                "![[Review Queue.generated]]\n"
            ),
            dry_run=dry_run,
            preserve_existing=True,
        )
    )
    for template_name, template_content in _templater_files().items():
        statuses.append(
            _project_file(
                output_root / "Templates" / template_name,
                template_content,
                dry_run=dry_run,
                preserve_existing=True,
            )
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "schema": PROJECTION_SCHEMA,
        "ok": True,
        "message": "人物・話題・Knowledge TOP projectionを更新しました。",
        "source_record_count": len(records),
        "projected_target_count": len(latest_records),
        "projected_event_count": sum(
            len(events) for events in person_events.values()
        ),
        "projected_person_count": len(people),
        "projected_topic_count": len(topics),
        "unclassified_event_count": unclassified_count,
        "review_item_count": unclassified_count + len(people),
        "written_file_count": statuses.count("written"),
        "planned_file_count": statuses.count("planned"),
        "unchanged_file_count": statuses.count("unchanged"),
        "removed_stale_generated_file_count": removed_file_count,
        "planned_stale_generated_file_count": (
            removed_file_count if dry_run else 0
        ),
        "human_notes_preserved": True,
        "outbound_actions": "disabled",
        "private_local_only": True,
        "paths_returned": False,
        "dry_run": dry_run,
        "elapsed_ms": round(elapsed_ms, 3),
    }
