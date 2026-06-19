import importlib.util
import os
from pathlib import Path
import tomllib

import pytest

from discord_context_bridge import (
    DisabledCapability,
    audit_context_store,
    audit_event_store,
    context_passport_from_text,
    fast_briefing,
    get_context_document,
    guide_reply_from_text,
    import_visible_text,
    list_context_documents,
    load_events,
    ops_view_summary,
    parse_visible_text,
    review_reply_intent,
    send_message,
    status_dashboard,
    upsert_context_document,
)
from discord_context_bridge import cli as cli_module
from discord_context_bridge.cli import build_parser
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.core import resolve_context_bindings
from discord_context_bridge import mcp_server

FIXTURE = Path(__file__).parent / "fixtures" / "visible_text.txt"
RICH_COPY_FIXTURE = Path(__file__).parent / "fixtures" / "discord_rich_copy.txt"
PASSPORT_FIXTURE = Path(__file__).parent / "fixtures" / "thread_context_passport.txt"
SERVER_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "server_context.txt"
CHANNEL_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "channel_context.txt"
THREAD_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "thread_context_rules.txt"
ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_visible_text_supports_copied_blocks():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "Can you clarify the premise before we reply?"
    assert events[2].text_snippet == "I think the reply should mention the audience."
    assert all(event.actions_allowed == ["read"] for event in events)


def test_parse_visible_text_supports_discord_rich_copy_metadata():
    events = parse_visible_text(RICH_COPY_FIXTURE.read_text(encoding="utf-8"))

    assert [event.author_label for event in events] == ["member-a", "member-b", "member-c"]
    assert events[0].text_snippet == "前提を確認してから返事したいです。 公開時期の話ですよね。"
    assert events[1].text_snippet == "そうです。相手に伝わる言い方にしたいです。"
    assert events[2].text_snippet == "返信前に文脈を揃えましょう。"


def test_import_visible_text_appends_and_deduplicates(tmp_path):
    store = tmp_path / "events.ndjson"

    first = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)
    second = import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    assert first["parsed"] == 3
    assert first["appended"] == 3
    assert first["language"] == "ja"
    assert first["message"] == "Discord の可視テキストを取り込みました。"
    assert first["preview"][0]["source_label"] == "Discord の可視テキスト"
    assert first["preview"][0]["actions_allowed_label"] == "読み取りのみ"
    assert second["duplicate"] == 3
    assert len(load_events(store)) == 3


def test_import_visible_text_dry_run_previews_without_writing(tmp_path):
    store = tmp_path / "events.ndjson"

    result = import_visible_text(RICH_COPY_FIXTURE.read_text(encoding="utf-8"), path=store, dry_run=True)

    assert result["dry_run"] is True
    assert result["parsed"] == 3
    assert result["appended"] == 0
    assert result["message"] == "保存せずに取り込み結果を確認しました。"
    assert result["preview"][0]["author_label"] == "member-a"
    assert result["briefing"]["event_count"] == 3
    assert not store.exists()


def test_audit_event_store_reports_safe_empty_store(tmp_path):
    report = audit_event_store(tmp_path / "events.ndjson")

    assert report["safe_for_tunnel"] is True
    assert report["safe_for_tunnel_label"] == "外部公開前の監査を通過しました。"
    assert report["event_count"] == 0
    assert report["issues"] == []


def test_audit_event_store_flags_private_identifiers(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(
        "\n".join(
            [
                "member-a: webhook is https://discord.com/api/webhooks/123456789012345678/token",
                "member-b: user id 987654321098765432 should not be stored",
                "member-c: local path /Users/example/Library/Application Support/Discord",
            ]
        ),
        path=store,
    )

    report = audit_event_store(store)

    assert report["safe_for_tunnel"] is False
    assert report["event_count"] == 3
    assert [issue["kind"] for issue in report["issues"]] == [
        "discord_webhook_url",
        "discord_snowflake_id",
        "local_absolute_path",
    ]


def test_ops_view_summary_omits_message_text_and_reports_boundary(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    summary = ops_view_summary(store)

    assert summary["message"] == "運用ログ表示を作成しました。"
    assert summary["safe_labels"] == ["safe-general"]
    assert summary["event_count"] == 3
    assert summary["delta_count"] == 3
    assert summary["last_seen"]
    assert summary["gate_verdict"] == "pass"
    assert summary["outbound"] == "disabled"
    assert "このツールから Discord へ送信しません。" == summary["outbound_label"]
    assert "text_snippet" not in summary
    assert "authors" not in summary


def test_status_dashboard_splits_operational_state_without_private_text(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    dashboard = status_dashboard(store, github_state="merged")

    assert dashboard["schema"] == "discord_bridge_status_dashboard.v1"
    assert dashboard["now"]["context_available"] is True
    assert dashboard["now"]["event_count"] == 3
    assert dashboard["broken"] == []
    assert dashboard["blocked"] == []
    assert dashboard["github"]["state"] == "merged"
    assert dashboard["safety_boundary"]["outbound_actions"] == "disabled"
    assert dashboard["safety_boundary"]["raw_text_included"] is False
    assert "Can you clarify" not in str(dashboard)
    assert "member-a" not in str(dashboard)
    assert str(tmp_path) not in str(dashboard)


def test_status_dashboard_reports_missing_context_without_local_path(tmp_path):
    dashboard = status_dashboard(tmp_path / "missing.ndjson")

    assert dashboard["now"]["context_available"] is False
    assert dashboard["blocked"] == ["Discord 可視本文はまだ取り込まれていません。"]
    assert dashboard["next"] == ["private adapter probe または import-visible-text を実行します。"]
    assert str(tmp_path) not in str(dashboard)


def test_fast_briefing_and_reply_review(tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    briefing = fast_briefing(load_events(store))
    review = review_reply_intent(
        "I understand this is about launch timing and my reply will ask for the premise.",
        load_events(store),
    )

    assert briefing["event_count"] == 3
    assert briefing["language"] == "ja"
    assert briefing["message"] == "直近文脈の短い要約を作成しました。"
    assert briefing["partial_label"] == "直近の一部だけを使った要約です。"
    assert review["language"] == "ja"
    assert review["message"] == "返信前レビューが完了しました。"
    assert review["ok_to_reply"] == "likely_ok"
    assert review["ok_to_reply_label"] == "返信してよさそうです。"
    assert review["send_capability"] == "disabled"
    assert review["alignment_label"] == "文脈に合っています。"
    assert review["topic_warning_label"] == "話題の大きなズレは見つかりません。"


def test_guide_reply_from_text_returns_conversation_guide():
    guide = guide_reply_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "公開時期の前提を確認してから返信します。",
    )

    assert guide["language"] == "ja"
    assert guide["message"] == "Discord 返信ガイドを作成しました。"
    assert guide["parsed"] == 3
    assert "公開時期の話ですよね" in guide["counterparty_context"]
    assert guide["reply_review"]["ok_to_reply_label"] == "返信してよさそうです。"
    assert guide["reply_review"]["quick_verdict"] == "go"
    assert guide["reply_review"]["quick_verdict_label"].startswith("go:")
    assert guide["send_capability_label"] == "このツールから Discord へ送信しません。"
    assert "文脈と返信意図は大きくずれていません。" in guide["next_actions"]


def test_guide_reply_warns_about_topic_mismatch():
    guide = guide_reply_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "価格について返信します。",
    )

    assert guide["reply_review"]["ok_to_reply"] == "ask_first"
    assert guide["reply_review"]["quick_verdict"] == "ask-context"
    assert "話題がずれている可能性があります" in guide["reply_review"]["topic_warning_label"]
    assert "文脈は公開時期" in guide["reply_review"]["topic_warning_label"]
    assert "返信案は価格" in guide["reply_review"]["topic_warning_label"]
    assert guide["next_actions"][0] == guide["reply_review"]["topic_warning_label"]


def test_review_reply_intent_quick_verdict_waits_without_context():
    review = review_reply_intent("前提を確認してから返します。", [])

    assert review["quick_verdict"] == "wait"
    assert review["quick_verdict_label"].startswith("wait:")
    assert review["send_capability"] == "disabled"


def test_review_reply_intent_quick_verdict_flags_risky_tone():
    events = parse_visible_text(PASSPORT_FIXTURE.read_text(encoding="utf-8"))
    review = review_reply_intent("それは最悪です。ふざけないでください。", events)

    assert review["quick_verdict"] == "risky"
    assert review["quick_verdict_label"].startswith("risky:")
    assert review["send_capability"] == "disabled"


def test_context_passport_from_text_summarizes_thread_context():
    passport = context_passport_from_text(PASSPORT_FIXTURE.read_text(encoding="utf-8"))

    assert passport["language"] == "ja"
    assert passport["message"] == "スレッド文脈カードを作成しました。"
    assert passport["parsed"] == 4
    assert "相談" in passport["thread_purpose"]
    assert "公開時期" in passport["thread_purpose"]
    assert "このチャンネルは公開前の企画相談用です" in passport["conversation_flow"]
    assert passport["rule_notes"]
    assert "未確認の断定は禁止" in passport["rule_notes"][0]
    assert passport["people_temperature"] == "serious"
    assert passport["context_ready"] is True
    assert passport["send_capability"] == "disabled"
    assert "断定せず、前提確認から入る。" in passport["natural_entry_angles"]


def test_context_passport_uses_explicit_server_channel_thread_context():
    passport = context_passport_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        server_context=SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        channel_context=CHANNEL_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        thread_context=THREAD_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
    )

    assert passport["external_context_used"] is True
    assert passport["context_sources"] == ["server_context", "channel_context", "thread_context"]
    assert "明示文脈を併用しています。" == passport["external_context_used_label"]
    assert "サーバー文脈" in passport["context_sources_label"]
    assert "チャンネル文脈" in passport["context_sources_label"]
    assert "スレッド文脈" in passport["context_sources_label"]
    assert "相談" in passport["thread_purpose"]
    assert "対象読者" in passport["thread_purpose"]
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])
    assert any("サーバー・チャンネル・スレッドの明示文脈" in action for action in passport["natural_entry_angles"])


def test_context_library_saves_audits_and_reuses_context(tmp_path):
    context_store = tmp_path / "context-library.json"

    saved = upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
    )
    listed = list_context_documents(context_store)
    audit = audit_context_store(context_store)
    loaded = get_context_document("server", "nexus", path=context_store)

    assert saved["message"] == "文脈庫を更新しました。"
    assert saved["created"] is True
    assert saved["entry"]["key"] == "nexus"
    assert "text" not in saved["entry"]
    assert listed["count"] == 1
    assert listed["entries"][0]["summary"].startswith("サーバールール:")
    assert "text" not in listed["entries"][0]
    assert audit["safe_for_tunnel"] is True
    assert "個人情報の共有は禁止" in loaded

    passport = context_passport_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        server_context=loaded,
    )
    assert passport["external_context_used"] is True
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])


def test_context_library_resolves_public_safe_labels(tmp_path):
    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community"],
    )
    upsert_context_document(
        "channel",
        "planning",
        CHANNEL_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community/safe-planning"],
    )
    upsert_context_document(
        "thread",
        "launch-thread",
        THREAD_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["safe-planning:thread"],
    )

    bindings = resolve_context_bindings(
        guild_label="example-community",
        channel_label="#safe-planning",
        path=context_store,
    )

    assert bindings["server"]["key"] == "nexus"
    assert bindings["channel"]["key"] == "planning"
    assert bindings["thread"]["key"] == "launch-thread"


def test_context_library_audit_flags_private_identifiers(tmp_path):
    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "unsafe",
        "Webhook: https://discord.com/api/webhooks/123456789012345678/token",
        path=context_store,
    )

    audit = audit_context_store(context_store)

    assert audit["safe_for_tunnel"] is False
    assert audit["issue_count"] >= 1
    assert "discord_webhook_url" in {issue["kind"] for issue in audit["issues"]}


def test_send_message_is_disabled():
    with pytest.raises(DisabledCapability):
        send_message("hello")


def test_user_facing_docs_declare_japanese_default():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    checklist = (ROOT / "PUBLIC_RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    assert "基本言語は日本語" in readme
    assert "Language Boundary" in checklist
    assert "CLI / API のユーザー向けメッセージが日本語" in checklist


def test_package_version_tracks_current_release():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert metadata["project"]["version"] == "0.10.0"
    assert "## 0.10.0 - 2026-06-19" in changelog
    assert "read_visible_discord_text.py" in changelog


def test_user_facing_runtime_messages_are_japanese():
    gap = review_reply_intent("", [])

    assert gap["language"] == "ja"
    assert gap["missing_knowledge"] == ["共有前提", "返信対象"]
    assert gap["ok_to_reply_label"] == "先に確認した方がよさそうです。"
    assert gap["alignment_label"] == "文脈に不足があります。"
    assert gap["missing_knowledge_label"] == "不足している前提: 共有前提、返信対象"
    assert gap["suggested_correction"] == "直近の文脈はまだ取り込まれていません。"

    with pytest.raises(DisabledCapability, match="Discord への送信機能"):
        send_message("hello")


def test_cli_help_uses_japanese_user_facing_text():
    help_text = build_parser().format_help()

    assert "可視会話テキスト" in help_text
    assert "直近文脈" in help_text
    assert "audit-store" in help_text
    assert "ops-view" in help_text
    assert "status-dashboard" in help_text
    assert "import-clipboard" in help_text
    assert "guide-reply" in help_text
    assert "context-passport" in help_text
    assert "watch-passport" in help_text
    assert "使い方:" in help_text
    assert "オプション:" in help_text
    assert "show this help message and exit" not in help_text
    assert "positional arguments:" not in help_text


def test_cli_status_dashboard_outputs_safe_json(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(
        [
            "--store",
            str(store),
            "status-dashboard",
            "--json",
            "--github-state",
            "merged",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"schema": "discord_bridge_status_dashboard.v1"' in output
    assert '"state": "merged"' in output
    assert '"outbound_actions": "disabled"' in output
    assert "Can you clarify" not in output
    assert "member-a" not in output
    assert str(tmp_path) not in output


def test_cli_guide_reply_outputs_human_readable_guide(capsys):
    result = cli_main(
        [
            "guide-reply",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--draft",
            "公開時期の前提を確認してから返信します。",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "Discord 返信ガイドを作成しました。" in output
    assert "解析件数: 3" in output
    assert "返信してよさそうです。" in output
    assert "このツールから Discord へ送信しません。" in output


def test_cli_guide_reply_reads_clipboard_command(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    result = cli_main(
        [
            "guide-reply",
            "--from-clipboard",
            "--clipboard-command",
            "pbpaste",
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"message": "Discord 返信ガイドを作成しました。"' in output
    assert '"send_capability": "disabled"' in output


def test_cli_guide_reply_reads_source_command(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_main(
        [
            "guide-reply",
            "--source-command",
            "discord-visible-text",
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["discord-visible-text"]
    assert calls[0][1]["capture_output"] is True
    assert '"message": "Discord 返信ガイドを作成しました。"' in output
    assert '"send_capability": "disabled"' in output


def test_cli_source_command_error_redacts_sensitive_stderr(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failed with https://discord.com/api/webhooks/123456789012345678/token"

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    with pytest.raises(SystemExit) as exc:
        cli_module.read_command_text("discord-visible-text")

    message = str(exc.value)
    assert "安全監査により詳細を省略しました。" in message
    assert "webhooks/123456789012345678" not in message
    assert "discord-visible-text" not in message


def test_cli_source_command_error_reports_safe_reason(monkeypatch):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "permission denied"

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    with pytest.raises(SystemExit) as exc:
        cli_module.read_command_text("discord-visible-text")

    assert "exit_code=1 reason=permission" in str(exc.value)


def test_cli_context_passport_outputs_human_readable_context(capsys):
    result = cli_main(
        [
            "context-passport",
            "--input",
            str(PASSPORT_FIXTURE),
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "スレッド文脈カードを作成しました。" in output
    assert "解析件数: 4" in output
    assert "ルール注意:" in output
    assert "相談・注意・困りごと寄りです。" in output
    assert "このツールから Discord へ送信しません。" in output


def test_cli_context_passport_accepts_explicit_context_files(capsys):
    result = cli_main(
        [
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--server-context",
            str(SERVER_CONTEXT_FIXTURE),
            "--channel-context",
            str(CHANNEL_CONTEXT_FIXTURE),
            "--thread-context",
            str(THREAD_CONTEXT_FIXTURE),
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "明示文脈を併用しています。" in output
    assert "文脈ソース: サーバー文脈、チャンネル文脈、スレッド文脈" in output
    assert "個人情報の共有は禁止" in output


def test_cli_context_library_persists_and_passport_uses_keys(tmp_path, capsys):
    context_store = tmp_path / "context-library.json"

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-upsert",
            "--kind",
            "server",
            "--key",
            "nexus",
            "--input",
            str(SERVER_CONTEXT_FIXTURE),
            "--source",
            "fixture",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert '"message": "文脈庫を更新しました。"' in output

    result = cli_main(["--context-store", str(context_store), "audit-context-store"])
    output = capsys.readouterr().out
    assert result == 0
    assert '"safe_for_tunnel": true' in output

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--server-context-key",
            "nexus",
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "明示文脈を併用しています。" in output
    assert "個人情報の共有は禁止" in output


def test_cli_context_passport_auto_binds_safe_labels(tmp_path, capsys):
    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community"],
    )
    upsert_context_document(
        "channel",
        "planning",
        CHANNEL_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community/safe-planning"],
    )

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--guild",
            "example-community",
            "--channel",
            "safe-planning",
            "--auto-context-bindings",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "safe label から文脈庫を補助参照しました。" in output
    assert "サーバー文脈" in output
    assert "チャンネル文脈" in output
    assert "個人情報の共有は禁止" in output


def test_cli_context_passport_explicit_key_wins_over_auto_binding(tmp_path, capsys):
    context_store = tmp_path / "context-library.json"
    safe_auto_text = "自動側のサーバールール: 価格の相談用です。"
    explicit_text = "明示側のサーバールール: ルールとして未確認の断定は禁止です。"
    upsert_context_document(
        "server",
        "auto",
        safe_auto_text,
        path=context_store,
        labels=["example-community"],
    )
    upsert_context_document(
        "server",
        "explicit",
        explicit_text,
        path=context_store,
    )

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "context-passport",
            "--input",
            str(RICH_COPY_FIXTURE),
            "--guild",
            "example-community",
            "--channel",
            "safe-planning",
            "--auto-context-bindings",
            "--server-context-key",
            "explicit",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "明示側のサーバールール" in output
    assert "自動側のサーバールール" not in output


def test_cli_ops_view_outputs_safe_operations_summary(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store, channel_label="safe-general")

    result = cli_main(["--store", str(store), "ops-view"])
    output = capsys.readouterr().out

    assert result == 0
    assert "運用ログ表示を作成しました。" in output
    assert "safe label: safe-general" in output
    assert "件数: 3" in output
    assert "gate verdict: pass" in output
    assert "このツールから Discord へ送信しません。" in output
    assert "Can you clarify" not in output


def test_cli_context_passport_reads_source_command(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = PASSPORT_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_main(
        [
            "context-passport",
            "--source-command",
            "discord-visible-text",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["discord-visible-text"]
    assert '"message": "スレッド文脈カードを作成しました。"' in output
    assert '"people_temperature": "serious"' in output
    assert '"send_capability": "disabled"' in output


def test_cli_watch_guide_polls_source_command_and_outputs_updates(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    fixture = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
    outputs = [fixture, fixture, "member-d: 価格ではなく公開時期の前提です"]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(outputs.pop(0))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.time, "sleep", lambda interval: None)

    result = cli_main(
        [
            "watch-guide",
            "--source-command",
            "discord-visible-text",
            "--draft",
            "公開時期の前提を確認してから返信します。",
            "--max-polls",
            "3",
            "--interval",
            "0",
            "--source-timeout",
            "4",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert len(calls) == 3
    assert calls[0][1]["timeout"] == 4
    assert '"event": "guide_update"' in output
    assert '"message": "Discord 返信ガイドを更新しました。"' in output
    assert '"polls": 3' in output
    assert '"changed": 2' in output
    assert '"send_capability": "disabled"' in output
    assert "返信ガイド監視を終了しました。" in output


def test_cli_watch_passport_polls_source_command_and_outputs_updates(capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    fixture = PASSPORT_FIXTURE.read_text(encoding="utf-8")
    outputs = [fixture, fixture, "member-d: 公開時期の前提を確認してから相談しましょう"]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(outputs.pop(0))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.time, "sleep", lambda interval: None)

    result = cli_main(
        [
            "watch-passport",
            "--source-command",
            "discord-visible-text",
            "--max-polls",
            "3",
            "--interval",
            "0",
            "--source-timeout",
            "4",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert len(calls) == 3
    assert calls[0][0] == ["discord-visible-text"]
    assert calls[0][1]["timeout"] == 4
    assert '"event": "passport_update"' in output
    assert '"message": "スレッド文脈カードを更新しました。"' in output
    assert '"polls": 3' in output
    assert '"changed": 2' in output
    assert '"send_capability": "disabled"' in output
    assert "文脈カード監視を終了しました。" in output


def test_cli_watch_passport_allows_env_prefixed_source_command(capsys, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "outer")

    class Completed:
        returncode = 0
        stderr = ""
        stdout = PASSPORT_FIXTURE.read_text(encoding="utf-8")

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    result = cli_main(
        [
            "watch-passport",
            "--source-command",
            "PYTHONPATH=src discord-visible-text",
            "--max-polls",
            "1",
            "--interval",
            "0",
            "--json",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["discord-visible-text"]
    assert calls[0][1]["env"]["PYTHONPATH"] == "src"
    assert os.environ["PYTHONPATH"] == "outer"
    assert '"event": "passport_update"' in output


def test_cli_watch_passport_uses_context_library_keys(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")

    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
    )

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "watch-passport",
            "--source-command",
            "discord-visible-text",
            "--server-context-key",
            "nexus",
            "--max-polls",
            "1",
            "--interval",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "文脈カード更新 #1" in output
    assert "個人情報の共有は禁止" in output
    assert "このツールから Discord へ送信しません。" in output


def test_cli_watch_passport_auto_binds_safe_labels(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")

    context_store = tmp_path / "context-library.json"
    upsert_context_document(
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        path=context_store,
        source="fixture",
        labels=["example-community"],
    )

    monkeypatch.setattr(cli_module.subprocess, "run", lambda command, **kwargs: Completed())

    result = cli_main(
        [
            "--context-store",
            str(context_store),
            "watch-passport",
            "--source-command",
            "discord-visible-text",
            "--guild",
            "example-community",
            "--channel",
            "safe-planning",
            "--auto-context-bindings",
            "--max-polls",
            "1",
            "--interval",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "文脈カード更新 #1" in output
    assert "safe label から文脈庫を補助参照しました。" in output
    assert "個人情報の共有は禁止" in output


def test_cli_review_draft_alias_uses_stored_context(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text(FIXTURE.read_text(encoding="utf-8"), path=store)

    result = cli_main(
        [
            "--store",
            str(store),
            "review-draft",
            "--draft",
            "Before I reply, I will ask for the premise.",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert '"message": "返信前レビューが完了しました。"' in output
    assert '"send_capability": "disabled"' in output


def test_visible_text_adapter_reads_fixture_and_normalizes_output(capsys):
    adapter = load_script_module("visible_text_adapter_for_test", ROOT / "scripts" / "read_visible_discord_text.py")

    result = adapter.main(["--input", str(RICH_COPY_FIXTURE)])
    output = capsys.readouterr().out

    assert result == 0
    assert "member-a" in output
    assert "前提を確認してから返事したいです。" in output
    assert output.endswith("\n")


def test_visible_text_adapter_blocks_sensitive_output(tmp_path, capsys):
    adapter = load_script_module("visible_text_adapter_sensitive_for_test", ROOT / "scripts" / "read_visible_discord_text.py")
    unsafe = tmp_path / "unsafe.txt"
    unsafe.write_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", encoding="utf-8")

    result = adapter.main(["--input", str(unsafe)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "安全監査に失敗" in captured.err
    assert "discord_webhook_url" in captured.err


def test_visible_text_adapter_allow_unsafe_requires_env(tmp_path, capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_allow_unsafe_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    monkeypatch.delenv("DCB_ALLOW_UNSAFE_OUTPUT", raising=False)
    unsafe = tmp_path / "unsafe.txt"
    unsafe.write_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", encoding="utf-8")

    result = adapter.main(["--input", str(unsafe), "--allow-unsafe"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "discord_webhook_url" in captured.err
    assert "webhooks/123456789012345678" not in captured.err


def test_visible_text_adapter_blocks_expanded_sensitive_output(tmp_path, capsys):
    adapter = load_script_module(
        "visible_text_adapter_expanded_sensitive_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    unsafe_lines = [
        ("discord_snowflake_id", "member-a: synthetic id 123456789012345678"),
        ("local_absolute_path", "member-b: synthetic path /private/tmp/example-profile"),
        ("authorization_header", "member-c: Authorization: synthetic-secret-value"),
        ("bearer_token_like", "member-d: Bearer syntheticBearerValue123"),
    ]
    unsafe = tmp_path / "expanded-unsafe.txt"
    unsafe.write_text("\n".join(line for _, line in unsafe_lines), encoding="utf-8")

    result = adapter.main(["--input", str(unsafe)])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "安全監査に失敗" in captured.err
    for issue, line in unsafe_lines:
        assert issue in captured.err
        assert line not in captured.err


def test_visible_text_adapter_can_read_source_command(capsys, monkeypatch):
    adapter = load_script_module("visible_text_adapter_command_for_test", ROOT / "scripts" / "read_visible_discord_text.py")

    class Completed:
        returncode = 0
        stdout = "member-a: 画面に見えている本文です\n"
        stderr = ""

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--source-command", "discord-visible-text"])
    output = capsys.readouterr().out

    assert result == 0
    assert output == "member-a: 画面に見えている本文です\n"


def test_screenshot_ocr_runner_reads_existing_image_with_private_ocr_command(tmp_path, capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")
    calls = []

    class Completed:
        returncode = 0
        stdout = "member-a: OCRで読めた本文です\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool {image}", "--timeout", "4"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["ocr-tool", str(image)]
    assert calls[0][1]["timeout"] == 4
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_runs_capture_then_ocr(capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_capture_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ocr-tool":
            return Completed("member-a: OCRで読めた本文です\n")
        return Completed("")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(
        [
            "--screenshot-command",
            "capture-tool {image}",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == "capture-tool"
    assert calls[1][0] == "ocr-tool"
    assert calls[0][1].endswith("capture.png")
    assert calls[1][1].endswith("capture.png")
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_builds_macos_screencapture_region(capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_region_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ocr-tool":
            return Completed("member-a: OCRで読めた本文です\n")
        return Completed("")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(
        [
            "--screencapture-region",
            "10,20,300,400",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][:3] == ["screencapture", "-x", "-R10,20,300,400"]
    assert calls[1][0] == "ocr-tool"
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_builds_named_capture_profile(capsys, monkeypatch):
    runner = load_script_module(
        "screenshot_ocr_runner_named_profile_for_test",
        ROOT / "scripts" / "read_screenshot_ocr_text.py",
    )
    calls = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "ocr-tool":
            return Completed("member-a: OCRで読めた本文です\n")
        return Completed("")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    result = runner.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,300,400",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][:3] == ["screencapture", "-x", "-R10,20,300,400"]
    assert calls[1][0] == "ocr-tool"
    assert output == "member-a: OCRで読めた本文です\n"


def test_screenshot_ocr_runner_rejects_invalid_capture_region(capsys):
    runner = load_script_module("screenshot_ocr_runner_bad_region_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")

    result = runner.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,0,400",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "invalid_capture_region" in captured.err


def test_screenshot_ocr_runner_blocks_sensitive_ocr_output(tmp_path, capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_sensitive_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")

    class Completed:
        returncode = 0
        stdout = "member-a: https://discord.com/api/webhooks/123456789012345678/token\n"
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: Completed())

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool {image}"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "discord_webhook_url" in captured.err
    assert "webhooks/123456789012345678" not in captured.err


def test_screenshot_ocr_runner_allow_unsafe_requires_env(tmp_path, capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_allow_unsafe_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    monkeypatch.delenv("DCB_ALLOW_UNSAFE_OUTPUT", raising=False)
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")

    class Completed:
        returncode = 0
        stdout = "member-a: https://discord.com/api/webhooks/123456789012345678/token\n"
        stderr = ""

    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: Completed())

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool {image}", "--allow-unsafe"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "discord_webhook_url" in captured.err
    assert "webhooks/123456789012345678" not in captured.err


def test_screenshot_ocr_runner_redacts_sensitive_command_error(capsys, monkeypatch):
    runner = load_script_module("screenshot_ocr_runner_error_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failed near /private/tmp/discord-profile"

    monkeypatch.setattr(runner.subprocess, "run", lambda command, **kwargs: Completed())

    result = runner.main(["--screenshot-command", "capture-tool {image}", "--ocr-command", "ocr-tool {image}"])
    captured = capsys.readouterr()

    assert result == 1
    assert "安全監査により詳細を省略しました。" in captured.err
    assert "/private/tmp/discord-profile" not in captured.err


def test_screenshot_ocr_runner_requires_image_placeholder(tmp_path, capsys):
    runner = load_script_module("screenshot_ocr_runner_placeholder_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")
    image = tmp_path / "capture.png"
    image.write_bytes(b"fake-png")

    result = runner.main(["--image", str(image), "--ocr-command", "ocr-tool"])
    captured = capsys.readouterr()

    assert result == 1
    assert "placeholder_missing" in captured.err


def test_screenshot_ocr_runner_blocks_full_screen_screencapture(capsys):
    runner = load_script_module("screenshot_ocr_runner_fullscreen_for_test", ROOT / "scripts" / "read_screenshot_ocr_text.py")

    result = runner.main(
        [
            "--screenshot-command",
            "screencapture -x {image}",
            "--ocr-command",
            "ocr-tool {image}",
        ]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert "full_screen_capture_blocked" in captured.err


def test_live_ops_smoke_omits_message_text(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"

    monkeypatch.setattr(
        live_smoke,
        "read_command_text",
        lambda command, empty_message, timeout=None: FIXTURE.read_text(encoding="utf-8"),
    )

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--store",
            str(store),
            "--channel",
            "safe-general",
            "--reset",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "live ops smoke が完了しました。" in output
    assert "safe label: safe-general" in output
    assert "parsed: 3" in output
    assert "appended: 3" in output
    assert "message text: omitted" in output
    assert "Can you clarify" not in output
    assert "text_snippet" not in output


def test_probe_visible_source_reports_without_message_text(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_for_test", ROOT / "scripts" / "probe_visible_source.py")
    calls = []

    class Completed:
        returncode = 0
        stdout = "member-a: 画面に見えている本文です\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(
        [
            "--ocr-command",
            "cat {image}",
            "--source-command",
            "python3 scripts/read_screenshot_ocr_text.py --image tests/fixtures/discord_rich_copy.txt --ocr-command 'cat {image}'",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "visible source probe pack が完了しました。" in output
    assert "- ocr_fixture: pass" in output
    assert "- live_ops_smoke: pass" in output
    assert "message text: omitted" in output
    assert "画面に見えている本文" not in output
    assert any("scripts/read_screenshot_ocr_text.py" in command for command in calls)


def test_probe_visible_source_can_build_ocr_capture_region_source(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_region_for_test", ROOT / "scripts" / "probe_visible_source.py")
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,300,400",
            "--ocr-language",
            "jpn+eng",
            "--json",
        ]
    )
    payload = capsys.readouterr().out

    assert result == 0
    live_smoke_call = next(command for command in calls if "scripts/live_ops_smoke.py" in command)
    source_index = live_smoke_call.index("--source-command") + 1
    source_command = live_smoke_call[source_index]
    assert "--capture-profile macos-screencapture-region --capture-region 10,20,300,400" in source_command
    assert "tesseract {image} stdout -l jpn+eng" in source_command
    assert '"text_output": "omitted"' in payload
    assert '"capture_profile"' in payload
    assert '"image_output": "temporary"' in payload


def test_probe_visible_source_reports_live_smoke_metrics(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_metrics_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        if "scripts/live_ops_smoke.py" in command:
            return Completed(
                '{"parsed": 7, "source_ready": true, "gate_verdict": "pass", '
                '"event_count": 7, "text_output": "omitted", "outbound": "disabled"}'
            )
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--source-command", "visible-source", "--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"parsed": 7' in payload
    assert '"source_ready": true' in payload
    assert '"gate_verdict": "pass"' in payload
    assert '"text_output": "omitted"' in payload


def test_probe_visible_source_rejects_invalid_ocr_language():
    probe = load_script_module("probe_visible_source_bad_language_for_test", ROOT / "scripts" / "probe_visible_source.py")

    with pytest.raises(ValueError, match="ocr language"):
        probe.build_ocr_capture_source_command(
            region="10,20,300,400",
            language="eng --psm 6",
            timeout=20,
        )


def test_probe_visible_source_requires_capture_profile_for_capture_region(capsys):
    probe = load_script_module("probe_visible_source_region_requires_profile_for_test", ROOT / "scripts" / "probe_visible_source.py")

    result = probe.main(["--capture-region", "10,20,300,400", "--json"])
    payload = capsys.readouterr().out

    assert result == 2
    assert '"reason": "capture_profile_required"' in payload


def test_probe_visible_source_reports_invalid_ocr_language_without_traceback(capsys):
    probe = load_script_module("probe_visible_source_bad_language_cli_for_test", ROOT / "scripts" / "probe_visible_source.py")

    result = probe.main(
        [
            "--capture-profile",
            "macos-screencapture-region",
            "--capture-region",
            "10,20,300,400",
            "--ocr-language",
            "eng --psm 6",
            "--json",
        ]
    )
    payload = capsys.readouterr().out

    assert result == 2
    assert '"reason": "invalid_ocr_language"' in payload


def test_probe_visible_source_json_marks_dependency_warning(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_json_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        stdout = ""
        stderr = ""

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        if command[:2] == ["tesseract", "--version"]:
            return Completed(127)
        return Completed(0)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"overall": "warn"' in payload
    assert '"name": "tesseract"' in payload
    assert '"status": "warn"' in payload
    assert '"text_output": "omitted"' in payload


def test_probe_visible_source_missing_optional_dependency_is_warning(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_missing_dependency_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        if command[:2] == ["tesseract", "--version"]:
            raise FileNotFoundError("missing")
        return Completed()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"overall": "warn"' in payload
    assert '"reason": "not_found"' in payload
    assert '"name": "tesseract"' in payload


def test_probe_visible_source_marks_accessibility_probe_failure_as_warning(capsys, monkeypatch):
    probe = load_script_module("probe_visible_source_ax_warn_for_test", ROOT / "scripts" / "probe_visible_source.py")

    class Completed:
        stdout = ""
        stderr = "Application is not running"

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        if any("read_visible_discord_text.py" in part for part in command):
            return Completed(1)
        return Completed(0)

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    result = probe.main(["--ax-probe", "--json"])
    payload = capsys.readouterr().out

    assert result == 0
    assert '"overall": "warn"' in payload
    assert '"name": "macos_accessibility_probe"' in payload
    assert '"status": "warn"' in payload


def test_live_ops_smoke_passes_source_timeout(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_timeout_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"
    calls = []

    def fake_read_command_text(command, *, empty_message, timeout=None):
        calls.append({"command": command, "empty_message": empty_message, "timeout": timeout})
        return FIXTURE.read_text(encoding="utf-8")

    monkeypatch.setattr(live_smoke, "read_command_text", fake_read_command_text)

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--source-timeout",
            "3.5",
            "--store",
            str(store),
            "--reset",
        ]
    )

    assert result == 0
    assert calls[0]["timeout"] == 3.5
    assert "message text: omitted" in capsys.readouterr().out


def test_live_ops_smoke_fails_when_source_parses_no_messages(tmp_path, capsys, monkeypatch):
    live_smoke = load_script_module("live_ops_smoke_min_parsed_for_test", ROOT / "scripts" / "live_ops_smoke.py")
    store = tmp_path / "live-smoke.ndjson"

    monkeypatch.setattr(
        live_smoke,
        "read_command_text",
        lambda command, empty_message, timeout=None: "\n\n",
    )

    result = live_smoke.main(
        [
            "--source-command",
            "discord-visible-text",
            "--store",
            str(store),
            "--reset",
        ]
    )
    output = capsys.readouterr().out

    assert result == 2
    assert "parsed: 0" in output
    assert "min parsed: 1" in output
    assert "Discord 可視本文をまだ解析できていません。" in output
    assert "gate verdict: source_not_ready" in output


def test_visible_text_adapter_builds_macos_accessibility_script():
    adapter = load_script_module("visible_text_adapter_macos_for_test", ROOT / "scripts" / "read_visible_discord_text.py")

    script = adapter.build_macos_accessibility_script("Discord")

    assert 'process "Discord"' in script
    assert 'attribute "AXFocusedUIElement"' in script
    assert "focused UI element" not in script
    assert "set targetWindow to front window" in script
    assert "entire contents of targetWindow" in script
    assert "appendText" in script


def test_visible_text_adapter_builds_focused_only_macos_script():
    adapter = load_script_module(
        "visible_text_adapter_macos_focused_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script("Discord", focused_only=True)

    assert "set focusedOnly to true" in script
    assert "focused element text not found" in script
    assert "entire contents of targetWindow" in script


def test_visible_text_adapter_builds_no_focus_macos_script():
    adapter = load_script_module(
        "visible_text_adapter_macos_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script("Discord", focus_app=False)

    assert "set shouldFocusApp to false" in script
    assert "set frontmost to true" in script
    assert "if shouldFocusApp then" in script
    assert "set targetWindow to window 1" in script
    assert "focused-only requires app focus" in script


def test_visible_text_adapter_passes_timeout_to_source_command(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_timeout_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    class Completed:
        returncode = 0
        stdout = "member-a: 画面に見えている本文です\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    result = adapter.main(["--source-command", "discord-visible-text", "--timeout", "4"])

    assert result == 0
    assert calls[0][1]["timeout"] == 4
    assert "画面に見えている本文です" in capsys.readouterr().out


def test_visible_text_adapter_builds_macos_window_list_script():
    adapter = load_script_module(
        "visible_text_adapter_macos_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_window_list_script('Discord "Canary"')

    assert 'process "Discord \\"Canary\\""' in script
    assert "repeat with candidateWindow in windows" in script
    assert "candidateName" in script
    assert "windowIndex" in script


def test_visible_text_adapter_lists_macos_windows(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_list_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    class Completed:
        returncode = 0
        stdout = "1\tDiscord\n2\tNexus AI - Discord\n"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    result = adapter.main(["--list-macos-windows", "--window-name-contains", "Nexus", "--timeout", "4"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][1]["timeout"] == 4
    assert "Discord window candidates:" in output
    assert "- 1: title_present=true title_length=7 hint_match=false" in output
    assert "- 2: title_present=true title_length=18 hint_match=true" in output
    assert "Nexus AI - Discord" not in output


def test_visible_text_adapter_preflights_empty_macos_window_list(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_preflight_empty_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "")

    with pytest.raises(RuntimeError, match="Discord window candidates not found"):
        adapter.preflight_macos_window_selection("Discord", timeout=3)


def test_visible_text_adapter_preflights_macos_window_index(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_preflight_index_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n")

    with pytest.raises(RuntimeError, match="window index not found before scan: 2; candidate_count=1"):
        adapter.preflight_macos_window_selection("Discord", window_index=2, timeout=3)


def test_visible_text_adapter_can_show_macos_window_names_with_opt_in(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_show_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    class Completed:
        returncode = 0
        stdout = "1\tDiscord\n2\tNexus AI - Discord\n"
        stderr = ""

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--list-macos-windows", "--show-window-names"])
    output = capsys.readouterr().out

    assert result == 0
    assert "- 1: Discord" in output
    assert "- 2: Nexus AI - Discord" in output


def test_visible_text_adapter_blocks_sensitive_macos_window_names_when_shown(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_sensitive_windows_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    class Completed:
        returncode = 0
        stdout = "1\tDiscord 123456789012345678\n"
        stderr = ""

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--list-macos-windows", "--show-window-names"])
    captured = capsys.readouterr()

    assert result == 2
    assert captured.out == ""
    assert "安全監査に失敗" in captured.err
    assert "discord_snowflake_id" in captured.err


def test_visible_text_adapter_can_select_macos_window_by_name_hint():
    adapter = load_script_module(
        "visible_text_adapter_macos_window_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script('Discord "Canary"', "nexus-ai")

    assert 'process "Discord \\"Canary\\""' in script
    assert 'set windowNameHint to "nexus-ai"' in script
    assert "repeat with candidateWindow in windows" in script
    assert "if candidateName contains windowNameHint" in script
    assert "window not found" in script
    assert 'process "Discord "Canary""' not in script


def test_visible_text_adapter_can_select_macos_window_by_index():
    adapter = load_script_module(
        "visible_text_adapter_macos_window_index_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    script = adapter.build_macos_accessibility_script("Discord", window_index=2)

    assert "set targetWindowIndex to 2" in script
    assert "window index not found" in script
    assert "set targetWindow to window targetWindowIndex" in script


def test_visible_text_adapter_auto_fallback_tries_focused_before_full(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        calls.append(
            {
                "process_name": process_name,
                "window_name_contains": window_name_contains,
                "window_index": window_index,
                "timeout": timeout,
                "focused_only": focused_only,
                "focus_app": focus_app,
            }
        )
        if focused_only:
            return ""
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    text = adapter.read_macos_accessibility_auto("Discord", window_index=2, timeout=3)

    assert text == "member-a: 画面に見えている本文です\n"
    assert calls == [
        {
            "process_name": "Discord",
            "window_name_contains": "",
            "window_index": 2,
            "timeout": 3,
            "focused_only": True,
            "focus_app": True,
        },
        {
            "process_name": "Discord",
            "window_name_contains": "",
            "window_index": 2,
            "timeout": 3,
            "focused_only": False,
            "focus_app": True,
        },
    ]


def test_visible_text_adapter_auto_fallback_reports_attempts(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_error_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        if focused_only:
            raise RuntimeError("focused element text not found")
        return ""

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    with pytest.raises(RuntimeError, match="selected focused: focused element text not found"):
        adapter.read_macos_accessibility_auto("Discord", timeout=3)


def test_visible_text_adapter_auto_fallback_can_skip_focus(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        calls.append({"focused_only": focused_only, "focus_app": focus_app, "window_index": window_index})
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    text = adapter.read_macos_accessibility_auto("Discord", window_index=2, timeout=3, focus_app=False)

    assert text == "member-a: 画面に見えている本文です\n"
    assert calls == [{"focused_only": False, "focus_app": False, "window_index": 2}]


def test_visible_text_adapter_no_focus_does_not_fallback_to_front_window(monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_no_focus_no_front_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_read(
        process_name,
        window_name_contains="",
        *,
        window_index=0,
        timeout=None,
        focused_only=False,
        focus_app=True,
    ):
        calls.append({"focused_only": focused_only, "focus_app": focus_app, "window_index": window_index})
        return ""

    monkeypatch.setattr(adapter, "read_macos_accessibility", fake_read)

    with pytest.raises(RuntimeError, match="selected full: empty"):
        adapter.read_macos_accessibility_auto("Discord", window_index=2, timeout=3, focus_app=False)

    assert calls == [{"focused_only": False, "focus_app": False, "window_index": 2}]


def test_visible_text_adapter_cli_uses_macos_auto_fallback(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_cli_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append((process_name, window_name_contains, window_index, timeout, focus_app))
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "preflight_macos_window_selection", lambda *args, **kwargs: [(2, "Discord")])
    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--macos-accessibility-auto", "--window-index", "2", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [("Discord", "", 2, 3.0, True)]
    assert output == "member-a: 画面に見えている本文です\n"


def test_visible_text_adapter_cli_can_use_macos_auto_without_focus(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_macos_auto_cli_no_focus_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append((process_name, window_name_contains, window_index, timeout, focus_app))
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "preflight_macos_window_selection", lambda *args, **kwargs: [(2, "Discord")])
    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--macos-accessibility-auto", "--no-focus", "--window-index", "2", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [("Discord", "", 2, 3.0, False)]
    assert output == "member-a: 画面に見えている本文です\n"


def test_visible_text_adapter_probe_reports_safe_route_without_body(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_probe_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )
    calls = []

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n2\tNexus AI\n")

    def fake_auto(process_name, window_name_contains="", *, window_index=0, timeout=None, focus_app=True):
        calls.append({"window_index": window_index, "focus_app": focus_app})
        if window_index == 1:
            return ""
        return "member-a: 画面に見えている本文です\n"

    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--probe-macos-accessibility", "--no-focus", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 0
    assert calls == [
        {"window_index": 1, "focus_app": False},
        {"window_index": 2, "focus_app": False},
    ]
    assert "Discord Accessibility route probe:" in output
    assert "focus_app: false" in output
    assert "- 1: status=empty chars=0 lines=0" in output
    assert "- 2: status=ready" in output
    assert "画面に見えている本文" not in output
    assert "Nexus AI" not in output


def test_visible_text_adapter_probe_blocks_unsafe_route_without_body(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_probe_unsafe_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n")
    monkeypatch.setattr(
        adapter,
        "read_macos_accessibility_auto",
        lambda *args, **kwargs: "member-a: https://discord.com/api/webhooks/123456789012345678/token\n",
    )

    result = adapter.main(["--probe-macos-accessibility", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 2
    assert "status=unsafe" in output
    assert "issues=discord_webhook_url" in output
    assert "webhooks/123456789012345678" not in output


def test_visible_text_adapter_probe_redacts_sensitive_error_reason(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_probe_redact_error_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    monkeypatch.setattr(adapter, "list_macos_windows", lambda process_name, timeout=None: "1\tDiscord\n")

    def fake_auto(*args, **kwargs):
        raise RuntimeError("failed near https://discord.com/api/webhooks/123456789012345678/token")

    monkeypatch.setattr(adapter, "read_macos_accessibility_auto", fake_auto)

    result = adapter.main(["--probe-macos-accessibility", "--timeout", "3"])
    output = capsys.readouterr().out

    assert result == 2
    assert "reason=安全監査により詳細を省略しました。" in output
    assert "webhooks/123456789012345678" not in output


def test_visible_text_adapter_source_command_error_redacts_sensitive_stderr(capsys, monkeypatch):
    adapter = load_script_module(
        "visible_text_adapter_source_command_error_for_test",
        ROOT / "scripts" / "read_visible_discord_text.py",
    )

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failed near https://discord.com/api/webhooks/123456789012345678/token"

    monkeypatch.setattr(adapter.subprocess, "run", lambda command, **kwargs: Completed())

    result = adapter.main(["--source-command", "private-adapter"])
    captured = capsys.readouterr()

    assert result == 1
    assert "安全監査により詳細を省略しました。" in captured.err
    assert "webhooks/123456789012345678" not in captured.err
    assert "private-adapter" not in captured.err


def test_gh_guard_parses_github_remote_owner():
    gh_guard = load_script_module("gh_guard_for_test", ROOT / "scripts" / "gh_guard.py")

    assert gh_guard.parse_github_owner("https://github.com/nexus-ai-2045/discord-context-bridge.git") == "nexus-ai-2045"
    assert gh_guard.parse_github_owner("git@github.com:nexus-ai-2045/discord-context-bridge.git") == "nexus-ai-2045"
    assert gh_guard.parse_github_owner("https://example.com/nexus-ai-2045/discord-context-bridge.git") is None


def test_gh_guard_parses_active_account_from_auth_status():
    gh_guard = load_script_module("gh_guard_auth_status_for_test", ROOT / "scripts" / "gh_guard.py")
    output = """
github.com
  ✓ Logged in to github.com account nexus-ai-2045 (keyring)
  - Active account: true
  - Git operations protocol: https

  ✓ Logged in to github.com account old-account (keyring)
  - Active account: false
"""

    assert gh_guard.parse_active_account(output) == "nexus-ai-2045"


def test_gh_guard_parses_active_account_from_failed_auth_status():
    gh_guard = load_script_module("gh_guard_failed_auth_status_for_test", ROOT / "scripts" / "gh_guard.py")
    output = """
github.com
  X Failed to log in to github.com account nexus-ai-2045 (default)
  - Active account: true
  - The token in default is invalid.

  X Failed to log in to github.com account old-account (default)
  - Active account: false
"""

    assert gh_guard.parse_active_account(output) == "nexus-ai-2045"


def test_gh_guard_reports_account_drift_and_switch(monkeypatch):
    gh_guard = load_script_module("gh_guard_switch_for_test", ROOT / "scripts" / "gh_guard.py")
    accounts = ["old-account", "nexus-ai-2045"]
    switched = []

    monkeypatch.setattr(gh_guard, "get_remote_url", lambda remote: "https://github.com/nexus-ai-2045/discord-context-bridge.git")
    monkeypatch.setattr(gh_guard, "get_active_gh_account", lambda: (accounts.pop(0), True, ""))
    monkeypatch.setattr(gh_guard, "switch_gh_account", lambda owner: switched.append(owner))

    report = gh_guard.build_report("origin", switch=True)

    assert report["ok"] is True
    assert report["expected_owner"] == "nexus-ai-2045"
    assert report["active_account_before"] == "old-account"
    assert report["active_account_after"] == "nexus-ai-2045"
    assert report["auth_status_ok_after"] is True
    assert report["switched"] is True
    assert switched == ["nexus-ai-2045"]


def test_cli_audit_store_returns_nonzero_when_unsafe(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    import_visible_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", path=store)

    result = cli_main(["--store", str(store), "audit-store"])
    output = capsys.readouterr().out

    assert result == 2
    assert '"safe_for_tunnel": false' in output


def test_cli_import_clipboard_reads_local_clipboard_command(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stdout = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
        stderr = ""

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    store = tmp_path / "events.ndjson"

    result = cli_main(
        [
            "--store",
            str(store),
            "import-clipboard",
            "--clipboard-command",
            "pbpaste",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert calls[0][0] == ["pbpaste"]
    assert calls[0][1]["capture_output"] is True
    assert '"parsed": 3' in output
    assert len(load_events(store)) == 3


def test_cli_watch_clipboard_imports_only_changed_text(tmp_path, capsys, monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    fixture = RICH_COPY_FIXTURE.read_text(encoding="utf-8")
    outputs = [fixture, fixture, "member-d: 追加の前提です"]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed(outputs.pop(0))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_module.time, "sleep", lambda interval: None)
    store = tmp_path / "events.ndjson"

    result = cli_main(
        [
            "--store",
            str(store),
            "watch-clipboard",
            "--clipboard-command",
            "pbpaste",
            "--max-polls",
            "3",
            "--interval",
            "0",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert len(calls) == 3
    assert '"polls": 3' in output
    assert '"changed": 2' in output
    assert "clipboard の変化を取り込みました。" in output
    assert "clipboard 取り込み" in output
    assert "clipboard 監視を終了しました。" in output
    assert "clipboard 監視完了" in output
    assert len(load_events(store)) == 4


def test_mcp_dependency_error_is_japanese(monkeypatch):
    def missing_fastmcp():
        raise RuntimeError(mcp_server.MCP_DEPENDENCY_HELP)

    monkeypatch.setattr(mcp_server, "_load_fastmcp", missing_fastmcp)

    with pytest.raises(RuntimeError, match="MCP server を使うには"):
        mcp_server.build_server()


def test_mcp_server_registers_context_tools(monkeypatch, tmp_path):
    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings
            self.tools = {}

        def tool(self):
            def register(func):
                self.tools[func.__name__] = func
                return func

            return register

        def run(self):
            raise AssertionError("unit test should not start stdio server")

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    server = mcp_server.build_server(store=tmp_path / "events.ndjson", context_store=tmp_path / "context-library.json")

    assert server.name == "discord-context-bridge"
    assert server.settings["host"] == "127.0.0.1"
    assert server.settings["port"] == 8000
    assert server.settings["streamable_http_path"] == "/mcp"
    assert sorted(server.tools) == [
        "audit_context_library_before_tunnel",
        "audit_event_store_before_tunnel",
        "get_context_passport_from_visible_text",
        "get_fast_briefing",
        "guide_reply_from_visible_text",
        "import_visible_discord_text",
        "list_context_library_entries",
        "review_reply_before_send",
        "upsert_context_library_entry",
    ]

    imported = server.tools["import_visible_discord_text"]("member-a: 前提を確認したいです", dry_run=True)
    audit = server.tools["audit_event_store_before_tunnel"]()
    saved_context = server.tools["upsert_context_library_entry"](
        "server",
        "nexus",
        SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
        source="fixture",
    )
    context_list = server.tools["list_context_library_entries"]()
    context_audit = server.tools["audit_context_library_before_tunnel"]()
    review = server.tools["review_reply_before_send"]("前提を確認してから返信します")
    guide = server.tools["guide_reply_from_visible_text"](
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "公開時期の前提を確認してから返信します。",
    )
    passport = server.tools["get_context_passport_from_visible_text"](
        PASSPORT_FIXTURE.read_text(encoding="utf-8"),
        server_context_key="nexus",
    )

    assert imported["language"] == "ja"
    assert imported["dry_run"] is True
    assert audit["safe_for_tunnel"] is True
    assert saved_context["created"] is True
    assert context_list["count"] == 1
    assert context_audit["safe_for_tunnel"] is True
    assert review["language"] == "ja"
    assert guide["message"] == "Discord 返信ガイドを作成しました。"
    assert passport["message"] == "スレッド文脈カードを作成しました。"
    assert passport["people_temperature"] == "serious"
    assert passport["external_context_used"] is True
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])


def test_http_mcp_entrypoint_uses_streamable_http(monkeypatch, tmp_path):
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

        def run(self, **kwargs):
            calls.append((self, kwargs))

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    result = mcp_server.main_http(
        [
            "--store",
            str(tmp_path / "events.ndjson"),
            "--host",
            "0.0.0.0",
            "--port",
            "8787",
            "--path",
            "/mcp",
        ]
    )

    assert result == 0
    server, run_kwargs = calls[0]
    assert server.settings["host"] == "0.0.0.0"
    assert server.settings["port"] == 8787
    assert server.settings["streamable_http_path"] == "/mcp"
    assert run_kwargs == {"transport": "streamable-http", "mount_path": "/mcp"}
    assert "streamable HTTP MCP server" in mcp_server.build_http_parser().format_help()


def test_http_mcp_require_safe_store_blocks_unsafe_store(monkeypatch, tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text("member-a: https://discord.com/api/webhooks/123456789012345678/token", path=store)
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    with pytest.raises(SystemExit, match="保存データの監査に失敗しました"):
        mcp_server.main_http(
            [
                "--store",
                str(store),
                "--require-safe-store",
            ],
            run=lambda server: calls.append(server),
        )

    assert calls == []


def test_http_mcp_require_safe_store_allows_safe_store(monkeypatch, tmp_path):
    store = tmp_path / "events.ndjson"
    import_visible_text("member-a: 前提を確認してから返信します", path=store)
    calls = []

    class FakeFastMCP:
        def __init__(self, name, **settings):
            self.name = name
            self.settings = settings

        def tool(self):
            def register(func):
                return func

            return register

    monkeypatch.setattr(mcp_server, "_load_fastmcp", lambda: FakeFastMCP)

    result = mcp_server.main_http(
        [
            "--store",
            str(store),
            "--require-safe-store",
        ],
        run=lambda server: calls.append(server),
    )

    assert result == 0
    assert len(calls) == 1
    assert "--require-safe-store" in mcp_server.build_http_parser().format_help()
