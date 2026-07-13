import json
from pathlib import Path

from discord_context_bridge import (
    build_bridge_intake,
    build_discord_send_staging_packet,
    build_reply_context_gate,
    build_reply_context_gate_from_messages,
    parse_visible_text,
)
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge import mcp_server


FIXTURE = Path(__file__).parent / "fixtures" / "visible_text.txt"


def test_bridge_intake_draft_fails_closed_without_reply_context(tmp_path):
    payload = build_bridge_intake(
        url="https://discord.com/channels/1/2/3",
        snapshot_store=tmp_path / "snapshots.ndjson",
        text="safe-member: visible but partial context",
        draft="前提を確認して返信します。",
        understanding_confirmed=True,
    )

    assert payload["decision"] == "reply_context_blocked"
    assert payload["guide_reply"]["quick_verdict"] == "reply-context-blocked"
    assert payload["outbound_actions"] == "disabled"


def test_mention_staging_does_not_require_reply_target():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    blocked_reply_gate = build_reply_context_gate(
        thread_root_present=False,
        reply_target_present=False,
        prior_message_count=0,
    )

    packet = build_discord_send_staging_packet(
        "確認済みの案内です。",
        events,
        mode="mention",
        target_url="https://discord.com/channels/123456789012345678/223456789012345678",
        mention_label="@safe-label",
        understanding_confirmed=True,
        reply_context_gate=blocked_reply_gate,
    )

    assert packet["staging_status"] == "ready_to_fill"
    assert "copy_block_not_ready" not in packet["blockers"]
    assert packet["outbound_actions"] == "disabled"


def test_mcp_infers_fetched_count_when_omitted(monkeypatch, tmp_path):
    class FakeFastMCP:
        def __init__(self, *_args, **_kwargs):
            self.tools = {}

        def tool(self):
            def register(func):
                self.tools[func.__name__] = func
                return func

            return register

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)
    server = mcp_server.build_server(store=tmp_path / "events.ndjson")

    payload = server.tools["plan_reply_context_before_draft"](
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=10,
        unresolved_reference_count=1,
    )

    assert payload["fetched_message_count"] == 11
    assert payload["next_fetch_limit"] == 21


def test_history_exhausted_with_unresolved_reference_blocks():
    gate = build_reply_context_gate(
        thread_root_present=True,
        reply_target_present=True,
        prior_message_count=4,
        history_exhausted=True,
        unresolved_reference_count=1,
        fetched_message_count=5,
    )

    assert gate["state"] == "blocked"
    assert gate["reason_code"] == "reply_context_unresolved_after_history_end"
    assert gate["next_fetch_limit"] == 5


def test_structured_messages_normalize_newest_first_order():
    chronological = [{"text": "root", "is_thread_root": True, "sequence": 0}]
    chronological.extend({"text": f"prior-{index}", "sequence": index + 1} for index in range(9))
    chronological.append({"text": "target", "is_reply_target": True, "sequence": 10})

    gate = build_reply_context_gate_from_messages(reversed(chronological))

    assert gate["state"] == "ready"
    assert gate["message_order_verified"] is True
    assert gate["message_order_source"] == "sequence"


def test_structured_messages_block_root_after_reply_target():
    messages = [{"text": f"newer-{index}", "sequence": index} for index in range(10)]
    messages.append({"text": "target", "is_reply_target": True, "sequence": 10})
    messages.append({"text": "invalid-root", "is_thread_root": True, "sequence": 11})

    gate = build_reply_context_gate_from_messages(messages)

    assert gate["state"] == "blocked"
    assert gate["reason_code"] == "reply_context_root_after_target"
    assert "thread_root_not_before_target" in gate["blockers"]


def test_guide_reply_json_returns_nonzero_when_copy_blocked(tmp_path, capsys):
    visible = tmp_path / "visible.txt"
    visible.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    result = cli_main(
        [
            "guide-reply",
            "--input",
            str(visible),
            "--draft",
            "前提を確認して返信します。",
            "--understanding-confirmed",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["reply_review"]["quick_verdict"] == "reply-context-blocked"
    assert payload["reply_review"]["copy_block"]["status"] == "blocked"
