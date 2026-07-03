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
        "最初の skeleton は、現在見えている Discord 本文を stdout に出すだけ",
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
