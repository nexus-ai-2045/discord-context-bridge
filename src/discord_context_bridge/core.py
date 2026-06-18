from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STORE = Path(".local/discord-context-bridge/events.ndjson")
DEFAULT_LANGUAGE = "ja"
TIMESTAMP_RE = re.compile(r"^(?:\[\d{1,2}:\d{2}\]|\d{1,2}:\d{2})\s*")
COLON_MESSAGE_RE = re.compile(r"^(?P<author>[^:\n]{1,80}):\s*(?P<text>.+)$")


class DisabledCapability(RuntimeError):
    """Raised when a deliberately disabled external action is requested."""


@dataclass(frozen=True)
class DiscordEvent:
    observed_at: str
    source: str
    guild_label: str
    channel_label: str
    author_label: str
    text_snippet: str
    actions_allowed: list[str] = field(default_factory=lambda: ["read"])
    private_surface: bool = True
    confidence: str = "visible"
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            identity_payload = self.to_dict(include_id=False)
            identity_payload.pop("observed_at", None)
            object.__setattr__(self, "event_id", stable_event_id(identity_payload))
        if self.actions_allowed != ["read"]:
            raise ValueError("public nucleus only supports read-only events")

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "observed_at": self.observed_at,
            "source": self.source,
            "guild_label": self.guild_label,
            "channel_label": self.channel_label,
            "author_label": self.author_label,
            "text_snippet": self.text_snippet,
            "actions_allowed": self.actions_allowed,
            "private_surface": self.private_surface,
            "confidence": self.confidence,
        }
        if include_id:
            payload["event_id"] = self.event_id
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscordEvent":
        return cls(
            observed_at=str(payload.get("observed_at") or utc_now()),
            source=str(payload.get("source") or "visible_text"),
            guild_label=str(payload.get("guild_label") or "example-community"),
            channel_label=str(payload.get("channel_label") or "general"),
            author_label=str(payload.get("author_label") or "unknown"),
            text_snippet=str(payload.get("text_snippet") or ""),
            actions_allowed=list(payload.get("actions_allowed") or ["read"]),
            private_surface=bool(payload.get("private_surface", True)),
            confidence=str(payload.get("confidence") or "visible"),
            event_id=str(payload.get("event_id") or ""),
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_event_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def load_events(path: Path = DEFAULT_STORE) -> list[DiscordEvent]:
    if not path.exists():
        return []
    events: list[DiscordEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(DiscordEvent.from_dict(json.loads(line)))
    return events


def append_event(event: DiscordEvent, path: Path = DEFAULT_STORE) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {item.event_id for item in load_events(path)}
    if event.event_id in existing:
        return False
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return True


def append_events(events: Iterable[DiscordEvent], path: Path = DEFAULT_STORE) -> dict[str, int]:
    appended = 0
    duplicate = 0
    for event in events:
        if append_event(event, path):
            appended += 1
        else:
            duplicate += 1
    return {"appended": appended, "duplicate": duplicate}


def looks_like_author_line(line: str) -> bool:
    if len(line) > 40:
        return False
    if line.endswith((".", "?", "!", "。", "？", "！")):
        return False
    return bool(re.search(r"[A-Za-z一-龥ぁ-んァ-ン0-9]", line))


def parse_visible_text(
    text: str,
    *,
    guild_label: str = "example-community",
    channel_label: str = "general",
    source: str = "visible_text",
    observed_at: str | None = None,
) -> list[DiscordEvent]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    events: list[DiscordEvent] = []
    current_author = "unknown"
    pending: list[str] = []

    def flush() -> None:
        nonlocal pending
        if pending:
            events.append(
                DiscordEvent.from_dict(
                    {
                        "observed_at": observed_at or utc_now(),
                        "source": source,
                        "guild_label": guild_label,
                        "channel_label": channel_label,
                        "author_label": current_author,
                        "text_snippet": " ".join(pending),
                        "confidence": "visible",
                        "private_surface": True,
                    }
                )
            )
            pending = []

    for raw_line in lines:
        line = TIMESTAMP_RE.sub("", raw_line).strip()
        match = COLON_MESSAGE_RE.match(line)
        if match:
            flush()
            current_author = match.group("author").strip()
            pending.append(match.group("text").strip())
            continue
        if looks_like_author_line(line):
            flush()
            current_author = line
            continue
        pending.append(line)
    flush()
    return events


def import_visible_text(
    text: str,
    *,
    path: Path = DEFAULT_STORE,
    guild_label: str = "example-community",
    channel_label: str = "general",
) -> dict[str, Any]:
    observed_at = utc_now()
    events = parse_visible_text(
        text,
        guild_label=guild_label,
        channel_label=channel_label,
        observed_at=observed_at,
    )
    result = append_events(events, path)
    return {
        **result,
        "language": DEFAULT_LANGUAGE,
        "parsed": len(events),
        "store": str(path),
        "briefing": fast_briefing(load_events(path)),
    }


def search_events(events: Iterable[DiscordEvent], query: str) -> list[DiscordEvent]:
    needle = query.casefold()
    return [
        event
        for event in events
        if needle in event.text_snippet.casefold()
        or needle in event.author_label.casefold()
        or needle in event.channel_label.casefold()
    ]


def fast_briefing(events: Iterable[DiscordEvent], limit: int = 3) -> dict[str, Any]:
    started = time.perf_counter()
    latest = list(events)[-limit:]
    return {
        "language": DEFAULT_LANGUAGE,
        "event_count": len(latest),
        "channels": sorted({event.channel_label for event in latest}),
        "authors": [event.author_label for event in latest],
        "briefing": " / ".join(event.text_snippet for event in latest),
        "partial": True,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def check_knowledge_gap(user_understanding: str, events: Iterable[DiscordEvent]) -> dict[str, Any]:
    context = fast_briefing(events)
    text = user_understanding.casefold()
    missing: list[str] = []
    if "premise" not in text and "前提" not in text:
        missing.append("共有前提")
    if "reply" not in text and "返信" not in text:
        missing.append("返信対象")
    return {
        "language": DEFAULT_LANGUAGE,
        "context_drift_warning": bool(missing),
        "knowledge_gap": missing,
        "recommended_briefing": context["briefing"] or "直近の文脈はまだ取り込まれていません。",
    }


def review_reply_intent(draft: str, events: Iterable[DiscordEvent]) -> dict[str, Any]:
    loaded = list(events)
    gap = check_knowledge_gap(draft, loaded)
    ok_to_reply = "ask_first" if gap["knowledge_gap"] else "likely_ok"
    return {
        "language": DEFAULT_LANGUAGE,
        "ok_to_reply": ok_to_reply,
        "alignment": "minor_gap" if gap["knowledge_gap"] else "aligned",
        "missing_knowledge": gap["knowledge_gap"],
        "likely_counterparty_meaning": fast_briefing(loaded)["briefing"],
        "suggested_correction": gap["recommended_briefing"] if gap["knowledge_gap"] else "",
    }


def send_message(*_: Any, **__: Any) -> None:
    raise DisabledCapability("Discord への送信機能は、この public nucleus では意図的に無効です。")
