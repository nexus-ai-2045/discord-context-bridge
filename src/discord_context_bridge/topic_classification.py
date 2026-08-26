from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import DiscordEvent, load_text_snapshots
from .knowledge_projection import (
    _extract_topics,
    _load_topic_registry,
    _parse_knowledge_events,
    _projection_records,
    _stable_id,
    _structured_message_identity,
)


PACKET_SCHEMA = "dcb.topic_classification_packet.v1"
RESULT_SCHEMA = "dcb.topic_classification_result.v1"
PROPOSAL_SCHEMA = "dcb.topic_classification_proposal.v1"
MODEL_ROUTE = "gpt-5.3-codex-spark"
PROMPT_VERSION = "dcb-topic-candidate-v1"


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_proposed_observation_ids(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    proposed: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if (
                record.get("schema") != PROPOSAL_SCHEMA
                or not str(record.get("proposal_id") or "")
                or not str(record.get("observation_id") or "")
            ):
                raise ValueError("invalid proposal ledger")
            observation_id = str(record.get("observation_id") or "")
            if observation_id:
                proposed.add(observation_id)
    return proposed


def _candidate_events(snapshot_store: Path) -> list[dict[str, str]]:
    records = load_text_snapshots(snapshot_store)
    candidates: list[dict[str, str]] = []
    for record in _projection_records(records):
        target = str(record.get("target_key") or record.get("stream_id") or "unknown")
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
            observation_parts = [target, content_hash, str(index)]
            if record.get("event_type") == "message_observation":
                observation_parts.append(_structured_message_identity(record))
            candidates.append(
                {
                    "observation_id": _stable_id("observation", *observation_parts),
                    "stream_ref": _stable_id("stream", target),
                    "observed_at": observed_at,
                    "content_hash": hashlib.sha256(
                        parsed.text_snippet.encode("utf-8")
                    ).hexdigest(),
                    "text": parsed.text_snippet,
                    "explicit_topics": "\0".join(
                        _extract_topics(parsed.text_snippet)
                    ),
                }
            )
    return candidates


def build_topic_classification_packet(
    *,
    snapshot_store: Path,
    topic_registry: Path,
    output_path: Path,
    proposal_ledger: Path | None = None,
    max_items: int = 100,
) -> dict[str, Any]:
    if max_items <= 0:
        raise ValueError("max_items must be greater than zero")
    if not snapshot_store.is_file():
        return {
            "schema": PACKET_SCHEMA,
            "ok": False,
            "reason": "snapshot_store_missing",
            "private_local_only": True,
            "outbound_actions": "disabled",
            "paths_returned": False,
        }
    (
        topics,
        assignments,
        _,
        broader_topics,
        related_topics,
        _,
    ) = _load_topic_registry(topic_registry)
    proposed = _load_proposed_observation_ids(proposal_ledger)
    pending = [
        item
        for item in _candidate_events(snapshot_store)
        if not item["explicit_topics"]
        and item["observation_id"] not in assignments
        and item["observation_id"] not in proposed
    ]
    selected = pending[:max_items]
    taxonomy = [
        {
            "topic_id": topic_id,
            "label": label,
            "broader_topic_ids": broader_topics.get(topic_id, []),
            "related_topic_ids": related_topics.get(topic_id, []),
        }
        for topic_id, (_, label) in sorted(topics.items())
    ]
    source_fingerprint = _json_fingerprint(
        [(item["observation_id"], item["content_hash"]) for item in selected]
    )
    packet_id = _stable_id(
        "topic-packet",
        source_fingerprint,
        _json_fingerprint(taxonomy),
        PROMPT_VERSION,
    )
    packet = {
        "schema": PACKET_SCHEMA,
        "packet_id": packet_id,
        "model_route": MODEL_ROUTE,
        "prompt_version": PROMPT_VERSION,
        "private_local_only": True,
        "contains_raw_discord_text": True,
        "external_send_approved": False,
        "review_policy": "proposal_only_human_promotion_required",
        "source_fingerprint": source_fingerprint,
        "taxonomy": taxonomy,
        "items": [
            {
                "observation_id": item["observation_id"],
                "stream_ref": item["stream_ref"],
                "observed_at": item["observed_at"],
                "content_hash": item["content_hash"],
                "text": item["text"],
            }
            for item in selected
        ],
        "instructions": {
            "task": "Propose zero or more topic candidates for every item.",
            "do_not": [
                "identify people",
                "invent facts",
                "promote proposals to reviewed assignments",
                "repeat raw text in the result",
            ],
            "result_schema": RESULT_SCHEMA,
        },
    }
    _atomic_text(
        output_path,
        json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return {
        "schema": PACKET_SCHEMA,
        "ok": True,
        "packet_id": packet_id,
        "candidate_count": len(selected),
        "remaining_candidate_count": max(0, len(pending) - len(selected)),
        "taxonomy_count": len(taxonomy),
        "private_local_only": True,
        "contains_raw_discord_text": True,
        "external_send_approved": False,
        "outbound_actions": "disabled",
        "paths_returned": False,
    }


def _validated_result(packet: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    if (
        result.get("schema") != RESULT_SCHEMA
        or result.get("packet_id") != packet.get("packet_id")
        or result.get("model") != MODEL_ROUTE
        or not isinstance(result.get("items"), list)
    ):
        raise ValueError("invalid classification result envelope")
    expected = {str(item["observation_id"]): item for item in packet["items"]}
    taxonomy_ids = {str(item["topic_id"]) for item in packet["taxonomy"]}
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in result["items"]:
        observation_id = str(item.get("observation_id") or "")
        topics = item.get("topics")
        abstain_reason = str(item.get("abstain_reason") or "").strip()
        if (
            observation_id not in expected
            or observation_id in seen
            or not isinstance(topics, list)
            or (not topics and not abstain_reason)
        ):
            raise ValueError("invalid classification result item")
        seen.add(observation_id)
        normalized_topics: list[dict[str, Any]] = []
        for topic in topics:
            existing_topic_id = str(topic.get("existing_topic_id") or "").strip()
            proposed_label = str(topic.get("proposed_label") or "").strip()
            confidence = topic.get("confidence")
            reason = str(topic.get("reason") or "").strip()
            if (
                bool(existing_topic_id) == bool(proposed_label)
                or (existing_topic_id and existing_topic_id not in taxonomy_ids)
                or not isinstance(confidence, (int, float))
                or not 0 <= float(confidence) <= 1
                or not reason
                or len(reason) > 500
            ):
                raise ValueError("invalid topic candidate")
            normalized_topics.append(
                {
                    "existing_topic_id": existing_topic_id or None,
                    "proposed_label": proposed_label or None,
                    "confidence": float(confidence),
                    "reason": reason,
                }
            )
        validated.append(
            {
                "observation_id": observation_id,
                "content_hash": expected[observation_id]["content_hash"],
                "topics": normalized_topics,
                "abstain_reason": abstain_reason,
            }
        )
    if seen != set(expected):
        raise ValueError("classification result is incomplete")
    return validated


def import_topic_classification_result(
    *, packet_path: Path, result_path: Path, proposal_ledger: Path
) -> dict[str, Any]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if packet.get("schema") != PACKET_SCHEMA:
        raise ValueError("invalid classification packet")
    validated = _validated_result(packet, result)
    existing_ids: set[str] = set()
    if proposal_ledger.exists():
        with proposal_ledger.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    record = json.loads(line)
                    proposal_id = str(record.get("proposal_id") or "")
                    if record.get("schema") != PROPOSAL_SCHEMA or not proposal_id:
                        raise ValueError("invalid proposal ledger")
                    existing_ids.add(proposal_id)
    recorded_at = datetime.now(timezone.utc).isoformat()
    records: list[dict[str, Any]] = []
    for item in validated:
        proposal_id = _stable_id(
            "topic-proposal",
            str(packet["packet_id"]),
            item["observation_id"],
            _json_fingerprint(item),
        )
        if proposal_id in existing_ids:
            continue
        records.append(
            {
                "schema": PROPOSAL_SCHEMA,
                "proposal_id": proposal_id,
                "packet_id": packet["packet_id"],
                "observation_id": item["observation_id"],
                "content_hash": item["content_hash"],
                "model": MODEL_ROUTE,
                "prompt_version": PROMPT_VERSION,
                "topics": item["topics"],
                "abstain_reason": item["abstain_reason"],
                "review_status": "pending_human_review",
                "recorded_at": recorded_at,
                "private_local_only": True,
            }
        )
    if records:
        proposal_ledger.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ).encode("utf-8")
        descriptor = os.open(
            proposal_ledger, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("proposal ledger append failed")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {
        "schema": PROPOSAL_SCHEMA,
        "ok": True,
        "packet_id": packet["packet_id"],
        "validated_item_count": len(validated),
        "appended_proposal_count": len(records),
        "pending_human_review_count": sum(
            1 for item in validated if item["topics"]
        ),
        "abstained_count": sum(1 for item in validated if not item["topics"]),
        "reviewed_registry_changed": False,
        "wiki_changed": False,
        "private_local_only": True,
        "outbound_actions": "disabled",
        "paths_returned": False,
    }


def build_topic_human_review_packet(
    *, packet_path: Path, proposal_ledger: Path, output_path: Path
) -> dict[str, Any]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if packet.get("schema") != PACKET_SCHEMA:
        raise ValueError("invalid classification packet")
    item_by_id = {item["observation_id"]: item for item in packet["items"]}
    proposals: list[dict[str, Any]] = []
    with proposal_ledger.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            proposal = json.loads(line)
            if (
                proposal.get("schema") == PROPOSAL_SCHEMA
                and proposal.get("packet_id") == packet["packet_id"]
            ):
                proposals.append(proposal)
    lines = [
        "---",
        'title: "DCB 題分類 人間レビューパケット"',
        "type: dcb-topic-review-packet",
        "private_local_only: true",
        f'packet_id: "{packet["packet_id"]}"',
        "---",
        "",
        "# DCB 題分類 人間レビューパケット",
        "",
        "> Sparkの出力は候補です。採用してもreviewed registryへの反映は別工程です。",
        "",
    ]
    for proposal in proposals:
        source = item_by_id[proposal["observation_id"]]
        lines.extend(
            [
                f"## `{proposal['observation_id']}`",
                "",
                source["text"],
                "",
                f"- content_hash: `{source['content_hash']}`",
                f"- model: `{proposal['model']}`",
                "- decision: [ ] adopt / [ ] revise / [ ] reject",
                "",
                "### 候補",
                "",
            ]
        )
        if not proposal["topics"]:
            lines.append(f"- abstain: {proposal['abstain_reason']}")
        for topic in proposal["topics"]:
            label = topic["existing_topic_id"] or topic["proposed_label"]
            lines.append(
                f"- `{label}` / confidence={topic['confidence']:.2f} / {topic['reason']}"
            )
        lines.append("")
    _atomic_text(output_path, "\n".join(lines).rstrip() + "\n")
    return {
        "schema": "dcb.topic_human_review_packet.v1",
        "ok": True,
        "proposal_count": len(proposals),
        "private_local_only": True,
        "contains_raw_discord_text": True,
        "reviewed_registry_changed": False,
        "paths_returned": False,
        "outbound_actions": "disabled",
    }
