from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STORE = Path(".local/discord-context-bridge/events.ndjson")
DEFAULT_CONTEXT_STORE = Path(".local/discord-context-bridge/context-library.json")
DEFAULT_REVIEW_STORE = Path(".local/discord-context-bridge/review-registry.json")
DEFAULT_TEXT_SNAPSHOT_STORE = Path(".local/discord-context-bridge/text-snapshots.ndjson")
DEFAULT_ATTACHMENT_LEDGER = Path(".local/discord-context-bridge/attachment-ledger.md")
DEFAULT_LANGUAGE = "ja"
TIMESTAMP_RE = re.compile(r"^(?:\[\d{1,2}:\d{2}\]|\d{1,2}:\d{2})\s*")
COLON_MESSAGE_RE = re.compile(r"^(?P<author>[^:\n]{1,80}):\s*(?P<text>.+)$")
AUTHOR_WITH_TIMESTAMP_RE = re.compile(
    r"^(?P<author>.{1,80}?)\s+(?:—|–|-)\s+"
    r"(?:(?:Today|Yesterday) at \d{1,2}:\d{2}\s*(?:AM|PM)?|"
    r"\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?|"
    r"\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})$",
    re.IGNORECASE,
)
TIMESTAMP_METADATA_RE = re.compile(
    r"^(?:(?:Today|Yesterday) at \d{1,2}:\d{2}\s*(?:AM|PM)?|"
    r"\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?|"
    r"\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})$",
    re.IGNORECASE,
)
DISCORD_WEBHOOK_RE = re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d{17,20}/[A-Za-z0-9_-]+")
DISCORD_TOKEN_RE = re.compile(
    r"(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,})"
)
DISCORD_SNOWFLAKE_RE = re.compile(r"(?<!\d)\d{17,20}(?!\d)")
LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?:/Users/[^ \n]+|/home/[^ \n]+|[A-Za-z]:\\[^ \n]+)"
)
TOPIC_KEYWORDS = {
    "公開時期": ("公開時期", "リリース", "launch", "launch timing"),
    "価格": ("価格", "料金", "pricing", "price"),
    "対象読者": ("対象読者", "読者", "audience"),
}
PURPOSE_KEYWORDS = {
    "相談": ("相談", "困って", "help", "question", "質問"),
    "雑談": ("雑談", "ノリ", "しりとり", "遊び", "fun", "chat"),
    "企画": ("企画", "計画", "設計", "strategy", "plan"),
    "告知": ("告知", "お知らせ", "announce", "release"),
}
RULE_KEYWORDS = ("ルール", "禁止", "注意", "ネタバレ", "spoiler", "敬意", "respect", "荒らし", "規約")
SERIOUS_KEYWORDS = ("困って", "相談", "不安", "問題", "障害", "炎上", "注意", "ルール", "禁止")
PLAY_KEYWORDS = ("しりとり", "雑談", "ノリ", "笑", "w", "www", "遊び", "冗談")


class DisabledCapability(RuntimeError):
    """外部送信など、意図的に無効化した機能が呼ばれた時の例外。"""


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
            "source_label": "Discord の可視テキスト",
            "guild_label": self.guild_label,
            "channel_label": self.channel_label,
            "author_label": self.author_label,
            "text_snippet": self.text_snippet,
            "actions_allowed": self.actions_allowed,
            "actions_allowed_label": "読み取りのみ",
            "private_surface": self.private_surface,
            "private_surface_label": "非公開の会話面を含む可能性があります。",
            "confidence": self.confidence,
            "confidence_label": "画面上で見えている範囲",
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


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def target_key_for_url(url: str) -> str:
    return stable_text_hash(url.strip())


def parse_snapshot_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_captured_at(records: Iterable[dict[str, Any]]) -> str:
    latest: datetime | None = None
    latest_text = ""
    for record in records:
        captured_at = record.get("captured_at") or record.get("observed_at") or record.get("timestamp")
        parsed = parse_snapshot_timestamp(captured_at)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
            latest_text = str(captured_at)
    return latest_text


def analyze_discord_forum_url_shape(url: str) -> dict[str, Any]:
    match = re.match(
        r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/channels/"
        r"([^/?#]+)(?:/([^/?#]+))?(?:/([^/?#]+))?(?:/([^/?#]+))?",
        url.strip(),
    )
    if not match:
        return {
            "language": DEFAULT_LANGUAGE,
            "schema": "discord_forum_url_shape.v1",
            "valid_discord_channel_url": False,
            "shape": "invalid",
            "path_id_count": 0,
            "guild_id_present": False,
            "parent_channel_id_present": False,
            "thread_id_present": False,
            "message_id_present": False,
            "blocked_reason": "discord_channel_url_required",
            "same_guild_fuzzy_match_allowed": False,
        }

    guild_id, first_channel_id, thread_or_message_id, message_id = match.groups()
    path_id_count = len([part for part in (guild_id, first_channel_id, thread_or_message_id, message_id) if part])
    blocked_reason = ""
    if path_id_count < 2:
        blocked_reason = "discord_channel_url_required"
    elif path_id_count == 2:
        blocked_reason = "forum_parent_channel_missing"
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_forum_url_shape.v1",
        "valid_discord_channel_url": path_id_count >= 2,
        "shape": {
            1: "guild_only",
            2: "channel_or_thread_without_parent",
            3: "forum_parent_thread_or_message",
            4: "forum_parent_thread_message",
        }.get(path_id_count, "unknown"),
        "path_id_count": path_id_count,
        "guild_id_present": bool(guild_id),
        "parent_channel_id_present": path_id_count >= 3,
        "thread_id_present": path_id_count >= 3,
        "message_id_present": path_id_count >= 4,
        "blocked_reason": blocked_reason,
        "same_guild_fuzzy_match_allowed": False,
    }


def snapshot_freshness(
    records: list[dict[str, Any]],
    *,
    generated_at: str,
    source: str,
    max_age_hours: int = 24,
) -> dict[str, Any]:
    newest_captured_at = latest_captured_at(records)
    if not records:
        return {
            "status": "missing",
            "reason": "no_exact_url_or_target_key_match",
            "source": source,
            "newest_captured_at": "",
            "age_seconds": None,
            "max_age_hours": max_age_hours,
        }

    now = parse_snapshot_timestamp(generated_at) or datetime.now(timezone.utc)
    newest = parse_snapshot_timestamp(newest_captured_at)
    if newest is None:
        return {
            "status": "unknown",
            "reason": "snapshot_timestamp_unparseable",
            "source": source,
            "newest_captured_at": newest_captured_at,
            "age_seconds": None,
            "max_age_hours": max_age_hours,
        }

    age_seconds = max(0, int((now - newest).total_seconds()))
    status = "recent" if age_seconds <= max_age_hours * 3600 else "stale"
    return {
        "status": status,
        "reason": "snapshot_within_recency_window" if status == "recent" else "snapshot_older_than_recency_window",
        "source": source,
        "newest_captured_at": newest_captured_at,
        "age_seconds": age_seconds,
        "max_age_hours": max_age_hours,
    }


def stale_policy_for_freshness(freshness: dict[str, Any]) -> dict[str, Any]:
    is_stale = freshness.get("status") == "stale"
    return {
        "usable_for_reply": not is_stale and freshness.get("status") != "missing",
        "usable_for_routing": freshness.get("status") in {"recent", "stale", "unknown"},
        "required_action": "refresh_exact_url_snapshot" if is_stale else "none",
        "fallback_allowed": "manual_visible_text_or_chrome_extension_only",
        "reason": "stale_snapshot_requires_refresh" if is_stale else str(freshness.get("reason") or ""),
    }


def load_snapshot_like_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            loaded = {"text": line, "line_no": line_no, "source_format": "plain_text"}
        if isinstance(loaded, dict):
            loaded.setdefault("line_no", line_no)
            records.append(loaded)
    return records


def snapshot_record_matches(record: dict[str, Any], *, url: str, target_key: str) -> bool:
    if record.get("url") == url or record.get("target_key") == target_key:
        return True
    return bool(url and url in json.dumps(record, ensure_ascii=False))


def matching_snapshot_records(path: Path, *, url: str, target_key: str) -> list[dict[str, Any]]:
    return [
        record
        for record in load_snapshot_like_records(path)
        if snapshot_record_matches(record, url=url, target_key=target_key)
    ]


def append_snapshot_like_record(path: Path, record: dict[str, Any], *, url: str, target_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in record.items() if key not in {"line_no", "source_format"}}
    payload["url"] = url
    payload["target_key"] = target_key
    payload.setdefault("captured_at", utc_now())
    payload.setdefault("private_local_only", True)
    payload.setdefault("external_share_allowed", False)
    payload.setdefault("outbound_actions", "disabled")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def build_url_intake_gate(
    url: str,
    *,
    raw_cache_path: Path | None,
    ai_log_path: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    target_key: str | None = None,
    sync: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    key = target_key or target_key_for_url(url)
    generated = generated_at or utc_now()
    url_shape = analyze_discord_forum_url_shape(url)
    payload: dict[str, Any] = {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_url_intake_gate.v1",
        "generated_at": generated,
        "message": "Discord URL intake gate を確認しました。",
        "url_output": "omitted",
        "target_key": key,
        "url_shape": url_shape,
        "snapshot_status": "unknown",
        "freshness": snapshot_freshness([], generated_at=generated, source="none"),
        "recency": {
            "status": "missing",
            "reason": "no_exact_url_or_target_key_match",
            "source": "none",
        },
        "raw_cache_checked": "no",
        "ai_log_compared": "no",
        "sync_performed": "no",
        "exact_coverage": "no",
        "fallback_allowed": "no",
        "fallback_policy": "dom_export_or_manual_paste_only",
        "ocr_allowed": False,
        "outbound_actions": "disabled",
        "raw_text_returned": False,
        "state": "blocked",
        "blocked_reason": "",
        "route_failure": "none",
        "paths_output": "omitted",
    }

    if raw_cache_path is None:
        payload["blocked_reason"] = "raw_cache_gate_skipped"
        payload["route_failure"] = "raw_cache_gate_skipped"
        payload["stale_policy"] = stale_policy_for_freshness(payload["freshness"])
        return payload

    payload["raw_cache_checked"] = "yes"
    raw_matches = matching_snapshot_records(raw_cache_path, url=url, target_key=key)
    raw_freshness = snapshot_freshness(raw_matches, generated_at=generated, source="raw_cache")
    payload["raw_cache"] = {"exists": raw_cache_path.exists(), "match_count": len(raw_matches)}

    ai_matches = matching_snapshot_records(ai_log_path, url=url, target_key=key)
    ai_freshness = snapshot_freshness(ai_matches, generated_at=generated, source="ai_log")
    payload["ai_log_compared"] = "yes"
    payload["ai_log"] = {"exists": ai_log_path.exists(), "match_count": len(ai_matches)}

    if raw_matches and ai_matches:
        payload["state"] = "ready"
        payload["exact_coverage"] = "yes"
        payload["sync_performed"] = "not_needed"
        payload["blocked_reason"] = ""
        payload["snapshot_status"] = "ready"
        payload["freshness"] = ai_freshness
        payload["recency"] = {key: ai_freshness[key] for key in ("status", "reason", "source")}
        payload["stale_policy"] = stale_policy_for_freshness(ai_freshness)
        return payload

    if raw_matches and not ai_matches:
        payload["state"] = "ai_log_stale"
        payload["blocked_reason"] = "ai_log_stale"
        payload["snapshot_status"] = "ai_log_stale"
        payload["freshness"] = raw_freshness
        payload["recency"] = {
            "status": raw_freshness["status"],
            "reason": "raw_cache_exact_match_but_ai_log_missing",
            "source": "raw_cache",
        }
        if sync:
            append_snapshot_like_record(ai_log_path, raw_matches[0], url=url, target_key=key)
            ai_matches = matching_snapshot_records(ai_log_path, url=url, target_key=key)
            ai_freshness = snapshot_freshness(ai_matches, generated_at=generated, source="ai_log")
            payload["sync_performed"] = "yes"
            payload["exact_coverage"] = "yes"
            payload["state"] = "ready"
            payload["blocked_reason"] = ""
            payload["snapshot_status"] = "ready"
            payload["freshness"] = ai_freshness
            payload["recency"] = {key: ai_freshness[key] for key in ("status", "reason", "source")}
            payload["ai_log"]["exists"] = True
            payload["ai_log"]["match_count"] = len(ai_matches)
        payload["stale_policy"] = stale_policy_for_freshness(payload["freshness"])
        return payload

    payload["state"] = "raw_cache_missing"
    payload["blocked_reason"] = url_shape["blocked_reason"] or "raw_cache_missing"
    payload["snapshot_status"] = "raw_cache_missing"
    payload["freshness"] = snapshot_freshness([], generated_at=generated, source="none")
    payload["recency"] = {
        "status": "missing",
        "reason": "no_exact_url_or_target_key_match",
        "source": "none",
    }
    payload["fallback_allowed"] = "yes"
    payload["stale_policy"] = stale_policy_for_freshness(payload["freshness"])
    return payload


def build_coverage_report(
    *,
    url: str = "",
    target_key: str = "",
    raw_cache_path: Path | None = None,
    ai_log_path: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    source_kind: str = "saved_log",
    dedupe_policy: str = "by_hash",
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or utc_now()
    key = target_key or (target_key_for_url(url) if url else "")
    raw_matches = (
        matching_snapshot_records(raw_cache_path, url=url, target_key=key)
        if raw_cache_path is not None and url and key
        else []
    )
    ai_matches = matching_snapshot_records(ai_log_path, url=url, target_key=key) if url and key else []
    exact_coverage = bool(url and key and (ai_matches or (source_kind == "saved_log" and raw_matches)))
    selected_records = ai_matches or raw_matches
    freshness_source = "ai_log" if ai_matches else "raw_cache" if raw_matches else "none"
    freshness = snapshot_freshness(selected_records, generated_at=generated, source=freshness_source)
    url_shape = analyze_discord_forum_url_shape(url) if url else {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_forum_url_shape.v1",
        "valid_discord_channel_url": False,
        "shape": "missing",
        "path_id_count": 0,
        "guild_id_present": False,
        "parent_channel_id_present": False,
        "thread_id_present": False,
        "message_id_present": False,
        "blocked_reason": "url_missing",
        "same_guild_fuzzy_match_allowed": False,
    }
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_context_coverage_report.v1",
        "generated_at": generated,
        "message": "Discord URL coverage report を作成しました。",
        "target": {
            "target_key": key,
            "url_present": bool(url),
            "url_output": "omitted",
        },
        "url_shape": url_shape,
        "snapshot_status": "ready" if exact_coverage else "raw_cache_missing" if raw_cache_path is not None else "unknown",
        "freshness": freshness,
        "recency": {key: freshness[key] for key in ("status", "reason", "source")},
        "stale_policy": stale_policy_for_freshness(freshness),
        "source_kind": source_kind,
        "dedupe_policy": dedupe_policy,
        "coverage": {
            "raw_cache_checked": raw_cache_path is not None,
            "raw_cache_match_count": len(raw_matches),
            "ai_log_checked": True,
            "ai_log_match_count": len(ai_matches),
            "exact_coverage": exact_coverage,
        },
        "fallback_policy": {
            "allowed": "dom_export_or_manual_paste_only",
            "ocr_allowed": False,
            "outbound_actions": "disabled",
            "raw_text_returned": False,
        },
        "same_guild_fuzzy_match_allowed": False,
        "path_output": "omitted",
    }


def build_url_intake_fast_path(
    *,
    url: str,
    snapshot_store: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    target_key: str = "",
    hook_snapshot_status: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    key = target_key or target_key_for_url(url)
    generated = generated_at or utc_now()
    latest = build_latest_snapshot_report(path=snapshot_store, target_key=key, url=url)
    url_shape = analyze_discord_forum_url_shape(url)
    snapshot_ready = bool(latest.get("ok"))
    decision = "snapshot_metadata_ready" if snapshot_ready else "need_visible_text"
    next_step = "use_saved_snapshot_metadata" if snapshot_ready else "ask_for_visible_text_or_paste"
    observed_snapshot_status = "ready" if snapshot_ready else str(latest.get("reason") or hook_snapshot_status or "snapshot_missing")

    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_url_intake_fast_path.v1",
        "generated_at": generated,
        "message": "Discord URL intake の最短判定を作成しました。",
        "decision": decision,
        "next_step": next_step,
        "recommended_command": "report-latest",
        "command_budget": {
            "when_hook_status_present": "zero_extra_commands",
            "when_measurement_requested": "report-latest_once",
            "full_intake_policy_check": "verify-url-intake_once",
        },
        "hook_snapshot_status": hook_snapshot_status or "not_provided",
        "observed_snapshot_status": observed_snapshot_status,
        "text_required_tools_allowed": False,
        "target": {
            "target_key": key,
            "url_present": bool(url),
            "url_output": "omitted",
        },
        "url_shape": url_shape,
        "latest_snapshot_report": {
            "ok": bool(latest.get("ok")),
            "reason": str(latest.get("reason") or ""),
            "requested_filter": str(latest.get("requested_filter") or ""),
            "raw_text_returned": bool(latest.get("raw_text_returned")),
            "path_output": "omitted",
        },
        "operations": {
            "discord_outbound_actions": "disabled",
            "browser_access": "not_performed",
            "new_capture": False,
            "raw_text_returned": False,
            "paths_output": "omitted",
        },
        "raw_text_returned": False,
        "participant_names_returned": False,
        "local_paths_returned": False,
        "paths_output": "omitted",
        "outbound_actions": "disabled",
        "route_failure": "none",
    }


def load_events(path: Path = DEFAULT_STORE) -> list[DiscordEvent]:
    if not path.exists():
        return []
    events: list[DiscordEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(DiscordEvent.from_dict(json.loads(line)))
    return events


def find_private_issues(fields: dict[str, str], *, event_id: str = "", event_index: int = 0) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    checks = [
        ("discord_webhook_url", DISCORD_WEBHOOK_RE),
        ("discord_token", DISCORD_TOKEN_RE),
        ("local_absolute_path", LOCAL_ABSOLUTE_PATH_RE),
        ("discord_snowflake_id", DISCORD_SNOWFLAKE_RE),
    ]
    for field, value in fields.items():
        for kind, pattern in checks:
            if pattern.search(value):
                issues.append(
                    {
                        "event_index": event_index,
                        "event_id": event_id,
                        "field": field,
                        "kind": kind,
                    }
                )
                break
    return issues


def audit_event_store(path: Path = DEFAULT_STORE) -> dict[str, Any]:
    events = load_events(path)
    issues: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        issues.extend(
            find_private_issues(
                {
                    "author_label": event.author_label,
                    "guild_label": event.guild_label,
                    "channel_label": event.channel_label,
                    "text_snippet": event.text_snippet,
                },
                event_id=event.event_id,
                event_index=index,
            )
        )
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "保存データの安全監査が完了しました。",
        "store": str(path),
        "event_count": len(events),
        "issue_count": len(issues),
        "safe_for_tunnel": not issues,
        "safe_for_tunnel_label": "外部公開前の監査を通過しました。" if not issues else "外部公開前に確認が必要です。",
        "issues": issues,
    }


def ops_view_summary(path: Path = DEFAULT_STORE) -> dict[str, Any]:
    events = load_events(path)
    audit = audit_event_store(path)
    labels = sorted({event.channel_label for event in events})
    last_seen = max((event.observed_at for event in events), default=None)
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "運用ログ表示を作成しました。",
        "store": str(path),
        "safe_labels": labels,
        "event_count": len(events),
        "last_seen": last_seen,
        "delta_count": len(events),
        "gate_verdict": "pass" if audit["safe_for_tunnel"] else "review_required",
        "gate_verdict_label": audit["safe_for_tunnel_label"],
        "issue_count": audit["issue_count"],
        "outbound": "disabled",
        "outbound_label": "このツールから Discord へ送信しません。",
    }


def status_dashboard(path: Path = DEFAULT_STORE, *, github_state: str = "not_checked") -> dict[str, Any]:
    events = load_events(path)
    audit = audit_event_store(path)
    labels = sorted({event.channel_label for event in events})
    broken: list[str] = []
    blocked: list[str] = []
    next_steps: list[str] = []

    if audit["issue_count"]:
        broken.append("保存データの安全監査に確認事項があります。")
        blocked.append("外部公開前に audit-store を確認してください。")
    if not events:
        blocked.append("Discord 可視本文はまだ取り込まれていません。")
        next_steps.append("private adapter probe または import-visible-text を実行します。")
    else:
        next_steps.append("context-passport または review-draft で返信前確認を行えます。")

    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_bridge_status_dashboard.v1",
        "message": "Discord bridge status dashboard を作成しました。",
        "now": {
            "context_available": bool(events),
            "event_count": len(events),
            "safe_label_count": len(labels),
            "latest_seen": max((event.observed_at for event in events), default=None),
            "text_returned": False,
            "text_saved_in_status": False,
        },
        "done": [
            "read-only event store",
            "ops-view",
            "context-passport",
            "review-draft",
            "private-adapter-probe",
        ],
        "broken": broken,
        "blocked": blocked,
        "next": next_steps,
        "github": {"state": github_state},
        "residual": [
            "Discord への送信、削除、reaction は無効です。",
            "実 Discord 本文取得は private adapter 側で扱います。",
        ],
        "safety_boundary": {
            "outbound_actions": "disabled",
            "raw_text_included": False,
            "participant_names_included": False,
            "local_paths_included": False,
        },
    }


def load_context_library(path: Path = DEFAULT_CONTEXT_STORE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("文脈庫の形式が不正です。list JSON が必要です。")
    return [dict(item) for item in loaded]


def save_context_library(entries: list[dict[str, Any]], path: Path = DEFAULT_CONTEXT_STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_review_registry(path: Path = DEFAULT_REVIEW_STORE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("review registry の形式が不正です。list JSON が必要です。")
    return [dict(item) for item in loaded]


def save_review_registry(entries: list[dict[str, Any]], path: Path = DEFAULT_REVIEW_STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def context_entry_id(kind: str, key: str) -> str:
    return stable_event_id({"kind": kind, "key": key})


def normalize_safe_label(label: str) -> str:
    return " ".join(label.strip().lstrip("#").casefold().split())


def unique_safe_labels(labels: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for label in labels:
        cleaned = " ".join(str(label).strip().split())
        normalized = normalize_safe_label(cleaned)
        if cleaned and normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(cleaned)
    return unique


def context_binding_candidates(kind: str, *, guild_label: str, channel_label: str) -> list[str]:
    guild = guild_label.strip()
    channel = channel_label.strip().lstrip("#")
    if kind == "server":
        return unique_safe_labels([guild])
    if kind == "channel":
        return unique_safe_labels(
            [
                channel,
                f"{guild}/{channel}",
                f"{guild}:{channel}",
                f"{guild}#{channel}",
            ]
        )
    if kind == "thread":
        return unique_safe_labels(
            [
                channel,
                f"{channel}:thread",
                f"{guild}/{channel}",
                f"{guild}/{channel}/thread",
                f"{guild}:{channel}",
            ]
        )
    raise ValueError("kind は server / channel / thread のいずれかです。")


def resolve_context_binding(
    kind: str,
    *,
    guild_label: str,
    channel_label: str,
    path: Path = DEFAULT_CONTEXT_STORE,
) -> dict[str, str] | None:
    candidates = {
        normalize_safe_label(label)
        for label in context_binding_candidates(kind, guild_label=guild_label, channel_label=channel_label)
    }
    for entry in load_context_library(path):
        if entry.get("kind") != kind:
            continue
        labels = unique_safe_labels([str(entry.get("key") or ""), *list(entry.get("labels") or [])])
        for label in labels:
            if normalize_safe_label(label) in candidates:
                return {
                    "kind": kind,
                    "key": str(entry.get("key") or ""),
                    "matched_label": label,
                }
    return None


def resolve_context_bindings(
    *,
    guild_label: str,
    channel_label: str,
    path: Path = DEFAULT_CONTEXT_STORE,
) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for kind in ("server", "channel", "thread"):
        binding = resolve_context_binding(
            kind,
            guild_label=guild_label,
            channel_label=channel_label,
            path=path,
        )
        if binding:
            bindings[kind] = binding
    return bindings


def upsert_context_document(
    kind: str,
    key: str,
    text: str,
    *,
    path: Path = DEFAULT_CONTEXT_STORE,
    source: str = "manual",
    labels: Iterable[str] | None = None,
) -> dict[str, Any]:
    if kind not in {"server", "channel", "thread"}:
        raise ValueError("kind は server / channel / thread のいずれかです。")
    if not key.strip():
        raise ValueError("key は空にできません。")
    if not text.strip():
        raise ValueError("text は空にできません。")
    entries = load_context_library(path)
    entry_id = context_entry_id(kind, key)
    entry = {
        "id": entry_id,
        "kind": kind,
        "key": key,
        "labels": unique_safe_labels(labels or []),
        "source": source,
        "text": text.strip(),
        "summary": compact_context_text(text),
        "updated_at": utc_now(),
    }
    replaced = False
    for index, existing in enumerate(entries):
        if existing.get("id") == entry_id:
            entries[index] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    save_context_library(entries, path)
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "文脈庫を更新しました。",
        "changed": True,
        "created": not replaced,
        "context_store": str(path),
        "entry": {key: value for key, value in entry.items() if key != "text"},
    }


def get_context_document(kind: str, key: str, *, path: Path = DEFAULT_CONTEXT_STORE) -> str:
    entry_id = context_entry_id(kind, key)
    for entry in load_context_library(path):
        if entry.get("id") == entry_id:
            return str(entry.get("text") or "")
    return ""


def list_context_documents(path: Path = DEFAULT_CONTEXT_STORE) -> dict[str, Any]:
    entries = load_context_library(path)
    public_entries = [{key: value for key, value in entry.items() if key != "text"} for entry in entries]
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "文脈庫の一覧を取得しました。",
        "context_store": str(path),
        "count": len(entries),
        "entries": public_entries,
    }


def audit_context_store(path: Path = DEFAULT_CONTEXT_STORE) -> dict[str, Any]:
    entries = load_context_library(path)
    issues: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        issues.extend(
            find_private_issues(
                {
                    "key": str(entry.get("key") or ""),
                    "labels": " ".join(str(label) for label in entry.get("labels") or []),
                    "source": str(entry.get("source") or ""),
                    "summary": str(entry.get("summary") or ""),
                    "text": str(entry.get("text") or ""),
                },
                event_id=str(entry.get("id") or ""),
                event_index=index,
            )
        )
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "文脈庫の安全監査が完了しました。",
        "context_store": str(path),
        "entry_count": len(entries),
        "issue_count": len(issues),
        "safe_for_tunnel": not issues,
        "safe_for_tunnel_label": "外部公開前の監査を通過しました。" if not issues else "外部公開前に確認が必要です。",
        "issues": issues,
    }


def review_state_id(thread_key: str) -> str:
    return stable_event_id({"kind": "review_state", "thread_key": thread_key})


def upsert_review_state(
    thread_key: str,
    review: dict[str, Any],
    *,
    path: Path = DEFAULT_REVIEW_STORE,
    read_scope: Iterable[str] | None = None,
    gate_decision: str = "pending",
) -> dict[str, Any]:
    if not thread_key.strip():
        raise ValueError("thread_key は空にできません。")
    safe_thread_key = redact_artifact_text(thread_key) or "manual-thread"
    human_gate = dict(review.get("human_gate") or {})
    copy_block = dict(review.get("copy_block") or {})
    scope = [redact_artifact_text(str(item)) for item in (read_scope or ["visible_text", "review_draft"]) if str(item).strip()]
    entry = {
        "schema": "discord_review_state.v1",
        "id": review_state_id(safe_thread_key),
        "thread_key": safe_thread_key,
        "updated_at": utc_now(),
        "gate_decision": redact_artifact_text(gate_decision) or "pending",
        "recommended_option": str(human_gate.get("recommended_option") or "copy"),
        "copy_block_status": str(copy_block.get("status") or "unknown"),
        "quick_verdict": str(review.get("quick_verdict") or "unknown"),
        "ok_to_reply": str(review.get("ok_to_reply") or "unknown"),
        "alignment": str(review.get("alignment") or "unknown"),
        "missing_premise_count": len(review.get("missing_knowledge") or []),
        "read_scope": scope,
        "next_action": next_action_from_review_state(
            recommended_option=str(human_gate.get("recommended_option") or "copy"),
            copy_block_status=str(copy_block.get("status") or "unknown"),
        ),
        "stopline": [
            "Discord send/reaction/edit/delete disabled",
            "raw Discord text omitted",
            "participant names omitted",
        ],
        "outbound_actions": "disabled",
    }
    entries = load_review_registry(path)
    replaced = False
    for index, existing in enumerate(entries):
        if existing.get("id") == entry["id"]:
            entries[index] = entry
            replaced = True
            break
    if not replaced:
        entries.append(entry)
    save_review_registry(entries, path)
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "review registry を更新しました。",
        "changed": True,
        "created": not replaced,
        "path_output": "omitted",
        "entry": entry,
    }


def next_action_from_review_state(*, recommended_option: str, copy_block_status: str) -> str:
    if copy_block_status == "blocked":
        return "copy block を短く編集して再レビューしてください。"
    if recommended_option == "copy":
        return "copy block を人間が確認して Discord 側で貼り付けます。"
    if recommended_option == "read-more":
        return "追加文脈を読んでから再レビューしてください。"
    if recommended_option == "wait":
        return "今は送らず待機してください。"
    if recommended_option == "no-reply":
        return "返信しない判断を記録してください。"
    return "人間が下書きを編集して再レビューしてください。"


def get_review_state(thread_key: str, *, path: Path = DEFAULT_REVIEW_STORE) -> dict[str, Any] | None:
    state_id = review_state_id(redact_artifact_text(thread_key) or "manual-thread")
    for entry in load_review_registry(path):
        if entry.get("id") == state_id:
            return dict(entry)
    return None


def build_handoff_packet(
    *,
    thread_key: str = "manual-thread",
    review_state: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = status or {}
    review_state = review_state or {}
    read_scope = list(review_state.get("read_scope") or [])
    next_action = str(review_state.get("next_action") or "review-draft で返信前レビューを作成してください。")
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_handoff_packet.v1",
        "message": "handoff packet を作成しました。",
        "thread_key": redact_artifact_text(thread_key) or "manual-thread",
        "current_state": {
            "review_state_available": bool(review_state),
            "context_available": bool((status.get("now") or {}).get("context_available")),
            "gate_decision": review_state.get("gate_decision", "not_recorded"),
            "copy_block_status": review_state.get("copy_block_status", "not_recorded"),
        },
        "read_scope": read_scope,
        "next_action": next_action,
        "stopline": review_state.get(
            "stopline",
            [
                "Discord send/reaction/edit/delete disabled",
                "raw Discord text omitted",
                "participant names omitted",
            ],
        ),
        "residual": [
            "実 Discord 本文取得はMVP外の任意 adapter です。",
            "公開、告知、外部送信は現在会話の明示承認が必要です。",
        ],
        "path_output": "omitted",
        "safety_boundary": {
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "local_paths_output": "omitted",
            "outbound_actions": "disabled",
        },
    }


def load_text_snapshots(path: Path = DEFAULT_TEXT_SNAPSHOT_STORE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            snapshots.append(dict(json.loads(line)))
    return snapshots


def append_text_snapshot(snapshot: dict[str, Any], path: Path = DEFAULT_TEXT_SNAPSHOT_STORE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")


def latest_snapshot_for_target(target_key: str, path: Path = DEFAULT_TEXT_SNAPSHOT_STORE) -> dict[str, Any] | None:
    for snapshot in reversed(load_text_snapshots(path)):
        if snapshot.get("target_key") == target_key:
            return snapshot
    return None


def snapshot_observation_event_id(
    *,
    captured_at: str,
    target_key: str,
    content_hash: str,
    source: str,
    stream_sequence: int,
) -> str:
    return stable_text_hash("|".join([captured_at, target_key, content_hash, source, str(stream_sequence)]))


def canonical_event_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "event_hash"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_kind_from_source(source: str) -> str:
    lowered = source.casefold()
    if "chrome" in lowered or "dom" in lowered or "browser" in lowered:
        return "Chrome DOM"
    if "cache" in lowered:
        return "cache"
    if "clipboard" in lowered:
        return "clipboard"
    if "source-command" in lowered or "source_command" in lowered or "adapter" in lowered:
        return "source-command"
    if "visible" in lowered or "snapshot" in lowered:
        return "visible_text"
    return "unknown"


def acquisition_context_for_source(source: str) -> dict[str, Any]:
    source_kind = source_kind_from_source(source)
    base = {
        "schema": "discord_bridge_acquisition_context.v1",
        "source_kind": source_kind,
        "source_route": source or "unknown",
        "record_origin": "local_visible_text_snapshot",
        "token_or_cookie_read": False,
        "outbound_actions": "disabled",
    }
    if source_kind == "Chrome DOM":
        return {
            **base,
            "capture_method": "chrome_dom_visible_range",
            "live_browser_access_required_for_capture": True,
            "private_adapter_required_for_capture": True,
        }
    if source_kind == "cache":
        return {
            **base,
            "capture_method": "local_cache_snapshot",
            "live_browser_access_required_for_capture": False,
            "private_adapter_required_for_capture": False,
        }
    if source_kind == "source-command":
        return {
            **base,
            "capture_method": "local_source_command_stdout",
            "live_browser_access_required_for_capture": None,
            "private_adapter_required_for_capture": True,
        }
    if source_kind == "clipboard":
        return {
            **base,
            "capture_method": "local_clipboard_visible_text",
            "live_browser_access_required_for_capture": False,
            "private_adapter_required_for_capture": False,
        }
    return {
        **base,
        "capture_method": "manual_or_visible_text_snapshot",
        "live_browser_access_required_for_capture": False,
        "private_adapter_required_for_capture": False,
    }


def compact_snapshot_body_evidence(text: str, *, include_preview: bool = False, max_preview_chars: int = 24) -> dict[str, Any]:
    stripped = " ".join(text.split())
    preview = stripped[:max_preview_chars] if include_preview and stripped else "omitted"
    return {
        "schema": "discord_bridge_snapshot_body_evidence.v1",
        "raw_text_returned": False,
        "full_content_returned": False,
        "preview_returned": bool(include_preview and stripped),
        "preview": preview,
        "preview_char_count": len(preview) if include_preview and stripped else 0,
        "char_count": len(text),
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "content_hash": stable_text_hash(text) if text else "",
    }


def select_latest_snapshot(
    *,
    path: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    target_key: str = "",
    url: str = "",
) -> dict[str, Any] | None:
    snapshots = load_text_snapshots(path)
    if target_key:
        for snapshot in reversed(snapshots):
            if snapshot.get("target_key") == target_key:
                return snapshot
        return None
    if url:
        for snapshot in reversed(snapshots):
            if snapshot.get("url") == url:
                return snapshot
        return None
    return snapshots[-1] if snapshots else None


def build_latest_snapshot_report(
    *,
    path: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
    target_key: str = "",
    url: str = "",
    include_preview: bool = False,
) -> dict[str, Any]:
    snapshot = select_latest_snapshot(path=path, target_key=target_key, url=url)
    requested_filter = "target_key" if target_key else "url" if url else "latest"
    report_acquisition_context = {
        "schema": "discord_bridge_report_acquisition_context.v1",
        "mode": "existing_saved_snapshot",
        "description": "この report は保存済み snapshot store だけを読みます。Chrome 接続、DOM 再取得、新規 capture は行いません。",
        "live_browser_access": False,
        "new_capture": False,
        "snapshot_store_read": True,
    }
    if not snapshot:
        return {
            "language": DEFAULT_LANGUAGE,
            "schema": "discord_bridge_latest_snapshot_report.v1",
            "ok": False,
            "message": "該当する保存済み snapshot がありません。",
            "reason": "snapshot_missing",
            "requested_filter": requested_filter,
            "raw_text_returned": False,
            "participant_names_returned": False,
            "local_paths_returned": False,
            "path_output": "omitted",
            "report_acquisition_context": report_acquisition_context,
            "outbound_actions": "disabled",
        }

    text = str(snapshot.get("text") or "")
    source = str(snapshot.get("source") or "unknown")
    stored_acquisition_context = dict(snapshot.get("acquisition_context") or acquisition_context_for_source(source))
    events = parse_visible_text(text)
    latest_event = events[-1].to_dict() if events else {}
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_bridge_latest_snapshot_report.v1",
        "ok": True,
        "message": "保存済み snapshot から最新 report を作成しました。",
        "requested_filter": requested_filter,
        "raw_text_returned": False,
        "participant_names_returned": False,
        "local_paths_returned": False,
        "path_output": "omitted",
        "target": {
            "target_key": str(snapshot.get("target_key") or ""),
            "url_present": bool(snapshot.get("url")),
            "url_output": "omitted",
            "title_present": bool(snapshot.get("title")),
            "title_output": "omitted",
        },
        "latest_saved_or_visible_message": {
            "observed_at": str(snapshot.get("captured_at") or ""),
            "speaker": "omitted",
            "speaker_returned": False,
            "parsed_message_available": bool(events),
            "parsed_observed_at": str(latest_event.get("observed_at") or "") if latest_event else "",
            "body_evidence": compact_snapshot_body_evidence(text, include_preview=include_preview),
        },
        "input_state": {
            "state": "not_applicable",
            "reason": "保存済み snapshot store の metadata-only report のため、Discord 入力欄は観測しません。",
        },
        "source_summary": {
            "source_kind": stored_acquisition_context.get("source_kind", source_kind_from_source(source)),
            "source_route": source,
            "stored_acquisition_context": stored_acquisition_context,
            "report_acquisition_context": report_acquisition_context,
        },
        "uncaptured_range": {
            "before_first_saved_snapshot": "unknown",
            "after_latest_saved_snapshot": "unknown",
            "reason": "保存済み snapshot 以外の Discord 画面範囲はこの report では取得しません。",
        },
        "outbound_actions": "disabled",
    }


def normalize_message_text(messages: Iterable[Any]) -> str:
    lines: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            author = str(message.get("author") or message.get("author_label") or "").strip()
            text = str(message.get("text") or message.get("content") or message.get("text_snippet") or "").strip()
            timestamp = str(message.get("timestamp") or message.get("observed_at") or "").strip()
            prefix = " ".join(part for part in [author, timestamp] if part)
            lines.append(f"{prefix}: {text}" if prefix and text else text or prefix)
        else:
            lines.append(str(message).strip())
    return "\n".join(line for line in lines if line)


def plan_discord_url_read(url: str) -> dict[str, Any]:
    match = re.match(r"^https://discord\.com/channels/([^/]+)/([^/]+)(?:/([^/?#]+))?", url.strip())
    if not match:
        return {
            "language": DEFAULT_LANGUAGE,
            "message": "Discord URL として読めませんでした。",
            "ok_to_open": False,
            "reason": "discord_channel_url_required",
            "outbound_actions": "disabled",
        }
    guild_id, channel_id, message_or_thread_id = match.groups()
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "Discord URL の読み取り計画を作成しました。",
        "ok_to_open": True,
        "guild_id": guild_id,
        "channel_id": channel_id,
        "message_or_thread_id": message_or_thread_id or "",
        "read_method": "browser_visible_text",
        "save_target": str(DEFAULT_TEXT_SNAPSHOT_STORE),
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
    }


def snapshot_visible_text(
    *,
    text: str = "",
    messages: Iterable[Any] | None = None,
    url: str = "",
    title: str = "",
    source: str = "visible_text",
    path: Path = DEFAULT_TEXT_SNAPSHOT_STORE,
) -> dict[str, Any]:
    message_list = list(messages or [])
    content = text.strip() or normalize_message_text(message_list)
    if not content:
        raise ValueError("snapshot する text または messages が必要です。")
    target_identity = url.strip() or title.strip() or content[:120]
    target_key = stable_text_hash(target_identity)
    content_hash = stable_text_hash(content)
    previous = latest_snapshot_for_target(target_key, path)
    previous_hash = str(previous.get("content_hash") or "") if previous else None
    previous_event_hash = str(previous.get("event_hash") or canonical_event_hash(previous)) if previous else ""
    snapshot_count_before = sum(1 for item in load_text_snapshots(path) if item.get("target_key") == target_key)
    previous_stream_sequence = (
        int(previous.get("stream_sequence") or previous.get("observation_index_for_target") or snapshot_count_before)
        if previous
        else 0
    )
    changed = previous_hash != content_hash
    stream_sequence = snapshot_count_before + 1
    captured_at = utc_now()
    snapshot = {
        "schema": "discord_context_bridge_text_snapshot_observation.v1",
        "event_id": snapshot_observation_event_id(
            captured_at=captured_at,
            target_key=target_key,
            content_hash=content_hash,
            source=source,
            stream_sequence=stream_sequence,
        ),
        "event_type": "discord.visible_text.snapshot_observed",
        "stream_id": target_key,
        "stream_sequence": stream_sequence,
        "expected_previous_stream_sequence": previous_stream_sequence,
        "specversion": "1.0",
        "type": "discord.visible_text.snapshot_observed",
        "subject": target_key,
        "time": captured_at,
        "datacontenttype": "text/plain; charset=utf-8",
        "dataschema": "discord_context_bridge_text_snapshot_observation.v1",
        "captured_at": captured_at,
        "observed_at": captured_at,
        "ingested_at": captured_at,
        "source": source,
        "url": url.strip(),
        "title": title.strip(),
        "target_key": target_key,
        "content_hash": content_hash,
        "previous_content_hash": previous_hash,
        "previous_event_hash": previous_event_hash,
        "changed": changed,
        "duplicate_content": not changed,
        "observation_index_for_target": stream_sequence,
        "acquisition_context": acquisition_context_for_source(source),
        "text": content,
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
    }
    snapshot["event_hash"] = canonical_event_hash(snapshot)
    append_text_snapshot(snapshot, path)
    snapshot_count = sum(1 for item in load_text_snapshots(path) if item.get("target_key") == target_key)
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "Discord 可視テキスト snapshot observation を追記しました。",
        "saved": True,
        "changed": changed,
        "duplicate_content": not changed,
        "snapshot_store": str(path),
        "target_key": target_key,
        "content_hash": content_hash,
        "previous_content_hash": previous_hash,
        "observation_index_for_target": snapshot_count,
        "snapshot_count_for_target": snapshot_count,
        "private_local_only": True,
        "external_share_allowed": False,
        "outbound_actions": "disabled",
        "source": source,
        "message_count": len(message_list),
        "context_ready": True,
        "visible_text_saved": True,
    }


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(dict(json.loads(line)))
    return records


def attachment_source_key(record: dict[str, Any], attachment: dict[str, Any], index: int) -> str:
    identity = {
        "message_id": record.get("message_id") or record.get("id") or record.get("messageId") or "",
        "attachment_id": attachment.get("id") or attachment.get("attachment_id") or "",
        "filename": attachment.get("filename") or attachment.get("name") or "",
        "index": index,
    }
    return stable_event_id(identity)


def classify_attachment_type(filename: str, content_type: str = "") -> str:
    mime = content_type.strip() or (mimetypes.guess_type(filename)[0] or "")
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime == "application/pdf":
        return "document"
    if mime.startswith("text/"):
        return "text"
    return "unknown"


def safe_attachment_filename(filename: str) -> str:
    safe = redact_artifact_text(Path(filename or "attachment").name)
    return safe or "attachment"


def attachment_local_status(local_path: str | Path | None) -> str:
    if not local_path:
        return "not_recorded"
    return "present" if Path(local_path).exists() else "missing"


def normalize_attachment_entries(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in records:
        attachments = record.get("attachments") or []
        if isinstance(attachments, dict):
            attachments = [attachments]
        if not isinstance(attachments, list):
            continue
        for index, item in enumerate(attachments):
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or item.get("name") or "attachment")
            content_type = str(item.get("content_type") or item.get("contentType") or "")
            local_path = item.get("local_path") or item.get("localPath") or item.get("path")
            width = item.get("width")
            height = item.get("height")
            size = item.get("size") or item.get("size_bytes") or item.get("sizeBytes")
            entry = {
                "schema": "discord_attachment_ledger_entry.v1",
                "source_key": attachment_source_key(record, item, index),
                "captured_at": str(record.get("captured_at") or record.get("timestamp") or record.get("observed_at") or ""),
                "filename": safe_attachment_filename(filename),
                "content_type": content_type or (mimetypes.guess_type(filename)[0] or "unknown"),
                "attachment_type": classify_attachment_type(filename, content_type),
                "width": int(width) if isinstance(width, int) else None,
                "height": int(height) if isinstance(height, int) else None,
                "size_bytes": int(size) if isinstance(size, int) else None,
                "local_status": attachment_local_status(local_path),
                "url_present": bool(item.get("url") or item.get("proxy_url") or item.get("proxyUrl")),
                "url_output": "omitted",
                "local_path_output": "omitted",
                "outbound_actions": "disabled",
            }
            entries.append(entry)
    return entries


def render_attachment_ledger_markdown(entries: list[dict[str, Any]]) -> str:
    lines = [
        "# Discord attachment ledger",
        "",
        "この ledger は添付の safe metadata だけを保持します。raw Discord URL、local path、本文は出力しません。",
        "",
        "| source_key | type | filename | size_bytes | dimensions | local_status |",
        "|---|---|---|---:|---|---|",
    ]
    for entry in entries:
        dimensions = ""
        if entry.get("width") and entry.get("height"):
            dimensions = f"{entry['width']}x{entry['height']}"
        lines.append(
            "| {source_key} | {attachment_type} | {filename} | {size_bytes} | {dimensions} | {local_status} |".format(
                source_key=entry["source_key"],
                attachment_type=entry["attachment_type"],
                filename=entry["filename"],
                size_bytes=entry["size_bytes"] if entry["size_bytes"] is not None else "",
                dimensions=dimensions,
                local_status=entry["local_status"],
            )
        )
    lines.extend(
        [
            "",
            "raw_url_output: omitted",
            "local_path_output: omitted",
            "outbound_actions: disabled",
        ]
    )
    return "\n".join(lines) + "\n"


def build_attachment_ledger(*, records: Iterable[dict[str, Any]], output: Path | None = None) -> dict[str, Any]:
    entries = normalize_attachment_entries(records)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_attachment_ledger_markdown(entries), encoding="utf-8")
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_attachment_ledger.v1",
        "message": "Discord 添付 ledger を作成しました。",
        "attachment_count": len(entries),
        "entries": entries,
        "ledger_created": output is not None,
        "path_output": "omitted",
        "raw_url_returned": False,
        "raw_text_returned": False,
        "local_paths_returned": False,
        "outbound_actions": "disabled",
    }


def build_attachment_ocr_log_markdown(
    *,
    source_key: str,
    ocr_text: str,
    note_label: str = "",
) -> str:
    safe_source_key = redact_artifact_text(source_key) or stable_text_hash(source_key)
    safe_note_label = redact_artifact_text(note_label)
    return "\n".join(
        [
            "# Discord attachment OCR log",
            "",
            "status: private_local_review",
            f"source_key: {safe_source_key}",
            f"note_label: {safe_note_label or 'なし'}",
            "raw_url_output: omitted",
            "local_path_output: omitted",
            "stdout_raw_text_output: omitted",
            "outbound_actions: disabled",
            "",
            "## OCR text",
            "",
            ocr_text.strip() or "(empty)",
            "",
        ]
    )


def write_attachment_ocr_log(
    *,
    source_key: str,
    ocr_text: str,
    output: Path,
    note_label: str = "",
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_attachment_ocr_log_markdown(source_key=source_key, ocr_text=ocr_text, note_label=note_label),
        encoding="utf-8",
    )
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_attachment_ocr_log.v1",
        "message": "Discord 添付 OCR log を保存しました。",
        "created": True,
        "source_key": redact_artifact_text(source_key) or stable_text_hash(source_key),
        "note_label": redact_artifact_text(note_label),
        "ocr_char_count": len(ocr_text),
        "path_output": "omitted",
        "raw_text_returned": False,
        "raw_url_returned": False,
        "local_paths_returned": False,
        "outbound_actions": "disabled",
    }


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


def public_safe_events(events: Iterable[DiscordEvent]) -> list[DiscordEvent]:
    author_aliases: dict[str, str] = {}
    safe_events: list[DiscordEvent] = []
    for event in events:
        if event.author_label not in author_aliases:
            author_aliases[event.author_label] = f"participant-{len(author_aliases) + 1:03d}"
        safe_events.append(
            DiscordEvent.from_dict(
                {
                    **event.to_dict(),
                    "author_label": author_aliases[event.author_label],
                    "text_snippet": "omitted",
                    "event_id": event.event_id,
                }
            )
        )
    return safe_events


def looks_like_author_line(line: str) -> bool:
    if len(line) > 40:
        return False
    if line.endswith((".", "?", "!", "。", "？", "！")):
        return False
    return bool(re.search(r"[A-Za-z一-龥ぁ-んァ-ン0-9]", line))


def looks_like_timestamp_metadata(line: str) -> bool:
    return bool(TIMESTAMP_METADATA_RE.match(line.strip()))


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
        if not line or looks_like_timestamp_metadata(line):
            continue
        author_with_timestamp = AUTHOR_WITH_TIMESTAMP_RE.match(line)
        if author_with_timestamp:
            flush()
            current_author = author_with_timestamp.group("author").strip()
            continue
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
    dry_run: bool = False,
) -> dict[str, Any]:
    observed_at = utc_now()
    events = parse_visible_text(
        text,
        guild_label=guild_label,
        channel_label=channel_label,
        observed_at=observed_at,
    )
    safe_events = public_safe_events(events)
    result = {"appended": 0, "duplicate": 0} if dry_run else append_events(safe_events, path)
    loaded_events = events if dry_run else load_events(path)
    return {
        **result,
        "dry_run": dry_run,
        "language": DEFAULT_LANGUAGE,
        "message": "Discord の可視テキストを取り込みました。" if not dry_run else "保存せずに取り込み結果を確認しました。",
        "parsed": len(events),
        "store": str(path),
        "preview": [event.to_dict() for event in events],
        "briefing": fast_briefing(loaded_events),
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
        "message": "直近文脈の短い要約を作成しました。",
        "event_count": len(latest),
        "channels": sorted({event.channel_label for event in latest}),
        "authors": [event.author_label for event in latest],
        "briefing": " / ".join(event.text_snippet for event in latest),
        "briefing_label": " / ".join(event.text_snippet for event in latest) or "直近の文脈はまだ取り込まれていません。",
        "partial": True,
        "partial_label": "直近の一部だけを使った要約です。",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def check_knowledge_gap(user_understanding: str, events: Iterable[DiscordEvent]) -> dict[str, Any]:
    context = fast_briefing(events)
    text = user_understanding.casefold()
    context_topics = detect_topics(context["briefing"])
    draft_topics = detect_topics(user_understanding)
    topic_mismatch = bool(context_topics and draft_topics and context_topics.isdisjoint(draft_topics))
    missing: list[str] = []
    if "premise" not in text and "前提" not in text:
        missing.append("共有前提")
    if "reply" not in text and "返信" not in text:
        missing.append("返信対象")
    return {
        "language": DEFAULT_LANGUAGE,
        "context_drift_warning": bool(missing),
        "knowledge_gap": missing,
        "context_topics": sorted(context_topics),
        "draft_topics": sorted(draft_topics),
        "topic_mismatch": topic_mismatch,
        "topic_warning_label": (
            "話題がずれている可能性があります: 文脈は"
            + "・".join(sorted(context_topics))
            + "、返信案は"
            + "・".join(sorted(draft_topics))
            + "に見えます。"
            if topic_mismatch
            else "話題の大きなズレは見つかりません。"
        ),
        "recommended_briefing": context["briefing"] or "直近の文脈はまだ取り込まれていません。",
    }


def detect_topics(text: str) -> set[str]:
    folded = text.casefold()
    topics: set[str] = set()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword.casefold() in folded for keyword in keywords):
            topics.add(topic)
    return topics


def detect_purposes(text: str) -> set[str]:
    folded = text.casefold()
    purposes: set[str] = set()
    for purpose, keywords in PURPOSE_KEYWORDS.items():
        if any(keyword.casefold() in folded for keyword in keywords):
            purposes.add(purpose)
    return purposes


def extract_matching_snippets(events: Iterable[DiscordEvent], keywords: Iterable[str], *, limit: int = 3) -> list[str]:
    lowered_keywords = [keyword.casefold() for keyword in keywords]
    snippets: list[str] = []
    for event in events:
        folded = event.text_snippet.casefold()
        if any(keyword in folded for keyword in lowered_keywords):
            snippets.append(event.text_snippet)
        if len(snippets) >= limit:
            break
    return snippets


def compact_context_text(text: str, *, limit: int = 180) -> str:
    compacted = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return compacted[:limit].rstrip() + ("…" if len(compacted) > limit else "")


def build_context_documents(
    *,
    server_context: str = "",
    channel_context: str = "",
    thread_context: str = "",
) -> list[dict[str, str]]:
    candidates = [
        ("server_context", "サーバー文脈", server_context),
        ("channel_context", "チャンネル文脈", channel_context),
        ("thread_context", "スレッド文脈", thread_context),
    ]
    return [
        {
            "source": source,
            "source_label": label,
            "text": text.strip(),
            "summary": compact_context_text(text),
        }
        for source, label, text in candidates
        if text and text.strip()
    ]


def context_documents_text(context_documents: Iterable[dict[str, str]]) -> str:
    return " ".join(document["text"] for document in context_documents)


def extract_context_rule_notes(context_documents: Iterable[dict[str, str]], *, limit: int = 4) -> list[str]:
    notes: list[str] = []
    lowered_keywords = [keyword.casefold() for keyword in RULE_KEYWORDS]
    for document in context_documents:
        folded = document["text"].casefold()
        if any(keyword in folded for keyword in lowered_keywords):
            notes.append(f"{document['source_label']}: {document['summary']}")
        if len(notes) >= limit:
            break
    return notes


def summarize_thread_purpose(events: list[DiscordEvent], context_documents: list[dict[str, str]] | None = None) -> tuple[str, str]:
    context_documents = context_documents or []
    joined = " ".join(event.text_snippet for event in events) + " " + context_documents_text(context_documents)
    purposes = sorted(detect_purposes(joined))
    topics = sorted(detect_topics(joined))
    if purposes and topics:
        value = " / ".join(purposes + topics)
        return value, f"このスレッドは {value} に関係していそうです。"
    if purposes:
        value = " / ".join(purposes)
        return value, f"このスレッドは {value} の場に見えます。"
    if topics:
        value = " / ".join(topics)
        return value, f"このスレッドは {value} の話題に見えます。"
    return "未特定", "目的はまだ断定できません。直近文脈だけで仮読みしています。"


def classify_thread_temperature(events: list[DiscordEvent], context_documents: list[dict[str, str]] | None = None) -> tuple[str, str]:
    context_documents = context_documents or []
    joined = (" ".join(event.text_snippet for event in events) + " " + context_documents_text(context_documents)).casefold()
    if any(keyword.casefold() in joined for keyword in ("炎上", "荒れ", "怒", "攻撃", "揉め", "hot")):
        return "hot", "温度が高い可能性があります。入る前に一度止めた方が安全です。"
    if any(keyword.casefold() in joined for keyword in SERIOUS_KEYWORDS):
        return "serious", "相談・注意・困りごと寄りです。勢いより前提確認が安全です。"
    if any(keyword.casefold() in joined for keyword in PLAY_KEYWORDS):
        return "play", "軽いノリの会話に見えます。短く入るなら自然です。"
    return "chat", "通常の会話に見えます。文脈に一言つなげると入りやすいです。"


def build_context_passport(
    events: list[DiscordEvent],
    *,
    context_documents: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    context_documents = context_documents or []
    briefing = fast_briefing(events, limit=5)
    purpose, purpose_label = summarize_thread_purpose(events, context_documents)
    temperature, temperature_label = classify_thread_temperature(events, context_documents)
    visible_rule_notes = extract_matching_snippets(events, RULE_KEYWORDS)
    context_rule_notes = extract_context_rule_notes(context_documents)
    rule_notes = context_rule_notes + visible_rule_notes
    premise_notes = [
        f"{document['source_label']}: {document['summary']}"
        for document in context_documents
        if any(keyword in document["text"] for keyword in ("前提", "目的", "ルール", "方針", "禁止", "注意"))
    ]
    premise_notes += extract_matching_snippets(events, ("前提", "つまり", "ここまで", "目的", "ルール", "まず"), limit=4)
    premise_notes = premise_notes[:5]
    topics = sorted(detect_topics(briefing["briefing"] + " " + context_documents_text(context_documents)))
    authors = [event.author_label for event in events[-5:]]
    context_sources = [document["source"] for document in context_documents]
    natural_entry_angles: list[str] = []
    if rule_notes:
        natural_entry_angles.append("先にルールや注意点を踏まえていることを示す。")
    if context_documents:
        natural_entry_angles.append("サーバー・チャンネル・スレッドの明示文脈を優先して確認する。")
    if topics:
        natural_entry_angles.append("今の話題（" + "、".join(topics) + "）に一言つなげて入る。")
    if temperature == "play":
        natural_entry_angles.append("短いノリで入る。説明しすぎない。")
    elif temperature in {"serious", "hot"}:
        natural_entry_angles.append("断定せず、前提確認から入る。")
    else:
        natural_entry_angles.append("直近発言への短い反応から入る。")

    context_ready = bool(events) and bool(briefing["briefing"])
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "スレッド文脈カードを作成しました。",
        "parsed": len(events),
        "external_context_used": bool(context_documents),
        "external_context_used_label": "明示文脈を併用しています。" if context_documents else "明示文脈は未入力です。可視本文だけで仮読みしています。",
        "context_sources": context_sources,
        "context_sources_label": "文脈ソース: " + ("、".join(document["source_label"] for document in context_documents) if context_documents else "可視本文のみ"),
        "thread_purpose": purpose,
        "thread_purpose_label": purpose_label,
        "conversation_flow": briefing["briefing_label"],
        "conversation_flow_label": "直近の流れ: " + briefing["briefing_label"],
        "implicit_premises": premise_notes,
        "implicit_premises_label": "暗黙の前提候補: " + (" / ".join(premise_notes) if premise_notes else "まだ強い前提は見つかりません。"),
        "rule_notes": rule_notes,
        "rule_notes_label": "ルール注意: " + (" / ".join(rule_notes) if rule_notes else "直近可視範囲では明示ルールは見つかりません。"),
        "people_temperature": temperature,
        "people_temperature_label": temperature_label,
        "recent_authors": authors,
        "natural_entry_angles": natural_entry_angles,
        "context_ready": context_ready,
        "context_ready_label": "発話前チェックに使える文脈があります。" if context_ready else "文脈が不足しています。先にスレッド本文を取り込んでください。",
        "send_capability": "disabled",
        "send_capability_label": "このツールから Discord へ送信しません。",
    }


def context_passport_from_text(
    text: str,
    *,
    guild_label: str = "example-community",
    channel_label: str = "general",
    server_context: str = "",
    channel_context: str = "",
    thread_context: str = "",
) -> dict[str, Any]:
    events = parse_visible_text(text, guild_label=guild_label, channel_label=channel_label)
    return build_context_passport(
        events,
        context_documents=build_context_documents(
            server_context=server_context,
            channel_context=channel_context,
            thread_context=thread_context,
        ),
    )


def build_context_digestion(
    events: list[DiscordEvent],
    *,
    context_documents: list[dict[str, str]] | None = None,
    focus: str = "",
) -> dict[str, Any]:
    context_documents = context_documents or []
    joined = " ".join(event.text_snippet for event in events) + " " + context_documents_text(context_documents)
    purposes = sorted(detect_purposes(joined))
    topics = sorted(detect_topics(joined))
    purpose, purpose_label = summarize_thread_purpose(events, context_documents)
    temperature, temperature_label = classify_thread_temperature(events, context_documents)
    rule_notes = extract_context_rule_notes(context_documents) + extract_matching_snippets(events, RULE_KEYWORDS, limit=2)
    premise_count = len(
        extract_matching_snippets(events, ("前提", "つまり", "ここまで", "目的", "ルール", "まず"), limit=5)
    )
    focus_text = redact_artifact_text(focus)
    focus_matched = bool(focus_text and focus_text.casefold() in joined.casefold())
    understanding_layers = [
        "目的: " + purpose_label,
        "温度: " + temperature_label,
        "前提: "
        + (
            "直近可視範囲に前提候補があります。"
            if premise_count
            else "前提はまだ薄いです。必要なら追加で読みます。"
        ),
    ]
    if rule_notes:
        understanding_layers.append("制約: ルールや注意点らしき情報があります。")
    else:
        understanding_layers.append("制約: 直近可視範囲では強いルール注意は未検出です。")
    if focus_text:
        understanding_layers.append(
            "焦点: 指定 focus は文脈内に見えます。" if focus_matched else "焦点: 指定 focus は直近文脈だけでは未確認です。"
        )
    open_questions: list[str] = []
    if not events:
        open_questions.append("Discord 可視本文がまだありません。")
    if not topics and not purposes:
        open_questions.append("話題・目的がまだ薄いので、前後の発言を追加すると咀嚼精度が上がります。")
    if temperature in {"serious", "hot"}:
        open_questions.append("温度が高い可能性があるため、返信前に相手の意図を人間が確認してください。")
    if focus_text and not focus_matched:
        open_questions.append("focus と直近文脈の接続が未確認です。")
    next_actions = [
        "3〜8点の理解サマリを人間が確認する。",
        "返信へ進む場合は review-draft または stage-discord-send の理解 gate を通す。",
    ]
    if not events:
        next_actions.insert(0, "先に import-visible-text または snapshot-discord-url-text で可視本文を取り込む。")
    elif open_questions:
        next_actions.insert(0, "不足している前提だけを追加で読む。")
    else:
        next_actions.insert(0, "この咀嚼結果を返信前の理解メモとして使える。")
    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_context_digestion.v1",
        "message": "咀嚼モードの文脈整理を作成しました。",
        "parsed": len(events),
        "mode": "chew",
        "focus": focus_text,
        "focus_matched": focus_matched if focus_text else None,
        "thread_purpose": purpose,
        "thread_purpose_label": purpose_label,
        "people_temperature": temperature,
        "people_temperature_label": temperature_label,
        "detected_purposes": purposes,
        "detected_topics": topics,
        "understanding_layers": understanding_layers,
        "open_questions": open_questions,
        "next_actions": next_actions,
        "confidence": "visible_context_only" if events else "missing_context",
        "confidence_label": "可視範囲だけの仮整理です。" if events else "咀嚼する文脈が不足しています。",
        "safety_boundary": {
            "raw_text_returned": False,
            "participant_names_returned": False,
            "local_paths_returned": False,
            "outbound_actions": "disabled",
        },
        "send_capability": "disabled",
        "send_capability_label": "このツールから Discord へ送信しません。",
    }


def digest_context_from_text(
    text: str,
    *,
    guild_label: str = "example-community",
    channel_label: str = "general",
    server_context: str = "",
    channel_context: str = "",
    thread_context: str = "",
    focus: str = "",
) -> dict[str, Any]:
    events = parse_visible_text(text, guild_label=guild_label, channel_label=channel_label)
    return build_context_digestion(
        events,
        context_documents=build_context_documents(
            server_context=server_context,
            channel_context=channel_context,
            thread_context=thread_context,
        ),
        focus=focus,
    )


CONTEXT_OPERATING_MODES = ("triage", "catchup", "join-thread", "boundary")


def build_context_operating_mode(
    events: list[DiscordEvent],
    *,
    mode: str,
    context_documents: list[dict[str, str]] | None = None,
    focus: str = "",
) -> dict[str, Any]:
    if mode not in CONTEXT_OPERATING_MODES:
        raise ValueError("unknown context operating mode")
    context_documents = context_documents or []
    purpose, purpose_label = summarize_thread_purpose(events, context_documents)
    temperature, temperature_label = classify_thread_temperature(events, context_documents)
    rule_notes = extract_context_rule_notes(context_documents) + extract_matching_snippets(events, RULE_KEYWORDS, limit=2)
    focus_text = redact_artifact_text(focus)
    no_context = not events
    needs_care = temperature in {"serious", "hot"} or bool(rule_notes)

    if mode == "triage":
        route = "read_context" if no_context else "boundary_check" if rule_notes else "chew_context"
        checklist = ["可視本文の有無", "ルール注意の有無", "返信へ進む前の理解 gate"]
        next_actions = ["先に可視本文を取り込む。"] if no_context else ["chew-context で理解メモを作る。"]
    elif mode == "catchup":
        route = "read_more" if no_context or needs_care else "caught_up_for_light_entry"
        checklist = ["最初または固定投稿", "直近の流れ", "ルール・管理者メモ", "未読や stale snapshot"]
        next_actions = ["固定文・最新発言・ルール注意を確認する。"]
    elif mode == "join-thread":
        route = "observe_only" if no_context else "ask_before_join" if temperature == "hot" else "read_then_join"
        checklist = ["初参加として入る余地", "話題からの脱線", "自己紹介の長さ", "相手が訂正できる余白"]
        next_actions = ["短く、直近話題に接続して入る。"] if route == "read_then_join" else ["追加で読み、入室前に人間が判断する。"]
    else:
        route = "hold_for_human_go" if needs_care else "safe_to_continue"
        checklist = ["raw 本文を出さない", "URL・snowflake・local path を出さない", "外部送信しない", "ルール注意を見落とさない"]
        next_actions = ["必要なら人間確認を挟む。"] if needs_care else ["次のローカル処理へ進める。"]

    return {
        "language": DEFAULT_LANGUAGE,
        "schema": "discord_context_operating_mode.v1",
        "message": "Discord 文脈運用モードを作成しました。",
        "mode": mode,
        "parsed": len(events),
        "focus": focus_text,
        "route": route,
        "thread_purpose": purpose,
        "thread_purpose_label": purpose_label,
        "people_temperature": temperature,
        "people_temperature_label": temperature_label,
        "rule_attention": bool(rule_notes),
        "checklist": checklist,
        "next_actions": next_actions,
        "do_not_do": [
            "Discord へ自動送信しない。",
            "raw 本文・参加者名・URL・snowflake・local path を出力しない。",
            "初参加時に長い自己紹介や断定で流れを切らない。",
        ],
        "safety_boundary": {
            "raw_text_returned": False,
            "participant_names_returned": False,
            "local_paths_returned": False,
            "outbound_actions": "disabled",
        },
        "send_capability": "disabled",
        "send_capability_label": "このツールから Discord へ送信しません。",
    }


def context_operating_mode_from_text(
    text: str,
    *,
    mode: str,
    guild_label: str = "example-community",
    channel_label: str = "general",
    server_context: str = "",
    channel_context: str = "",
    thread_context: str = "",
    focus: str = "",
) -> dict[str, Any]:
    events = parse_visible_text(text, guild_label=guild_label, channel_label=channel_label)
    return build_context_operating_mode(
        events,
        mode=mode,
        context_documents=build_context_documents(
            server_context=server_context,
            channel_context=channel_context,
            thread_context=thread_context,
        ),
        focus=focus,
    )


UNDERSTANDING_GATE_OPTIONS = ["understanding-ok", "read-more", "wrong-thread", "missing-rules", "stop"]


def build_understanding_gate(
    *,
    understanding_confirmed: bool,
    context_ready: bool,
) -> dict[str, Any]:
    confirmed = bool(understanding_confirmed and context_ready)
    return {
        "schema": "discord_understanding_gate.v1",
        "understanding_confirmed": confirmed,
        "status": "confirmed" if confirmed else "blocked",
        "decision": "confirmed" if confirmed else "pending",
        "recommended_option": "understanding-ok" if confirmed else "read-more",
        "options": UNDERSTANDING_GATE_OPTIONS,
        "reason": ""
        if confirmed
        else "文脈理解が人間に確認されるまで、下書き・final candidate・copy block は生成しません。",
        "outbound_actions": "disabled",
    }


def build_blocked_copy_block(reason: str, *, max_chars: int = 2000) -> dict[str, Any]:
    return {
        "schema": "discord_copy_block.v1",
        "status": "blocked",
        "text": "",
        "parts": [],
        "part_count": 0,
        "max_chars": max_chars,
        "split_required": False,
        "stop_reason": reason,
        "outbound_actions": "disabled",
    }


def review_reply_intent(
    draft: str,
    events: Iterable[DiscordEvent],
    *,
    understanding_confirmed: bool = False,
) -> dict[str, Any]:
    loaded = list(events)
    gap = check_knowledge_gap(draft, loaded)
    draft_folded = draft.casefold()
    risky_tone = any(keyword in draft_folded for keyword in ("バカ", "黙れ", "最悪", "ふざけ", "攻撃", "怒"))
    no_context = not loaded
    needs_check = bool(gap["knowledge_gap"] or gap["topic_mismatch"])
    understanding_gate = build_understanding_gate(
        understanding_confirmed=understanding_confirmed,
        context_ready=not no_context,
    )
    if understanding_gate["status"] != "confirmed":
        copy_block = build_blocked_copy_block(str(understanding_gate["reason"]))
        human_gate = {
            "schema": "discord_human_gate.v1",
            "human_decision_required": True,
            "decision": "pending",
            "recommended_option": "read-more",
            "options": UNDERSTANDING_GATE_OPTIONS,
            "outbound_actions": "disabled",
        }
        return {
            "language": DEFAULT_LANGUAGE,
            "message": "文脈理解の確認待ちです。下書きと copy block は生成しません。",
            "ok_to_reply": "ask_first",
            "ok_to_reply_label": "先に文脈理解を確認してください。",
            "quick_verdict": "understanding-blocked",
            "quick_verdict_label": "read-more: 文脈理解の確認前なので下書きへ進めません。",
            "one_check_before_reply": "3〜8点の理解サマリを人間が確認してから下書きへ進んでください。",
            "alignment": "not_checked",
            "alignment_label": "文脈理解の確認前です。",
            "missing_knowledge": gap["knowledge_gap"],
            "missing_knowledge_label": "理解確認gateで停止中です。",
            "topic_warning_label": gap["topic_warning_label"],
            "likely_counterparty_meaning": fast_briefing(loaded)["briefing"],
            "suggested_correction": gap["recommended_briefing"],
            "understanding_gate": understanding_gate,
            "final_candidate": "",
            "human_gate": human_gate,
            "copy_block": copy_block,
            "send_capability": "disabled",
            "send_capability_label": "このツールから Discord へ送信しません。",
        }
    if risky_tone:
        quick_verdict = "risky"
        quick_verdict_label = "risky: そのまま出す前にトーンを落としてください。"
        one_check = "相手を傷つける言い方になっていないかだけ確認してください。"
    elif no_context:
        quick_verdict = "wait"
        quick_verdict_label = "wait: 文脈が足りないので今は待ちです。"
        one_check = "先にスレッド本文を取り込んでください。"
    elif needs_check:
        quick_verdict = "ask-context"
        quick_verdict_label = "ask-context: 前提を一つ確認してから入るのが安全です。"
        one_check = "自分の返信が直近の話題に乗っているか確認してください。"
    else:
        quick_verdict = "go"
        quick_verdict_label = "go: 短く入って問題なさそうです。"
        one_check = "送る前に固有名詞と前提だけ確認してください。"
    ok_to_reply = "ask_first" if needs_check else "likely_ok"
    alignment = "minor_gap" if needs_check else "aligned"
    missing_knowledge = gap["knowledge_gap"]
    missing_knowledge_label = (
        "不足している前提はありません。"
        if not missing_knowledge
        else "不足している前提: " + "、".join(missing_knowledge)
    )
    final_candidate = build_final_candidate(draft, quick_verdict=quick_verdict)
    copy_block = build_copy_block(final_candidate)
    human_gate = build_human_gate(
        quick_verdict=quick_verdict,
        ok_to_reply=ok_to_reply,
        copy_block_status=str(copy_block["status"]),
    )
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "返信前レビューが完了しました。",
        "ok_to_reply": ok_to_reply,
        "ok_to_reply_label": "先に確認した方がよさそうです。" if ok_to_reply == "ask_first" else "返信してよさそうです。",
        "quick_verdict": quick_verdict,
        "quick_verdict_label": quick_verdict_label,
        "one_check_before_reply": one_check,
        "alignment": alignment,
        "alignment_label": "文脈に不足があります。" if alignment == "minor_gap" else "文脈に合っています。",
        "missing_knowledge": missing_knowledge,
        "missing_knowledge_label": missing_knowledge_label,
        "topic_warning_label": gap["topic_warning_label"],
        "likely_counterparty_meaning": fast_briefing(loaded)["briefing"],
        "suggested_correction": gap["recommended_briefing"] if needs_check else "",
        "understanding_gate": understanding_gate,
        "final_candidate": final_candidate,
        "human_gate": human_gate,
        "copy_block": copy_block,
        "send_capability": "disabled",
        "send_capability_label": "このツールから Discord へ送信しません。",
    }


def build_final_candidate(draft: str, *, quick_verdict: str) -> str:
    safe_draft = redact_artifact_text(draft) or "未入力"
    if quick_verdict in {"risky", "wait"}:
        return "送信前に人間が修正してください: " + safe_draft
    return safe_draft


def build_human_gate(*, quick_verdict: str, ok_to_reply: str, copy_block_status: str) -> dict[str, Any]:
    decision_required = True
    if quick_verdict in {"risky", "wait"}:
        recommended = "wait"
    elif ok_to_reply == "ask_first":
        recommended = "read-more"
    elif copy_block_status == "blocked":
        recommended = "edit"
    else:
        recommended = "copy"
    return {
        "schema": "discord_human_gate.v1",
        "human_decision_required": decision_required,
        "decision": "pending",
        "recommended_option": recommended,
        "options": ["copy", "edit", "read-more", "wait", "no-reply"],
        "outbound_actions": "disabled",
    }


def build_copy_block(candidate: str, *, max_chars: int = 2000, max_parts: int = 2) -> dict[str, Any]:
    safe_candidate = redact_artifact_text(candidate) or "未入力"
    if len(safe_candidate) <= max_chars:
        return {
            "schema": "discord_copy_block.v1",
            "status": "ready",
            "text": safe_candidate,
            "parts": [safe_candidate],
            "part_count": 1,
            "max_chars": max_chars,
            "split_required": False,
            "stop_reason": "",
            "outbound_actions": "disabled",
        }
    parts = [safe_candidate[index : index + max_chars] for index in range(0, len(safe_candidate), max_chars)]
    if len(parts) > max_parts:
        return {
            "schema": "discord_copy_block.v1",
            "status": "blocked",
            "text": "",
            "parts": [],
            "part_count": len(parts),
            "max_chars": max_chars,
            "split_required": True,
            "stop_reason": "copy block が3分割以上になるため、短く編集してから再レビューしてください。",
            "outbound_actions": "disabled",
        }
    return {
        "schema": "discord_copy_block.v1",
        "status": "split",
        "text": "",
        "parts": parts,
        "part_count": len(parts),
        "max_chars": max_chars,
        "split_required": True,
        "stop_reason": "",
        "outbound_actions": "disabled",
    }


def redact_artifact_text(text: str) -> str:
    redacted = DISCORD_WEBHOOK_RE.sub("[discord webhook omitted]", text)
    redacted = DISCORD_TOKEN_RE.sub("[discord token omitted]", redacted)
    redacted = LOCAL_ABSOLUTE_PATH_RE.sub("[local path omitted]", redacted)
    redacted = DISCORD_SNOWFLAKE_RE.sub("[discord id omitted]", redacted)
    redacted = re.sub(r"https?://\S+", "[url omitted]", redacted)
    redacted = re.sub(r"\bmember-[A-Za-z0-9_-]+\b", "safe-member", redacted)
    return redacted.strip()


def build_review_artifact_markdown(
    draft: str,
    review: dict[str, Any],
    *,
    title: str = "Discord review artifact",
) -> str:
    safe_draft = redact_artifact_text(draft) or "未入力"
    understanding_gate = dict(review.get("understanding_gate") or {})
    understanding_confirmed = understanding_gate.get("status") == "confirmed"
    final_candidate = redact_artifact_text(str(review.get("final_candidate") or safe_draft))
    human_gate = dict(review.get("human_gate") or {})
    copy_block = dict(review.get("copy_block") or build_copy_block(final_candidate))
    missing = review.get("missing_knowledge") or []
    missing_label = "、".join(str(item) for item in missing) if missing else "なし"
    suggested_check = redact_artifact_text(str(review.get("one_check_before_reply") or "送信前に人間が確認してください。"))
    quick_verdict_label = redact_artifact_text(str(review.get("quick_verdict_label") or "未判定"))
    ok_to_reply_label = redact_artifact_text(str(review.get("ok_to_reply_label") or "未判定"))
    alignment_label = redact_artifact_text(str(review.get("alignment_label") or "未判定"))
    topic_warning_label = redact_artifact_text(str(review.get("topic_warning_label") or "未判定"))
    draft_section = safe_draft if understanding_confirmed else "blocked: understanding_confirmed=false"
    final_candidate_section = final_candidate if understanding_confirmed else "blocked: understanding_confirmed=false"

    return "\n".join(
        [
            f"# {redact_artifact_text(title)}",
            "",
            "> do_not_post: この artifact は送信前レビュー用です。Discord へ自動送信しません。",
            "",
            "## 1. 文脈理解",
            "",
            f"- verdict: {quick_verdict_label}",
            f"- context_fit: {alignment_label}",
            f"- topic_check: {topic_warning_label}",
            f"- missing_premise: {missing_label}",
            f"- understanding_gate: {redact_artifact_text(str(understanding_gate.get('status') or 'not_recorded'))}",
            "",
            "## 2. provisional draft",
            "",
            draft_section,
            "",
            "## 3. risk review",
            "",
            f"- reply_state: {ok_to_reply_label}",
            f"- one_check_before_reply: {suggested_check}",
            "- outbound_actions: disabled",
            "",
            "## 4. unknown / next checks",
            "",
            f"- {suggested_check}",
            "- 追加読みが必要なら human gate で止める。",
            "",
            "## 5. human gate",
            "",
            f"- human_decision_required: {str(human_gate.get('human_decision_required', True)).lower()}",
            f"- decision: {redact_artifact_text(str(human_gate.get('decision') or 'pending'))}",
            f"- recommended_option: {redact_artifact_text(str(human_gate.get('recommended_option') or 'copy'))}",
            "- options: copy / edit / read-more / wait / no-reply",
            "",
            "## 6. final candidate",
            "",
            final_candidate_section,
            "",
            "## 7. copy block",
            "",
            *format_copy_block_markdown(copy_block),
            "",
            "## safety boundary",
            "",
            "- raw_discord_text_output: omitted",
            "- participant_names_output: omitted",
            "- text_returned: false",
            "- outbound_actions: disabled",
            "",
        ]
    )


def format_copy_block_markdown(copy_block: dict[str, Any]) -> list[str]:
    status = str(copy_block.get("status") or "blocked")
    if status == "ready":
        return [
            "status: ready",
            "",
            "```text",
            redact_artifact_text(str(copy_block.get("text") or "")),
            "```",
        ]
    if status == "split":
        lines = ["status: split", f"part_count: {copy_block.get('part_count', 0)}", ""]
        for index, part in enumerate(copy_block.get("parts") or [], start=1):
            lines.extend([f"### part {index}", "", "```text", redact_artifact_text(str(part)), "```", ""])
        return lines
    return [
        "status: blocked",
        f"part_count: {copy_block.get('part_count', 0)}",
        redact_artifact_text(str(copy_block.get("stop_reason") or "copy block を短く編集してください。")),
    ]


def guide_reply_from_text(
    text: str,
    draft: str,
    *,
    guild_label: str = "example-community",
    channel_label: str = "general",
    understanding_confirmed: bool = False,
) -> dict[str, Any]:
    events = parse_visible_text(text, guild_label=guild_label, channel_label=channel_label)
    briefing = fast_briefing(events)
    review = review_reply_intent(draft, events, understanding_confirmed=understanding_confirmed)
    next_actions = ["そのまま送らず、Discord 側で人間が確認してから返信してください。"]
    if review["missing_knowledge"]:
        next_actions.insert(0, "不足している前提を相手に確認してください。")
    if review["topic_warning_label"] != "話題の大きなズレは見つかりません。":
        next_actions.insert(0, review["topic_warning_label"])
    elif not review["missing_knowledge"]:
        next_actions.insert(0, "文脈と返信意図は大きくずれていません。")
    return {
        "language": DEFAULT_LANGUAGE,
        "message": "Discord 返信ガイドを作成しました。",
        "parsed": len(events),
        "counterparty_context": briefing["briefing_label"],
        "reply_review": review,
        "next_actions": next_actions,
        "send_capability": "disabled",
        "send_capability_label": "このツールから Discord へ送信しません。",
    }


DISCORD_MESSAGE_URL_RE = re.compile(
    r"^https://discord(?:app)?\.com/channels/(?P<guild>\d{17,20})/(?P<channel>\d{17,20})/(?P<message>\d{17,20})(?:\?.*)?$"
)
DISCORD_CHANNEL_URL_RE = re.compile(
    r"^https://discord(?:app)?\.com/channels/(?P<guild>\d{17,20})/(?P<channel>\d{17,20})(?:\?.*)?$"
)
DISCORD_FILL_ONLY_FORBIDDEN_ACTIONS = (
    "press_enter_to_send",
    "click_send_button",
    "send_message",
    "react",
    "edit",
    "delete",
)


def _enforce_discord_fill_only_guard(packet: dict[str, Any]) -> dict[str, Any]:
    browser_action = packet.get("browser_action") or {}
    allowed_actions = set(browser_action.get("allowed_actions") or [])
    forbidden_actions = set(browser_action.get("forbidden_actions") or [])
    overlap = sorted(allowed_actions & forbidden_actions)
    if overlap:
        raise RuntimeError(f"stage-discord-send fill-only guard conflict: {', '.join(overlap)}")
    if not set(DISCORD_FILL_ONLY_FORBIDDEN_ACTIONS).issubset(forbidden_actions):
        raise RuntimeError("stage-discord-send fill-only guard is missing forbidden send actions")
    if browser_action.get("capability") != "chrome_extension_fill_only":
        raise RuntimeError("stage-discord-send must remain chrome_extension_fill_only")
    if "stop_before_send_button" not in allowed_actions:
        raise RuntimeError("stage-discord-send must stop before the send button")
    guard = packet.get("stage_guard") or {}
    if guard.get("external_action") != "none_until_human_send" or guard.get("human_send_required") is not True:
        raise RuntimeError("stage-discord-send external action boundary is not fill-only")
    if packet.get("send_capability") != "disabled" or packet.get("outbound_actions") != "disabled":
        raise RuntimeError("stage-discord-send cannot enable outbound actions")
    return packet


def build_discord_send_staging_packet(
    draft: str,
    events: Iterable[DiscordEvent],
    *,
    mode: str = "reply",
    target_url: str = "",
    mention_label: str = "",
    understanding_confirmed: bool = False,
) -> dict[str, Any]:
    """Build a fill-only browser action packet for a Discord reply/mention.

    This deliberately does not send. It only tells a Chrome-extension runner how
    far it may go before the human performs the final Discord action.
    """
    normalized_mode = mode.strip().casefold() or "reply"
    if normalized_mode not in {"reply", "mention"}:
        normalized_mode = "unsupported"
    loaded = list(events)
    review = review_reply_intent(draft, loaded, understanding_confirmed=understanding_confirmed)
    copy_block = dict(review.get("copy_block") or {})
    blockers: list[str] = []
    target_kind = "unknown"
    if normalized_mode == "unsupported":
        blockers.append("unsupported_mode")
    if not understanding_confirmed:
        blockers.append("understanding_not_confirmed")
    if copy_block.get("status") not in {"ready", "split"}:
        blockers.append("copy_block_not_ready")
    if normalized_mode == "reply":
        if DISCORD_MESSAGE_URL_RE.match(target_url):
            target_kind = "message"
        else:
            blockers.append("reply_target_message_url_required")
    elif normalized_mode == "mention":
        if DISCORD_CHANNEL_URL_RE.match(target_url) or DISCORD_MESSAGE_URL_RE.match(target_url):
            target_kind = "channel_or_message"
        else:
            blockers.append("mention_target_discord_url_required")
        if not mention_label.strip().startswith("@"):
            blockers.append("mention_label_required")
    staging_status = "ready_to_fill" if not blockers else "blocked"
    browser_steps = [
        "socket_preflight",
        "claim_existing_discord_tab_or_open_target",
        "verify_visible_url_matches_target",
    ]
    if normalized_mode == "reply":
        browser_steps.extend(["open_message_reply_ui", "fill_reply_box"])
    elif normalized_mode == "mention":
        browser_steps.extend(["focus_message_box", "insert_human_verified_mention_then_draft"])
    browser_steps.extend(["socket_pre_send_ping", "stop_before_send_button"])
    packet = {
        "schema": "discord_send_staging_packet.v1",
        "language": DEFAULT_LANGUAGE,
        "message": "Discord 送信準備パケットを作成しました。" if staging_status == "ready_to_fill" else "Discord 送信準備は gate で停止しました。",
        "mode": normalized_mode,
        "staging_status": staging_status,
        "blockers": blockers,
        "target_kind": target_kind,
        "target_url_output": "omitted",
        "mention_label_output": redact_artifact_text(mention_label) if mention_label else "",
        "review": {
            "quick_verdict": review.get("quick_verdict"),
            "ok_to_reply": review.get("ok_to_reply"),
            "human_gate": review.get("human_gate"),
        },
        "copy_block": copy_block,
        "browser_action": {
            "capability": "chrome_extension_fill_only",
            "allowed_actions": browser_steps,
            "forbidden_actions": list(DISCORD_FILL_ONLY_FORBIDDEN_ACTIONS),
            "socket_checks": ["preflight", "after_navigation", "pre_send"],
            "double_submit_guard": "stop_before_send_button",
        },
        "stage_guard": {
            "schema": "discord_fill_only_guard.v1",
            "max_runner_action": "fill_draft_only",
            "external_action": "none_until_human_send",
            "latest_target_snapshot_required": True,
            "latest_target_snapshot_check": "required_before_user_action",
            "stop_condition": "stop_before_send_button",
            "human_send_required": True,
        },
        "human_gate": {
            "schema": "discord_send_human_gate.v1",
            "human_decision_required": True,
            "decision": "pending",
            "recommended_option": "fill-draft" if staging_status == "ready_to_fill" else "fix-blockers",
            "options": ["fill-draft", "edit", "read-more", "stop"],
            "outbound_actions": "disabled",
        },
        "send_capability": "disabled",
        "send_capability_label": "この packet は下書き入力までです。Discord 送信は人間が最後に実行してください。",
        "outbound_actions": "disabled",
        "raw_discord_text_output": "omitted",
    }
    return _enforce_discord_fill_only_guard(packet)


def verify_chrome_extension_fill_only_dry_run(
    staging_packet: dict[str, Any],
    *,
    socket_preflight: bool = False,
    target_url_verified: bool = False,
    socket_after_navigation: bool = False,
    latest_target_snapshot_confirmed: bool = False,
    reply_ui_candidates: int = 0,
    message_box_candidates: int = 0,
    draft_matches_copy_block: bool = False,
    socket_pre_send: bool = False,
) -> dict[str, Any]:
    """Validate browser observations before a Chrome runner may fill a draft.

    A ready staging packet only proves that the draft and safe metadata passed
    the send-prep gate. This dry-run gate proves the visible browser surface is
    also narrow enough to fill without sending.
    """
    mode = str(staging_packet.get("mode") or "").strip().casefold()
    blockers: list[str] = []
    if staging_packet.get("schema") != "discord_send_staging_packet.v1":
        blockers.append("invalid_staging_packet")
    if staging_packet.get("staging_status") != "ready_to_fill":
        blockers.append("staging_packet_not_ready")
    if not socket_preflight:
        blockers.append("socket_preflight_missing")
    if not target_url_verified:
        blockers.append("target_url_not_verified")
    if not socket_after_navigation:
        blockers.append("socket_after_navigation_missing")
    if not latest_target_snapshot_confirmed:
        blockers.append("latest_target_snapshot_not_confirmed")
    if mode == "reply":
        if reply_ui_candidates < 1:
            blockers.append("reply_ui_not_found")
        elif reply_ui_candidates > 1:
            blockers.append("reply_ui_not_unique")
    elif mode == "mention":
        if message_box_candidates < 1:
            blockers.append("message_box_not_found")
        elif message_box_candidates > 1:
            blockers.append("message_box_not_unique")
    else:
        blockers.append("unsupported_mode")
    if not draft_matches_copy_block:
        blockers.append("draft_mismatch")
    if not socket_pre_send:
        blockers.append("socket_pre_send_missing")

    dry_run_status = "ready_to_fill" if not blockers else "blocked"
    return {
        "schema": "chrome_extension_fill_only_dry_run.v1",
        "language": DEFAULT_LANGUAGE,
        "message": "Chrome 拡張 fill-only dry-run は入力可能です。"
        if dry_run_status == "ready_to_fill"
        else "Chrome 拡張 fill-only dry-run は gate で停止しました。",
        "dry_run_status": dry_run_status,
        "fill_permitted": dry_run_status == "ready_to_fill",
        "blockers": blockers,
        "observed": {
            "socket_preflight": socket_preflight,
            "target_url_verified": target_url_verified,
            "socket_after_navigation": socket_after_navigation,
            "latest_target_snapshot_confirmed": latest_target_snapshot_confirmed,
            "reply_ui_candidates": reply_ui_candidates,
            "message_box_candidates": message_box_candidates,
            "draft_matches_copy_block": draft_matches_copy_block,
            "socket_pre_send": socket_pre_send,
        },
        "required_stop": "stop_before_send_button",
        "allowed_next_action": "fill_draft_then_stop" if dry_run_status == "ready_to_fill" else "fix_blockers",
        "forbidden_actions": list(DISCORD_FILL_ONLY_FORBIDDEN_ACTIONS),
        "outbound_actions": "disabled",
        "send_capability": "disabled",
        "send_capability_label": "このツールから Discord へ送信しません。",
    }


def build_discord_post_send_closeout_packet(
    *,
    staging_packet: dict[str, Any] | None = None,
    dry_run_report: dict[str, Any] | None = None,
    human_sent_observed: bool = False,
    human_reviewed: bool = False,
    observed_text_status: str = "not_checked",
    unread_check_status: str = "not_checked",
    unread_signal_count: int = 0,
    observed_message_id: str = "",
    observed_url: str = "",
    note_label: str = "",
) -> dict[str, Any]:
    """Build a metadata-only closeout after the human performs final send.

    This records only state transitions. It never returns Discord body text,
    message URLs, or snowflake values.
    """
    normalized_text_status = observed_text_status.strip().lower().replace("-", "_") or "not_checked"
    normalized_unread_status = unread_check_status.strip().lower().replace("-", "_") or "not_checked"
    allowed_text_statuses = {
        "matches_copy_block",
        "human_edited_and_reviewed",
        "not_checked",
    }
    allowed_unread_statuses = {
        "none_unread",
        "has_unread",
        "not_checked",
    }
    safe_unread_signal_count = max(0, int(unread_signal_count))
    blockers: list[str] = []
    if staging_packet is not None:
        if (
            staging_packet.get("schema") != "discord_send_staging_packet.v1"
            or staging_packet.get("staging_status") != "ready_to_fill"
        ):
            blockers.append("staging_packet_not_ready")
    if dry_run_report is not None:
        if (
            dry_run_report.get("schema") != "chrome_extension_fill_only_dry_run.v1"
            or dry_run_report.get("dry_run_status") != "ready_to_fill"
            or dry_run_report.get("fill_permitted") is not True
        ):
            blockers.append("dry_run_not_ready")
    if not human_sent_observed:
        blockers.append("human_send_not_observed")
    if not human_reviewed:
        blockers.append("human_review_not_confirmed")
    if normalized_text_status not in allowed_text_statuses:
        blockers.append("invalid_observed_text_status")
    elif normalized_text_status == "not_checked":
        blockers.append("observed_text_not_checked")
    if normalized_unread_status not in allowed_unread_statuses:
        blockers.append("invalid_unread_check_status")
    elif normalized_unread_status == "not_checked":
        blockers.append("unread_not_checked")
    elif normalized_unread_status == "has_unread" or safe_unread_signal_count > 0:
        blockers.append("unread_items_remaining")

    closeout_status = "closed" if not blockers else "blocked"
    if closeout_status == "closed":
        recommended_next_state = "done"
    elif "human_send_not_observed" in blockers:
        recommended_next_state = "verify_visible_message"
    elif "human_review_not_confirmed" in blockers:
        recommended_next_state = "human_review_required"
    elif "unread_not_checked" in blockers:
        recommended_next_state = "check_unread_items"
    elif "unread_items_remaining" in blockers:
        recommended_next_state = "review_unread_items"
    else:
        recommended_next_state = "fix_blockers"
    return {
        "schema": "discord_post_send_closeout_packet.v1",
        "language": DEFAULT_LANGUAGE,
        "message": "Discord 送信後 closeout は完了しました。"
        if closeout_status == "closed"
        else "Discord 送信後 closeout は gate で停止しました。",
        "closeout_status": closeout_status,
        "blockers": blockers,
        "human_sent_observed": human_sent_observed,
        "human_reviewed": human_reviewed,
        "observed_text_status": normalized_text_status,
        "unread_check_status": normalized_unread_status,
        "unread_signal_count": safe_unread_signal_count,
        "observed_message_id_output": "omitted" if observed_message_id else "not_provided",
        "observed_url_output": "omitted" if observed_url else "not_provided",
        "note_label_output": redact_artifact_text(note_label) if note_label else "",
        "staging_packet_status": str(staging_packet.get("staging_status") or "provided")
        if staging_packet is not None
        else "not_provided",
        "dry_run_status": str(dry_run_report.get("dry_run_status") or "provided")
        if dry_run_report is not None
        else "not_provided",
        "recommended_next_state": recommended_next_state,
        "text_returned": False,
        "raw_discord_text_output": "omitted",
        "outbound_actions": "disabled",
        "send_capability": "disabled",
        "send_capability_label": "この closeout は送信後 metadata の確認だけです。Discord への操作は実行しません。",
    }


def _operation_check(
    name: str,
    ok: bool,
    *,
    evidence: str,
    blocker: str | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": "ok" if ok else "pending",
        "evidence": evidence,
    }
    if blocker:
        payload["blocker"] = blocker
    if next_action:
        payload["next_action"] = next_action
    return payload


def build_discord_send_operation_status(
    *,
    staging_packet: dict[str, Any] | None = None,
    dry_run_report: dict[str, Any] | None = None,
    closeout_report: dict[str, Any] | None = None,
    target_label: str = "",
    target_environment: str = "test",
    rollback_plan_reviewed: bool = False,
    production_runbook_fixed: bool = False,
) -> dict[str, Any]:
    """Summarize a human-assisted Discord send workflow from existing logs.

    This function deliberately consumes metadata-only gate reports. It does not
    send to Discord, and it does not echo raw URLs, snowflakes, or message text.
    """
    safe_target_label = redact_artifact_text(target_label.strip()) if target_label.strip() else ""
    environment = target_environment.strip().casefold() or "test"
    if environment not in {"test", "production"}:
        environment = "test"

    staging_ready = bool(
        staging_packet
        and staging_packet.get("schema") == "discord_send_staging_packet.v1"
        and staging_packet.get("staging_status") == "ready_to_fill"
    )
    staging_target_known = bool(
        staging_packet
        and staging_packet.get("target_kind") in {"message", "channel_or_message"}
        and staging_packet.get("target_url_output") == "omitted"
    )
    copy_block_ready = bool(
        staging_packet
        and (staging_packet.get("copy_block") or {}).get("status") in {"ready", "split"}
    )
    dry_run_ready = bool(
        dry_run_report
        and dry_run_report.get("schema") == "chrome_extension_fill_only_dry_run.v1"
        and dry_run_report.get("dry_run_status") == "ready_to_fill"
        and dry_run_report.get("fill_permitted") is True
    )
    closeout_closed = bool(
        closeout_report
        and closeout_report.get("schema") == "discord_post_send_closeout_packet.v1"
        and closeout_report.get("closeout_status") == "closed"
        and closeout_report.get("recommended_next_state") == "done"
    )
    closeout_unread_clear = bool(closeout_report and closeout_report.get("unread_check_status") == "none_unread")

    checks = [
        _operation_check(
            "target_destination_declared",
            bool(safe_target_label and staging_target_known),
            evidence="target_label + staging_packet.target_kind",
            blocker=None if safe_target_label else "target_label_missing",
            next_action="対象チャンネル/投稿先を safe label で指定する" if not safe_target_label else None,
        ),
        _operation_check(
            "draft_reviewed",
            bool(staging_ready and copy_block_ready),
            evidence="stage-discord-send review/copy_block",
            blocker=None if staging_ready and copy_block_ready else "staging_packet_not_ready",
            next_action="review-draft と stage-discord-send を通す" if not (staging_ready and copy_block_ready) else None,
        ),
        _operation_check(
            "dry_run_or_preview_ready",
            dry_run_ready,
            evidence="verify-chrome-fill-dry-run",
            blocker=None if dry_run_ready else "dry_run_not_ready",
            next_action="Chrome fill-only dry-run を ready_to_fill にする" if not dry_run_ready else None,
        ),
        _operation_check(
            "test_channel_send_observed",
            bool(environment == "test" and closeout_closed),
            evidence="closeout-discord-send on test target",
            blocker=None if environment == "test" and closeout_closed else "test_send_closeout_missing",
            next_action="テスト用チャンネルで人間送信し closeout-discord-send を closed にする"
            if not (environment == "test" and closeout_closed)
            else None,
        ),
        _operation_check(
            "send_log_and_failure_recovery_checked",
            bool(closeout_closed and closeout_unread_clear and rollback_plan_reviewed),
            evidence="closeout-discord-send + rollback_plan_reviewed",
            blocker=None
            if closeout_closed and closeout_unread_clear and rollback_plan_reviewed
            else "send_log_or_recovery_plan_missing",
            next_action="送信後 closeout と、失敗時の修正投稿/停止/人間確認手順を確認する"
            if not (closeout_closed and closeout_unread_clear and rollback_plan_reviewed)
            else None,
        ),
        _operation_check(
            "production_send_procedure_fixed",
            bool(production_runbook_fixed),
            evidence="docs/discord-send-operation-runbook.md",
            blocker=None if production_runbook_fixed else "production_runbook_not_confirmed",
            next_action="本番送信前の固定手順をレビュー済みにする" if not production_runbook_fixed else None,
        ),
    ]
    ok = all(check["status"] == "ok" for check in checks)
    return {
        "schema": "discord_send_operation_status.v1",
        "language": DEFAULT_LANGUAGE,
        "ok": ok,
        "state": "ready_for_production_human_send" if ok else "attention_required",
        "target": {
            "label": safe_target_label or "not_provided",
            "environment": environment,
            "url_output": "omitted",
        },
        "checks": checks,
        "summary": {
            "ready_count": sum(1 for check in checks if check["status"] == "ok"),
            "total_count": len(checks),
            "next": [check["next_action"] for check in checks if check.get("next_action")],
        },
        "safety_boundary": {
            "discord_send_executed_by_this_tool": False,
            "raw_discord_text_output": "omitted",
            "target_url_output": "omitted",
            "snowflake_values_output": "omitted",
            "outbound_actions": "disabled",
        },
    }


def send_message(*_: Any, **__: Any) -> None:
    raise DisabledCapability("Discord への送信機能は、この public nucleus では意図的に無効です。")
