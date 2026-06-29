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
