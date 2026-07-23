from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .core import load_text_snapshots, parse_visible_text


PROJECTION_SCHEMA = "discord_context_bridge_obsidian_projection.v1"
GENERATED_MARKER = "dcb_generated: true"


def _safe_component(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (cleaned[:64] or fallback).lower()


def _file_safe_label(value: str, *, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\[\]#^\x00-\x1f]+', "-", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned[:80] or fallback


def _target_label(record: dict[str, Any]) -> str:
    target_key = str(record.get("target_key") or record.get("stream_id") or "")
    if not target_key:
        target_key = hashlib.sha256(
            str(record.get("title") or record.get("content_hash") or "unknown").encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    return f"target-{_safe_component(target_key[:16], fallback='unknown')}"


def _display_label(record: dict[str, Any]) -> str:
    target = _target_label(record)
    title = str(record.get("title") or "").strip()
    title = re.sub(r"(?i)\bvisible[ _-]*snapshot\b", "", title)
    title = re.sub(r"(?i)\blatest[ _-]*\d{6,14}\b", "", title)
    title = re.sub(r"(?i)^target[ _-]*discord[ _-]*thread$", "Discordスレッド", title)
    title = re.sub(r"(?i)free[ _-]*chat", "フリーチャット", title)
    title = re.sub(r"[-_]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return _file_safe_label(
        title, fallback=f"Discord会話-{target.removeprefix('target-')[:8]}"
    )


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _yaml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _block_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"msg-{digest}"


def _latest_by_target(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        target = str(
            record.get("target_key") or record.get("stream_id") or f"unknown-{index}"
        )
        previous = latest.get(target)
        sequence = int(
            record.get("stream_sequence")
            or record.get("observation_index_for_target")
            or index + 1
        )
        if previous is None or sequence >= previous[0]:
            latest[target] = (sequence, record)
    return [item[1] for item in latest.values()]


def _render_month_note(record: dict[str, Any], *, label: str, notes_name: str) -> str:
    captured_at = str(
        record.get("captured_at")
        or record.get("observed_at")
        or record.get("time")
        or ""
    )
    source = str(record.get("source") or "unknown")
    content_hash = str(record.get("content_hash") or "")
    target = _target_label(record)
    text = str(record.get("text") or "").strip()
    events = parse_visible_text(text, source=source, observed_at=captured_at or None)
    status = str(record.get("capture_status") or "partial")
    lines = [
        "---",
        f"title: {_yaml_string(f'{label} 会話ログ')}",
        "tags:",
        "  - discord-context",
        "  - dcb-generated",
        GENERATED_MARKER,
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}",
        f"target_key: {_yaml_string(str(record.get('target_key') or ''))}",
        f"captured_at: {_yaml_string(captured_at)}",
        f"capture_status: {_yaml_string(status)}",
        f"source: {_yaml_string(source)}",
        f"content_hash: {_yaml_string(content_hash)}",
        f"message_count: {len(events)}",
        "private_local_only: true",
        "---",
        "",
        f"# {label} 会話ログ",
        "",
        "> [!warning] 非公開データ",
        "> DCBのローカル観測から生成した読み取り専用ビューです。外部共有しないでください。",
        "",
        f"- 取得状態: `{status}`",
        f"- 取得時刻: `{captured_at or 'unknown'}`",
        f"- 取得経路: `{source}`",
        "- 履歴正本: DCB append-only NDJSON",
        f"- 人間メモ: [[{notes_name}]]",
        "",
        "## メッセージ",
        "",
    ]
    if events:
        for index, event in enumerate(events, start=1):
            block_id = _block_id(
                target, content_hash, str(index), event.author_label, event.text_snippet
            )
            lines.extend(
                [
                    f"### {event.author_label}",
                    "",
                    event.text_snippet,
                    "",
                    f"^{block_id}",
                    "",
                ]
            )
    elif text:
        block_id = _block_id(target, content_hash, "raw")
        lines.extend([text, "", f"^{block_id}", ""])
    else:
        lines.extend(["_保存済み本文がありません。_", ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_channel_index(label: str, month_files: list[str], notes_name: str) -> str:
    links = (
        "\n".join(
            f"- [[{month_file}]]" for month_file in sorted(month_files, reverse=True)
        )
        or "- まだありません"
    )
    return (
        "---\n"
        f"title: {_yaml_string(f'{label} 概要')}\n"
        "tags:\n  - discord-context\n  - dcb-generated\n"
        f"{GENERATED_MARKER}\n"
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}\n"
        "private_local_only: true\n"
        "---\n\n"
        f"# {label} 概要\n\n"
        "## 月別ログ\n\n"
        f"{links}\n\n"
        "## 人間メモ\n\n"
        f"![[{notes_name}]]\n"
    )


def _render_home(entries: list[tuple[str, str]], record_count: int) -> str:
    links = (
        "\n".join(f"- [[{path}|{label}]]" for label, path in sorted(entries))
        or "- まだありません"
    )
    return (
        "---\n"
        'title: "Discord会話マップ"\n'
        "tags:\n  - discord-context\n  - dcb-generated\n"
        f"{GENERATED_MARKER}\n"
        f"projection_schema: {_yaml_string(PROJECTION_SCHEMA)}\n"
        f"projected_target_count: {len(entries)}\n"
        f"source_record_count: {record_count}\n"
        "private_local_only: true\n"
        "---\n\n"
        "# Discord会話マップ\n\n"
        "> [!info] DCB projection\n"
        "> NDJSONを正本とする再生成可能なObsidianビューです。\n\n"
        "## チャンネル\n\n"
        f"{links}\n\n"
        "## 一覧\n\n"
        "![[Views/Discord取得一覧.base]]\n"
    )


def _render_base(folder_name: str) -> str:
    escaped_folder = folder_name.replace('"', '\\"')
    return (
        "filters:\n"
        "  and:\n"
        "    - 'dcb_generated == true'\n"
        f"    - 'file.inFolder(\"{escaped_folder}/Channels\")'\n"
        "views:\n"
        "  - type: table\n"
        '    name: "取得ログ"\n'
        "    order:\n"
        "      - file.name\n"
        "      - capture_status\n"
        "      - captured_at\n"
        "      - message_count\n"
        "      - source\n"
    )


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


def export_obsidian_projection(
    *, snapshot_store: Path, output_root: Path
) -> dict[str, Any]:
    started = time.perf_counter()
    records = load_text_snapshots(snapshot_store)
    latest_records = _latest_by_target(records)
    statuses: list[str] = []
    month_files_by_target: dict[str, list[str]] = defaultdict(list)
    labels_by_target: dict[str, str] = {}
    index_entries: list[tuple[str, str]] = []

    for record in latest_records:
        target = _target_label(record)
        label = _display_label(record)
        labels_by_target[target] = label
        captured = _parse_time(
            record.get("captured_at") or record.get("observed_at") or record.get("time")
        )
        month = captured.strftime("%Y-%m")
        month_name = f"{label} {month}"
        notes_name = f"{label} メモ"
        month_files_by_target[target].append(month_name)
        channel_root = output_root / "Channels" / target
        existing_notes = list(channel_root.glob("* メモ.md"))
        desired_notes = channel_root / f"{notes_name}.md"
        if not desired_notes.exists() and len(existing_notes) == 1:
            existing_notes[0].replace(desired_notes)
        statuses.append(
            _write_if_changed(
                channel_root / f"{month_name}.md",
                _render_month_note(record, label=label, notes_name=notes_name),
            )
        )
        notes = desired_notes
        legacy_notes = channel_root / "Notes.md"
        if legacy_notes.exists() and not notes.exists():
            legacy_notes.replace(notes)
        if not notes.exists():
            statuses.append(
                _write_if_changed(
                    notes,
                    '---\ntitle: "人間メモ"\ntags:\n  - discord-context\n  - human-notes\n---\n\n# 人間メモ\n\n',
                )
            )

    for target, month_files in month_files_by_target.items():
        channel_root = output_root / "Channels" / target
        label = labels_by_target[target]
        notes_name = f"{label} メモ"
        for legacy in channel_root.glob("????-??.md"):
            if GENERATED_MARKER not in legacy.read_text(
                encoding="utf-8", errors="replace"
            ):
                continue
            migrated = channel_root / f"{label} {legacy.stem}.md"
            if not migrated.exists():
                legacy.replace(migrated)
                month_file = migrated
            else:
                month_file = legacy
            if month_file.stem not in month_files:
                month_files.append(month_file.stem)
        for existing in channel_root.glob("*.md"):
            if not re.fullmatch(r"(?:.+ )?\d{4}-\d{2}", existing.stem):
                continue
            if GENERATED_MARKER not in existing.read_text(
                encoding="utf-8", errors="replace"
            ):
                continue
            if existing.stem not in month_files:
                month_files.append(existing.stem)
        index_name = f"{label} 概要"
        statuses.append(
            _write_if_changed(
                channel_root / f"{index_name}.md",
                _render_channel_index(label, month_files, notes_name),
            )
        )
        index_entries.append((label, f"Channels/{target}/{index_name}"))

    statuses.append(
        _write_if_changed(
            output_root / "Views" / "Discord取得一覧.base",
            _render_base(output_root.name),
        )
    )
    statuses.append(
        _write_if_changed(
            output_root / "Discord会話マップ.md",
            _render_home(index_entries, len(records)),
        )
    )
    for legacy in [output_root / "Home.md", output_root / "Views" / "Channels.base"]:
        if not legacy.exists():
            continue
        content = legacy.read_text(encoding="utf-8", errors="replace")
        generated_legacy = GENERATED_MARKER in content
        if legacy.suffix == ".base":
            generated_legacy = content == _render_base(output_root.name)
        if generated_legacy:
            legacy.unlink()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "schema": PROJECTION_SCHEMA,
        "ok": True,
        "message": "Obsidian用Markdown projectionを更新しました。",
        "source_record_count": len(records),
        "projected_target_count": len(latest_records),
        "written_file_count": statuses.count("written"),
        "unchanged_file_count": statuses.count("unchanged"),
        "human_notes_preserved": True,
        "sqlite_used": False,
        "outbound_actions": "disabled",
        "private_local_only": True,
        "paths_returned": False,
        "elapsed_ms": round(elapsed_ms, 3),
    }
