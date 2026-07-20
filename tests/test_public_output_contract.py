"""公開出力契約テスト。

README の安全境界「raw Discord text、参加者名、local absolute path を
公開出力に含めません」を、ドキュメント文言ではなく実際の関数戻り値と
CLI stdout に対して固定する。

対象: import-visible-text / context-passport / guide-reply / review-intent の
CLI・core 両経路。bridge-intake / chew-discord-context は既に whitelist 済み。
"""

import json
from pathlib import Path

from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.core import (
    context_passport_from_text,
    guide_reply_from_text,
    import_visible_text,
    parse_visible_text,
    review_reply_intent,
)

FIXTURE = Path(__file__).parent / "fixtures" / "visible_text.txt"
PASSPORT_FIXTURE = Path(__file__).parent / "fixtures" / "thread_context_passport.txt"
RICH_COPY_FIXTURE = Path(__file__).parent / "fixtures" / "discord_rich_copy.txt"
SERVER_CONTEXT_FIXTURE = Path(__file__).parent / "fixtures" / "server_context.txt"

# 可視テキスト由来の raw 断片 (公開出力に出てはいけない)
RAW_AUTHORS = ("member-a", "member-b", "member-c", "mod-a")
RAW_SNIPPETS = (
    "Can you clarify the premise",
    "The context is about the launch timing",
    "このチャンネルは公開前の企画相談用です",
    "公開時期の話ですよね",
    "相手に伝わる言い方にしたいです",
)


def _assert_no_raw(payload_text: str) -> None:
    for author in RAW_AUTHORS:
        assert author not in payload_text, f"参加者名が公開出力に露出: {author}"
    for snippet in RAW_SNIPPETS:
        assert snippet not in payload_text, f"raw 本文が公開出力に露出: {snippet}"


def test_import_visible_text_result_is_public_safe(tmp_path):
    store = tmp_path / "events.ndjson"
    for dry_run in (False, True):
        result = import_visible_text(
            FIXTURE.read_text(encoding="utf-8"), path=store, dry_run=dry_run
        )
        dumped = json.dumps(result, ensure_ascii=False)
        _assert_no_raw(dumped)
        assert str(tmp_path) not in dumped, "local absolute path が公開出力に露出"
        assert result["parsed"] == 3
        assert result["preview"][0]["author_label"].startswith("participant-")
        assert result["preview"][0]["text_snippet"] == "omitted"


def test_context_passport_result_is_public_safe():
    passport = context_passport_from_text(PASSPORT_FIXTURE.read_text(encoding="utf-8"))
    dumped = json.dumps(passport, ensure_ascii=False)
    _assert_no_raw(dumped)
    assert passport["raw_text_returned"] is False
    assert passport["participant_names_returned"] is False
    # 派生ラベル (目的・温度・入り方) は維持される
    assert passport["thread_purpose"]
    assert passport["people_temperature"]
    assert passport["natural_entry_angles"]
    assert passport["context_ready"] is True


def test_context_passport_keeps_explicit_context_notes():
    # 明示文脈ドキュメント (ユーザー自身の入力) 由来の note は維持してよい
    passport = context_passport_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        server_context=SERVER_CONTEXT_FIXTURE.read_text(encoding="utf-8"),
    )
    dumped = json.dumps(passport, ensure_ascii=False)
    _assert_no_raw(dumped)
    assert any("個人情報の共有は禁止" in note for note in passport["rule_notes"])


def test_guide_reply_result_is_public_safe():
    guide = guide_reply_from_text(
        RICH_COPY_FIXTURE.read_text(encoding="utf-8"),
        "公開時期の前提を確認してから返信します。",
        understanding_confirmed=True,
    )
    _assert_no_raw(json.dumps(guide, ensure_ascii=False))
    assert guide["reply_review"]["quick_verdict"] == "go"


def test_review_reply_intent_result_is_public_safe():
    events = parse_visible_text(FIXTURE.read_text(encoding="utf-8"))
    for confirmed in (True, False):
        review = review_reply_intent(
            "前提を確認してから返します。", events, understanding_confirmed=confirmed
        )
        _assert_no_raw(json.dumps(review, ensure_ascii=False))


def test_cli_context_passport_stdout_is_public_safe(capsys):
    input_path = PASSPORT_FIXTURE
    for extra in ([], ["--json"]):
        result = cli_main(
            ["context-passport", "--input", str(input_path), *extra]
        )
        output = capsys.readouterr().out
        assert result == 0
        _assert_no_raw(output)


def test_cli_import_visible_text_stdout_is_public_safe(tmp_path, capsys):
    store = tmp_path / "events.ndjson"
    result = cli_main(
        [
            "--store",
            str(store),
            "import-visible-text",
            "--input",
            str(FIXTURE),
        ]
    )
    output = capsys.readouterr().out
    assert result == 0
    _assert_no_raw(output)
    assert str(tmp_path) not in output, "local absolute path が公開出力に露出"


def test_cli_guide_reply_stdout_is_public_safe(capsys):
    # gate で blocked になる経路 (exit 2) も含めて、出力は常に public safe であること
    for extra in ([], ["--json"]):
        result = cli_main(
            [
                "guide-reply",
                "--input",
                str(RICH_COPY_FIXTURE),
                "--draft",
                "公開時期の前提を確認してから返信します。",
                "--understanding-confirmed",
                *extra,
            ]
        )
        output = capsys.readouterr().out
        assert result in (0, 2)
        _assert_no_raw(output)
