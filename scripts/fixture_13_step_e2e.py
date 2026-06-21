#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.core import (
    audit_context_store,
    build_handoff_packet,
    build_review_artifact_markdown,
    context_passport_from_text,
    import_visible_text,
    load_events,
    review_reply_intent,
    status_dashboard,
    upsert_context_document,
    upsert_review_state,
)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def read_text(path: Path | None, default: str = "") -> str:
    if path is None:
        return default
    return path.read_text(encoding="utf-8")


def step(name: str, ok: bool, **data: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, **data}


def build_fixture_e2e_payload(
    text: str,
    *,
    server_context: str,
    channel_context: str,
    thread_context: str,
    draft: str,
    thread_key: str,
    work_dir: Path,
) -> dict[str, Any]:
    event_store = work_dir / "events.ndjson"
    context_store = work_dir / "context-library.json"
    review_store = work_dir / "review-registry.json"

    imported = import_visible_text(
        text,
        path=event_store,
        guild_label="fixture-community",
        channel_label="fixture-channel",
    )
    events = load_events(event_store)

    server_saved = upsert_context_document("server", "fixture-server", server_context, path=context_store, source="fixture")
    channel_saved = upsert_context_document("channel", "fixture-channel", channel_context, path=context_store, source="fixture")
    thread_saved = upsert_context_document("thread", "fixture-thread", thread_context, path=context_store, source="fixture")
    context_audit = audit_context_store(context_store)

    passport = context_passport_from_text(
        text,
        guild_label="fixture-community",
        channel_label="fixture-channel",
        server_context=server_context,
        channel_context=channel_context,
        thread_context=thread_context,
    )
    review = review_reply_intent(draft, events)
    artifact = build_review_artifact_markdown(draft, review, title="Discord review artifact")
    review_state = upsert_review_state(
        thread_key,
        review,
        path=review_store,
        read_scope=[
            "chrome_or_fixture_observation",
            "server_context",
            "channel_context",
            "thread_context",
            "recent_visible_messages",
            "draft_review",
        ],
    )
    status = status_dashboard(event_store, github_state="local_fixture")
    handoff = build_handoff_packet(thread_key=thread_key, review_state=review_state["entry"], status=status)

    steps = [
        step(
            "01_observation_entry",
            bool(imported.get("parsed", 0) >= 1),
            parsed=int(imported.get("parsed", 0)),
            text_output="omitted",
            participant_names_output="omitted",
        ),
        step(
            "02_ssot_registry_lookup",
            bool(server_saved.get("created") and channel_saved.get("created") and thread_saved.get("created") and context_audit.get("safe_for_tunnel")),
            registry_entries=3,
            safe_for_tunnel=bool(context_audit.get("safe_for_tunnel")),
            path_output="omitted",
        ),
        step(
            "03_read_scope_decision",
            bool(passport.get("external_context_used") and passport.get("context_ready")),
            read_scope=["server_context", "channel_context", "thread_context", "recent_visible_messages"],
            raw_text_output="omitted",
        ),
        step(
            "04_understanding_summary",
            bool(passport.get("context_ready")),
            summary_points=[
                "server/channel/thread の明示文脈を参照",
                "直近可視文脈を解析",
                "発話前チェックに必要な注意点を抽出",
            ],
            point_count=3,
        ),
        step(
            "05_provisional_draft",
            bool(draft.strip()),
            draft_chars=len(draft),
            draft_output="omitted",
        ),
        step(
            "06_risk_review",
            bool(review.get("quick_verdict")),
            quick_verdict=review.get("quick_verdict"),
            missing_premise_count=len(review.get("missing_knowledge") or []),
            outbound_actions="disabled",
        ),
        step(
            "07_markdown_review_artifact",
            "## 5. human gate" in artifact and "raw_discord_text_output: omitted" in artifact,
            artifact_sections=7,
            artifact_output="omitted",
            local_path_output="omitted",
        ),
        step(
            "08_final_candidate",
            bool(review.get("final_candidate")),
            final_candidate_chars=len(str(review.get("final_candidate") or "")),
            final_candidate_output="omitted",
        ),
        step(
            "09_human_gate",
            bool((review.get("human_gate") or {}).get("human_decision_required")),
            decision=(review.get("human_gate") or {}).get("decision"),
            recommended_option=(review.get("human_gate") or {}).get("recommended_option"),
        ),
        step(
            "10_copy_block",
            bool((review.get("copy_block") or {}).get("status") in {"ready", "split", "blocked"}),
            status=(review.get("copy_block") or {}).get("status"),
            part_count=(review.get("copy_block") or {}).get("part_count"),
            text_output="omitted",
        ),
        step(
            "11_follow_up_state",
            bool(review_state["entry"].get("next_action")),
            next_action=review_state["entry"].get("next_action"),
            gate_decision=review_state["entry"].get("gate_decision"),
        ),
        step(
            "12_review_state_registry",
            bool(review_state.get("changed") and review_state["entry"].get("schema") == "discord_review_state.v1"),
            schema=review_state["entry"].get("schema"),
            path_output="omitted",
            raw_text_output="omitted",
        ),
        step(
            "13_handoff_packet",
            bool(handoff.get("schema") == "discord_handoff_packet.v1" and handoff.get("current_state", {}).get("review_state_available")),
            schema=handoff.get("schema"),
            next_action=handoff.get("next_action"),
            path_output="omitted",
        ),
    ]
    ok = all(item["ok"] for item in steps)
    return {
        "schema": "discord_13_step_fixture_e2e.v1",
        "language": "ja",
        "ok": ok,
        "stage": "done" if ok else "blocked",
        "step_count": len(steps),
        "passed": sum(1 for item in steps if item["ok"]),
        "steps": steps,
        "safety_boundary": {
            "raw_discord_text_output": "omitted",
            "participant_names_output": "omitted",
            "draft_body_output": "omitted",
            "final_candidate_body_output": "omitted",
            "local_paths_output": "omitted",
            "outbound_actions": "disabled",
        },
        "residual": [
            "実 Discord 操作は行いません。",
            "実機 capture / browser / MCP / plugin はMVP成立条件に含めません。",
            "copy block の貼り付けは人間判断後に Discord 側で行います。",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="13工程MVPをfixtureだけでE2E確認する。")
    parser.add_argument("--input", type=Path, default=ROOT / "tests/fixtures/discord_rich_copy.txt")
    parser.add_argument("--server-context", type=Path, default=ROOT / "tests/fixtures/server_context.txt")
    parser.add_argument("--channel-context", type=Path, default=ROOT / "tests/fixtures/channel_context.txt")
    parser.add_argument("--thread-context", type=Path, default=ROOT / "tests/fixtures/thread_context_rules.txt")
    parser.add_argument("--draft", default="公開時期の前提を確認してから返信します。")
    parser.add_argument("--thread-key", default="fixture-thread")
    parser.add_argument("--work-dir", type=Path, help="検証用の一時storeを置くlocal directory。出力にはpathを出しません。")
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("13工程 fixture E2E")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"stage: {payload['stage']}")
    print(f"passed: {payload['passed']} / {payload['step_count']}")
    for item in payload["steps"]:
        status = "pass" if item["ok"] else "fail"
        print(f"- {item['name']}: {status}")
    print("raw_discord_text_output: omitted")
    print("participant_names_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        payload = build_fixture_e2e_payload(
            read_text(args.input),
            server_context=read_text(args.server_context),
            channel_context=read_text(args.channel_context),
            thread_context=read_text(args.thread_context),
            draft=args.draft,
            thread_key=args.thread_key,
            work_dir=args.work_dir,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="dcb-13-step-") as tmp:
            payload = build_fixture_e2e_payload(
                read_text(args.input),
                server_context=read_text(args.server_context),
                channel_context=read_text(args.channel_context),
                thread_context=read_text(args.thread_context),
                draft=args.draft,
                thread_key=args.thread_key,
                work_dir=Path(tmp),
            )
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
