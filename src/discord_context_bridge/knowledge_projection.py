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
_REVIEW_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_OBSERVATION_ID_PATTERN = re.compile(r"^observation-[a-f0-9]{12}$")


def _yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _load_person_registry(
    path: Path | None,
) -> tuple[dict[str, tuple[str, str]], int]:
    if path is None:
        return {}, 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != "dcb.person_registry.v1"
        or payload.get("private_local_only") is not True
        or not isinstance(payload.get("people"), list)
    ):
        raise ValueError("invalid person registry")
    aliases: dict[str, tuple[str, str]] = {}
    person_labels: dict[str, str] = {}
    for person in payload["people"]:
        person_id = str(person.get("person_id") or "").strip()
        display_label = str(person.get("display_label") or "").strip()
        reviewed_by = str(person.get("reviewed_by") or "").strip()
        reviewed_at = str(person.get("reviewed_at") or "").strip()
        if (
            not _REVIEW_ID_PATTERN.fullmatch(person_id)
            or not display_label
            or not reviewed_by
            or not reviewed_at
        ):
            raise ValueError("unreviewed person registry entry")
        previous_label = person_labels.setdefault(person_id, display_label)
        if previous_label != display_label:
            raise ValueError("conflicting person label")
        for alias in person.get("aliases") or []:
            candidate_id = str(alias or "").strip()
            if not _REVIEW_ID_PATTERN.fullmatch(candidate_id):
                raise ValueError("invalid person alias")
            existing = aliases.get(candidate_id)
            value = (person_id, display_label)
            if existing is not None and existing != value:
                raise ValueError("conflicting person alias")
            aliases[candidate_id] = value
    return aliases, len(aliases)


def _load_topic_registry(
    path: Path | None,
) -> tuple[
    dict[str, tuple[str, str]],
    dict[str, list[str]],
    dict[str, str],
    dict[str, list[str]],
    dict[str, list[str]],
    int,
]:
    if path is None:
        return {}, {}, {}, {}, {}, 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema") != "dcb.topic_assignment_registry.v1"
        or payload.get("private_local_only") is not True
        or not isinstance(payload.get("topics"), list)
        or not isinstance(payload.get("assignments"), list)
    ):
        raise ValueError("invalid topic registry")
    topics: dict[str, tuple[str, str]] = {}
    topic_aliases: dict[str, str] = {}
    broader_topics: dict[str, list[str]] = {}
    related_topics: dict[str, list[str]] = {}
    for topic in payload["topics"]:
        topic_id = str(topic.get("topic_id") or "").strip()
        label = str(topic.get("label") or "").strip()
        if not _REVIEW_ID_PATTERN.fullmatch(topic_id) or not label:
            raise ValueError("invalid topic definition")
        for field in ("aliases", "broader_topic_ids", "related_topic_ids"):
            value = topic.get(field, [])
            if not isinstance(value, list) or len(value) != len(set(value)):
                raise ValueError("invalid topic definition")
        if topic_id in topics:
            raise ValueError("duplicate topic definition")
        topics[topic_id] = (topic_id, label)
        aliases = [label, *(topic.get("aliases") or [])]
        for alias in aliases:
            normalized_alias = str(alias or "").strip().casefold()
            if not normalized_alias:
                raise ValueError("invalid topic alias")
            existing = topic_aliases.get(normalized_alias)
            if existing is not None and existing != topic_id:
                raise ValueError("conflicting topic alias")
            topic_aliases[normalized_alias] = topic_id
        broader_topics[topic_id] = [
            str(value or "").strip()
            for value in topic.get("broader_topic_ids") or []
        ]
        related_topics[topic_id] = [
            str(value or "").strip()
            for value in topic.get("related_topic_ids") or []
        ]
    for topic_id in topics:
        links = [*broader_topics[topic_id], *related_topics[topic_id]]
        if any(link == topic_id or link not in topics for link in links):
            raise ValueError("invalid topic relation")

    def visit(topic_id: str, active: set[str], visited: set[str]) -> None:
        if topic_id in active:
            raise ValueError("cyclic broader topic relation")
        if topic_id in visited:
            return
        active.add(topic_id)
        for parent_id in broader_topics[topic_id]:
            visit(parent_id, active, visited)
        active.remove(topic_id)
        visited.add(topic_id)

    visited_topics: set[str] = set()
    for topic_id in topics:
        visit(topic_id, set(), visited_topics)
    assignments: dict[str, list[str]] = {}
    for assignment in payload["assignments"]:
        observation_id = str(assignment.get("observation_id") or "").strip()
        topic_ids = assignment.get("topic_ids")
        reviewed_by = str(assignment.get("reviewed_by") or "").strip()
        reviewed_at = str(assignment.get("reviewed_at") or "").strip()
        if (
            not _OBSERVATION_ID_PATTERN.fullmatch(observation_id)
            or not isinstance(topic_ids, list)
            or not topic_ids
            or not reviewed_by
            or not reviewed_at
        ):
            raise ValueError("unreviewed topic assignment")
        normalized = [str(topic_id).strip() for topic_id in topic_ids]
        if any(not topic_id or topic_id not in topics for topic_id in normalized):
            raise ValueError("unknown assigned topic")
        if (
            observation_id in assignments
            and assignments[observation_id] != normalized
        ):
            raise ValueError("conflicting topic assignment")
        assignments[observation_id] = normalized
    return (
        topics,
        assignments,
        topic_aliases,
        broader_topics,
        related_topics,
        len(assignments),
    )


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


def _structured_message_identity(record: dict[str, Any]) -> str:
    return str(
        record.get("message_id")
        or record.get("event_id")
        or record.get("stream_sequence")
        or "|".join(
            [
                str(record.get("ordinal") or ""),
                str(record.get("author_label") or ""),
                str(record.get("visible_timestamp") or ""),
            ]
        )
    )


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
    # 日時表記は人物ではない。日本語の日時 (`2026年6月30日火曜日 22:32` 等) は
    # 曜日漢字を含むため上の数字クラス判定を素通りする。呼び出し元の分岐順序に
    # 依存せず弾けるよう、候補判定そのものに置く。
    if TIMESTAMP_METADATA_RE.match(candidate):
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
        # 日時行そのものは発言者行ではない。以降のどの分岐 (発言者+日時 / 次行が日時 /
        # `発言者: 本文`) よりも先に落とす。後ろに置くと、時刻のコロンが区切りと誤認されたり
        # (`2026年6月30日火曜日 22:32` -> `2026年6月30日火曜日 22`)、次行 lookahead により
        # 日時行自体が発言者名として採用される。
        if TIMESTAMP_METADATA_RE.match(line):
            index += 1
            continue
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
    topics: dict[str, str],
    broader_topic_ids: list[str],
    narrower_topic_ids: list[str],
    related_topic_ids: list[str],
) -> str:
    recorded_at = _latest_observed_at(events)
    person_links = "\n".join(
        f"- [[../People/{person_id}.generated|{_wikilink_alias(people[person_id])}]]"
        for person_id in sorted({event["person_id"] for event in events})
    )

    def topic_links(topic_ids: list[str]) -> str:
        return "\n".join(
            f"- [[{linked_id}.generated|{_wikilink_alias(topics[linked_id])}]]"
            for linked_id in topic_ids
        ) or "- まだありません"

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
        "> 明示タグ、Wikiリンク、または人間レビュー済み台帳から生成しています。",
        "",
        f"- 人間メモ: [[{topic_id}.notes]]",
        "",
        "## 上位の題",
        "",
        topic_links(broader_topic_ids),
        "",
        "## 下位の題",
        "",
        topic_links(narrower_topic_ids),
        "",
        "## 関連する題",
        "",
        topic_links(related_topic_ids),
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
    *,
    unclassified_events: list[dict[str, str]],
    people: dict[str, str],
    recorded_at: str,
) -> str:
    unclassified_count = len(unclassified_events)
    person_count = len(people)
    person_items = "\n".join(
        f"- `{person_id}` — {_wikilink_alias(label)}"
        for person_id, label in sorted(
            people.items(), key=lambda item: item[1].casefold()
        )
    )
    event_items = "\n".join(
        f"- `{event['observation_id']}` — actor `{event['person_id']}` / "
        f"observed_at `{event['observed_at'] or 'unknown'}`"
        for event in unclassified_events
    )
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
        f"{person_items or '- ありません'}\n\n"
        "## 話題未分類イベント\n\n"
        f"- 件数: {unclassified_count}\n\n"
        f"{event_items or '- ありません'}\n\n"
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
    *,
    snapshot_store: Path,
    output_root: Path,
    dry_run: bool = False,
    person_registry: Path | None = None,
    topic_registry: Path | None = None,
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
    person_aliases, reviewed_person_alias_count = _load_person_registry(
        person_registry
    )
    (
        reviewed_topics,
        topic_assignments,
        topic_aliases,
        broader_topics,
        related_topics,
        reviewed_topic_assignment_count,
    ) = _load_topic_registry(topic_registry)
    latest_records = _projection_records(records)
    people: dict[str, str] = {}
    topics: dict[str, str] = {
        topic_id: label for topic_id, (_, label) in reviewed_topics.items()
    }
    person_events: dict[str, list[dict[str, str]]] = defaultdict(list)
    topic_events: dict[str, list[dict[str, str]]] = defaultdict(list)
    statuses: list[str] = []
    unclassified_count = 0
    unclassified_events: list[dict[str, str]] = []
    unreviewed_people: dict[str, str] = {}

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
            default_person = (
                _stable_id("person", stream_ref, person_label.casefold()),
                person_label,
            )
            candidate_person_id = default_person[0]
            if candidate_person_id not in person_aliases:
                unreviewed_people.setdefault(candidate_person_id, person_label)
            person_id, person_label = person_aliases.get(
                candidate_person_id, default_person
            )
            people.setdefault(person_id, person_label)
            topic_labels = _extract_topics(parsed.text_snippet)
            topic_pairs: list[tuple[str, str]] = []
            for topic_label in topic_labels:
                topic_id = topic_aliases.get(topic_label.casefold())
                if topic_id is None:
                    topic_id = _stable_id("topic", topic_label.casefold())
                    canonical_label = topic_label
                else:
                    _, canonical_label = reviewed_topics[topic_id]
                topics.setdefault(topic_id, canonical_label)
                if (topic_id, canonical_label) not in topic_pairs:
                    topic_pairs.append((topic_id, canonical_label))
            observation_parts = [target, content_hash, str(index)]
            if record.get("event_type") == "message_observation":
                observation_parts.append(_structured_message_identity(record))
            observation_id = _stable_id("observation", *observation_parts)
            for topic_id in topic_assignments.get(observation_id, []):
                _, topic_label = reviewed_topics[topic_id]
                topics.setdefault(topic_id, topic_label)
                if (topic_id, topic_label) not in topic_pairs:
                    topic_pairs.append((topic_id, topic_label))
            event = {
                "event_id": _stable_id(
                    "event", target, content_hash, str(index), person_id
                ),
                "observation_id": observation_id,
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
            if not topic_pairs:
                unclassified_count += 1
                unclassified_events.append(event)
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

    narrower_topics: dict[str, list[str]] = defaultdict(list)
    for topic_id, parent_ids in broader_topics.items():
        for parent_id in parent_ids:
            narrower_topics[parent_id].append(topic_id)
    symmetric_related: dict[str, set[str]] = defaultdict(set)
    for topic_id, linked_ids in related_topics.items():
        for linked_id in linked_ids:
            symmetric_related[topic_id].add(linked_id)
            symmetric_related[linked_id].add(topic_id)

    for topic_id in topics:
        events = topic_events.get(topic_id, [])
        statuses.append(
            _project_file(
                output_root / "Topics" / f"{topic_id}.generated.md",
                _render_topic(
                    topic_id=topic_id,
                    label=topics[topic_id],
                    events=events,
                    people=people,
                    topics=topics,
                    broader_topic_ids=broader_topics.get(topic_id, []),
                    narrower_topic_ids=sorted(narrower_topics.get(topic_id, [])),
                    related_topic_ids=sorted(symmetric_related.get(topic_id, set())),
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
                unclassified_events=unclassified_events,
                people=unreviewed_people,
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
        "review_item_count": unclassified_count + len(unreviewed_people),
        "reviewed_person_alias_count": reviewed_person_alias_count,
        "reviewed_topic_assignment_count": reviewed_topic_assignment_count,
        "reviewed_topic_alias_count": max(0, len(topic_aliases) - len(reviewed_topics)),
        "topic_relation_count": sum(len(values) for values in broader_topics.values())
        + len(
            {
                tuple(sorted((left, right)))
                for left, values in symmetric_related.items()
                for right in values
            }
        ),
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
