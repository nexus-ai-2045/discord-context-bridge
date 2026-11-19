"""明示相談のローカル準備。取得・LLM呼出し・送信はhostの既存経路が担当する。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .core import (
    build_bridge_intake, parse_snapshot_timestamp, redact_sensitive_storage_text,
    snapshot_freshness, utc_now, _parse_timezone_aware_timestamp,
)
from .site_adapter_runtime import build_capture
from .url_identity import parse_discord_target_url


def _read_reference(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("empty_reference")
    return text, {"path": str(path.resolve()),
                  "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def prepare_reply_consultation(
    *, url: str, capture: dict[str, Any], snapshot_store: Path,
    draft: str, goal: str, skill_file: Path, output: Path,
    lcs_root: Path | None = None,
) -> dict[str, Any]:
    """承認済みDOM取得結果から、保存receiptと同じ入力のgateを持つprivate相談を作る。

    返却値はmetadataのみ。本文はcanonical、未送信draftは相談packetにだけ保存する。
    callerは保存前に既存ingressとブラウザ承認を済ませる。ここはブラウザを操作しない。
    """
    result: dict[str, Any] = {
        "schema": "discord_reply_consultation_status.v1", "language": "ja",
        "state": "blocked", "snapshot_saved": False, "packet_written": False,
        "lcs_read": False, "skill_read": False, "ai_advice_generated": False,
        "raw_text_returned": False, "path_output": "omitted",
        "outbound_actions": "disabled", "automatic_activation": False,
    }
    if not goal.strip():
        return {**result, "reason": "consultation_goal_missing"}
    if not isinstance(capture, dict):
        return {**result, "reason": "structured_capture_required"}
    observed_url = str(capture.get("source_url") or "")
    captured = _parse_timezone_aware_timestamp(capture.get("captured_at"))
    now = parse_snapshot_timestamp(utc_now())
    if captured is None:
        return {**result, "reason": "capture_timestamp_missing_or_invalid"}
    if captured > now:
        return {**result, "reason": "capture_timestamp_in_future"}
    freshness = snapshot_freshness([capture], generated_at=utc_now(), source="structured_capture")
    if freshness["status"] != "recent":
        return {**result, "reason": "capture_refresh_required"}
    try:
        normalized = build_capture(observed_url, structured=capture,
                                   captured_at=capture["captured_at"])
    except (ValueError, TypeError):
        return {**result, "reason": "invalid_structured_capture"}
    if normalized["drift"]["detected"] or not normalized["messages"]:
        return {**result, "reason": "capture_drift_or_empty"}
    raw_messages = capture["messages"]
    identities = [row.get("id") for row in raw_messages]
    if (any(not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value) for value in identities)
            or len(set(identities)) != len(identities)):
        return {**result, "reason": "message_identity_missing_or_duplicate"}
    sequences = [row.get("sequence") for row in raw_messages]
    if (any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in sequences)
            or len(set(sequences)) != len(sequences)):
        return {**result, "reason": "message_sequence_missing_or_duplicate"}
    targets = [row for row in raw_messages if row.get("is_reply_target") is True]
    if len(targets) != 1:
        return {**result, "reason": "reply_target_not_unique"}
    identity = parse_discord_target_url(url)
    if identity is None:
        return {**result, "reason": "requested_target_invalid"}
    if len(identity) >= 3:
        kind = capture.get("target_kind")
        if kind == "message":
            if targets[0]["id"] != identity[-1]:
                return {**result, "reason": "reply_target_message_mismatch"}
        elif kind == "thread" and len(identity) == 3:
            if (capture.get("thread_id") != identity[-1]
                    or any(row.get("channel_id") != identity[-1] for row in raw_messages)):
                return {**result, "reason": "reply_target_thread_mismatch"}
        else:
            return {**result, "reason": "nested_target_kind_unresolved"}
    effective_channel = (identity[-1] if capture.get("target_kind") == "thread" and len(identity) == 3
                         else identity[-2] if len(identity) >= 3 else identity[1])
    if any(("guild_id" in row and row["guild_id"] != identity[0])
           or ("channel_id" in row and row["channel_id"] != effective_channel)
           for row in raw_messages):
        return {**result, "reason": "message_scope_mismatch"}
    messages = []
    for raw, message in zip(raw_messages, normalized["messages"]):
        if not message["body_text"].strip():
            return {**result, "reason": "message_body_missing"}
        if any(key in raw and not isinstance(raw[key], bool)
               for key in ("is_thread_root", "is_reply_target", "unresolved_reference")):
            return {**result, "reason": "invalid_relationship_metadata"}
        messages.append({
            "text": message["body_text"], "author_label": message["author_label"],
            "timestamp": message["visible_timestamp"], "sequence": raw.get("sequence"),
            "is_thread_root": raw.get("is_thread_root", False),
            "is_reply_target": raw.get("is_reply_target", False),
            # 添付の内容を必要文脈として参照している場合は、解決するまで止める。
            "unresolved_reference": (
                raw.get("unresolved_reference", False)
                or bool(raw.get("reply_to_message_id") and raw["reply_to_message_id"] not in identities)
                or bool(message["attachments"])
            ),
            "id": raw.get("id", ""), "reply_to_message_id": raw.get("reply_to_message_id", ""),
        })
    exhausted = capture.get("history_exhausted", False)
    if not isinstance(exhausted, bool):
        return {**result, "reason": "invalid_history_exhausted"}
    try:
        intake = build_bridge_intake(
            url=url, observed_url=observed_url, capture_route="in_app_browser",
            source="in_app_browser", messages=messages, history_exhausted=exhausted,
            capture_observed_at=capture["captured_at"],
            snapshot_store=snapshot_store,
        )
    except (OSError, ValueError, RuntimeError):
        return {**result, "reason": "snapshot_save_or_validation_failed"}
    result["snapshot_saved"] = intake["snapshot"]["saved"]
    result["snapshot_receipt"] = intake["snapshot"].get("snapshot_receipt", {})
    if not result["snapshot_saved"]:
        return {**result, "reason": intake.get("blocked_reason") or "snapshot_not_saved"}
    gate = intake.get("reply_context_gate", {})
    result["reply_context_gate"] = gate
    if not gate.get("reply_generation_allowed"):
        return {**result, "reason": gate.get("reason_code") or "reply_context_required"}
    configured = os.environ.get("LCS_REPO_ROOT") if lcs_root is None else str(lcs_root)
    if configured is None:
        return {**result, "reason": "lcs_root_missing"}
    if not configured.strip():
        return {**result, "reason": "lcs_root_empty"}
    root = Path(configured).expanduser()
    refs = []
    try:
        for name in ("README.md", "interpersonal.md"):
            text, ref = _read_reference(root / "docs/knowledge/communication" / name)
            ref["stable_ids"] = re.findall(r"^## (LCS-IP-\d+):", text, re.MULTILINE)
            ref["updated_at"] = next(iter(re.findall(r"^更新日: (.+)$", text, re.MULTILINE)), "")
            refs.append(ref)
    except (OSError, UnicodeError, ValueError):
        return {**result, "reason": "lcs_reference_unavailable"}
    result["lcs_read"] = True
    try:
        skill, skill_ref = _read_reference(skill_file)
    except (OSError, UnicodeError, ValueError):
        return {**result, "reason": "interpersonal_skill_unavailable"}
    result["skill_read"] = True
    if "LCS_REPO_ROOT" not in skill or "docs/knowledge/communication" not in skill:
        return {**result, "reason": "interpersonal_skill_lcs_route_missing"}
    packet = {
        "schema": "discord_reply_consultation.v1", "private_local_only": True,
        "created_at": utc_now(), "snapshot_store": str(snapshot_store.resolve()),
        "snapshot_receipt": result["snapshot_receipt"], "reply_context_gate": gate,
        "goal": redact_sensitive_storage_text(goal),
        "unsent_draft": redact_sensitive_storage_text(draft),
        "lcs_references": refs, "interpersonal_skill": skill_ref,
        "assistant_task": (
            "明示相談。参照先の現在のhashを照合してskillとLCS正本を読む。"
            "snapshot receiptのeventをcanonicalで再検証し、その保存本文だけを文脈にする。"
            "文脈gate不足・失効・改変・鮮度切れなら候補を生成せず追加文脈を求める。"
            "引用・本人説明・相手の発言・AI提案を分け、関係する安定IDだけを使う。"
            "本人へ短い判断と必要なら返信案を返す。内面・悪意・人格を断定しない。"
            "これは候補準備であり助言実施や本人採用ではない。投稿・自動送信はしない。"
        ),
        "context_source": "saved_snapshot_event", "axes_copied": False,
        "ai_advice_generated": False, "outbound_actions": "disabled",
    }
    try:
        # 既存ファイルやsymlinkを上書きしない。packetはprivate保存先に置く。
        if output.parent.resolve() != snapshot_store.parent.resolve():
            return {**result, "reason": "packet_must_use_private_snapshot_directory"}
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        return {**result, "reason": "private_packet_write_failed"}
    return {**result, "state": "ready_for_ai_consultation", "reason": "saved_context_and_references_ready",
            "packet_written": True, "next_action": "host_read_private_packet_and_propose"}


def load_reply_consultation(output: Path) -> dict[str, Any]:
    """host向けprivate consumer。現在の正本を再検証した文脈と参照を返す。stdout用ではない。"""
    from .core import (
        build_reply_context_gate_from_messages, load_content_snapshot_records,
        canonical_event_hash, stable_text_hash,
    )
    packet = json.loads(output.read_text(encoding="utf-8"))
    if packet.get("schema") != "discord_reply_consultation.v1":
        raise ValueError("consultation_packet_invalid")
    receipt = packet["snapshot_receipt"]
    store = Path(packet["snapshot_store"])
    rows = load_content_snapshot_records(store)
    matches = [row for row in rows if row.get("event_id") == receipt.get("capture_id")]
    if len(matches) != 1:
        raise ValueError("consultation_snapshot_missing_or_revoked")
    row = matches[0]
    if (any(row.get(key) != receipt.get(key) for key in ("target_key", "event_hash", "content_hash", "observed_at"))
            or canonical_event_hash(row) != receipt.get("event_hash")
            or stable_text_hash(row["text"]) != receipt.get("content_hash")):
        raise ValueError("consultation_snapshot_binding_invalid")
    if snapshot_freshness([row], generated_at=utc_now(), source="saved_snapshot")["status"] != "recent":
        raise ValueError("consultation_snapshot_refresh_required")
    evidence = row.get("reply_context_evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("consultation_snapshot_binding_invalid")
    capture_observed_at = evidence.get("capture_observed_at")
    capture_time = _parse_timezone_aware_timestamp(capture_observed_at)
    if (capture_time is None or capture_time > parse_snapshot_timestamp(utc_now())
            or snapshot_freshness([{"captured_at": capture_observed_at}],
                                  generated_at=utc_now(), source="original_capture")["status"] != "recent"):
        raise ValueError("consultation_original_capture_refresh_required")
    gate = build_reply_context_gate_from_messages(
        evidence.get("messages", []), history_exhausted=evidence.get("history_exhausted", False)
    )
    if not gate.get("reply_generation_allowed") or gate != packet.get("reply_context_gate"):
        raise ValueError("consultation_context_gate_invalid")
    for ref in [*packet["lcs_references"], packet["interpersonal_skill"]]:
        _text, current = _read_reference(Path(ref["path"]))
        if current["sha256"] != ref["sha256"]:
            raise ValueError("consultation_reference_changed")
    # 本文は既存canonicalから返すだけ。packetやLCSへ原文を複写しない。
    return {"packet": packet, "saved_context": row["text"], "reply_context_gate": gate}
