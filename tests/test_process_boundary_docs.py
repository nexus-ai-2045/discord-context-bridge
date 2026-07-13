from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def section(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index + len(start))
    return text[start_index:end_index]


def test_readme_notification_metadata_probe_contract_is_textless():
    body = (ROOT / "README.md").read_text(encoding="utf-8")
    probe = section(
        body,
        "### Discord Desktop 通知 metadata probe",
        "## MCP",
    )

    required_terms = [
        "discord_notification_delta.v1",
        "Trigger condition",
        "Fallback order",
        "Notification Center",
        "Unified Log",
        "Cache.db",
        "no_notification_observed",
        "insufficient_metadata",
        'text_output="omitted"',
        "raw_payload_read=false",
        'outbound_actions="disabled"',
    ]

    for term in required_terms:
        assert term in probe


def test_process_boundary_notification_metadata_probe_gate_is_textless():
    body = (ROOT / "PROCESS_BOUNDARY.md").read_text(encoding="utf-8")
    probe = section(
        body,
        "## Notification metadata probe gate",
        "## 保存単位",
    )

    required_terms = [
        "metadata-only probe",
        "discord_notification_delta.v1",
        "no_notification_observed",
        "insufficient_metadata",
        "eventMessage",
        "SQLite BLOB payload",
        "raw Discord text",
        "送信",
        "外部投稿",
    ]

    for term in required_terms:
        assert term in probe


def test_architecture_context_closeout_preserves_public_safe_boundaries():
    body = (ROOT / "docs/architecture-context-closeout.md").read_text(encoding="utf-8")

    required_terms = [
        "obsidian_check: checked",
        "scope_route: public-discord-context-bridge / architecture-context-closeout / no-private-data / no-discord-action",
        "source provenance",
        "content boundary",
        "human gate",
        "post-send closeout",
        "operation proof",
        "context operating mode",
        "triage",
        "catchup",
        "join-thread",
        "boundary",
        "文脈運用モード smoke",
        "send_capability=disabled",
        "metadata-only",
        "fill-only",
        "no outbound action",
        "no raw text",
        "raw Discord text",
        "snowflake",
        "local absolute path",
        "python3 scripts/ops_check.py",
        "python3 scripts/gh_guard.py --account-only",
    ]

    for term in required_terms:
        assert term in body


def test_generated_runtime_skills_include_visible_ui_automation_stopline():
    for skill in (ROOT / "dist" / "skills").glob("*/SKILL.md"):
        body = skill.read_text(encoding="utf-8")
        assert "no_unapproved_visible_ui_automation" in body
        assert "Computer Use 的な画面操作" in body
        assert "SendKeys" in body
        assert "ユーザーの明示許可なしに実行しない" in body


def test_discord_send_runbook_blocks_wrong_route_and_unverified_done_claims():
    body = (ROOT / "docs" / "discord-send-operation-runbook.md").read_text(encoding="utf-8")

    required_terms = [
        "送信経路照合",
        "webhook / bot / browser の投稿先が一致しない場合は送信しない",
        "ブラウザタイトルや画面上のサーバー名・チャンネル名を title evidence として確認する",
        "ファイル添付に失敗した場合は、送信せず blocked として止める",
        "ユーザーが停止した場合は not_sent として閉じる",
        "auto-send-preflight",
        "ready_for_auto_send_adapter",
        "idempotency key",
        "metadata-only",
        "not_sent",
        "human_sent",
    ]

    for term in required_terms:
        assert term in body
