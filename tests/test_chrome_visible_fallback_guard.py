from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import chrome_visible_fallback_guard


SYNTHETIC_GUILD_ID = "111111111111111111"
SYNTHETIC_TARGET_CHANNEL_ID = "222222222222222222"
SYNTHETIC_SIBLING_CHANNEL_ID = "333333333333333333"
SYNTHETIC_OTHER_GUILD_ID = "444444444444444444"
SYNTHETIC_OTHER_CHANNEL_ID = "555555555555555555"
TARGET = (
    "https://discord.com/channels/"
    f"{SYNTHETIC_GUILD_ID}/{SYNTHETIC_TARGET_CHANNEL_ID}"
)
SNAPSHOT_TOKEN = "opaque-snapshot-sequence-1"


def test_guard_claims_existing_exact_target_without_new_tab() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        binding_token=SNAPSHOT_TOKEN,
        open_tabs=[
            {
                "providerTabId": "provider-tab-target",
                "title": "Discord | target",
                "url": TARGET,
                "tabGroup": "Codex",
                "lastOpened": "2026-07-07T00:00:00Z",
            }
        ],
    )

    assert payload["ok"] is True
    assert payload["decision"] == "claim_existing_target_tab"
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is True
    assert payload["chrome_action_policy"]["navigate_after_claim"] is False
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is False
    assert payload["inventory"]["matching_target_tab_count"] == 1
    assert payload["snapshot_binding"]["binding_token_kind"] == "caller_supplied_opaque_binding_only"
    assert payload["claim_binding"]["required_fields"] == ["providerTabId", "title", "url"]
    assert payload["chrome_action_policy"]["goto"] == "forbidden_target_already_exact"
    assert payload["chrome_action_policy"]["reload"] == "forbidden_target_already_exact"
    rendered = json.dumps(payload)
    assert SNAPSHOT_TOKEN not in rendered
    assert "provider-tab-target" not in rendered


def test_guard_reuses_other_discord_tab_instead_of_stopping() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        binding_token=SNAPSHOT_TOKEN,
        open_tabs=[
            {
                "providerTabId": "provider-tab-other",
                "title": "Discord | other channel",
                "url": (
                    "https://discord.com/channels/"
                    f"{SYNTHETIC_OTHER_GUILD_ID}/{SYNTHETIC_OTHER_CHANNEL_ID}"
                ),
                "tabGroup": "Codex",
            },
            {"title": "Example", "url": "https://example.com"},
        ],
    )

    assert payload["ok"] is True
    assert payload["decision"] == "claim_existing_discord_tab_then_navigate"
    assert payload["reason"] == "target_absent_but_reusable_discord_tab_exists"
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is True
    assert payload["chrome_action_policy"]["navigate_after_claim"] is True
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is False
    assert payload["inventory"]["selected_candidate_index"] == 0


def test_guard_prefers_same_guild_tab_for_navigation() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        binding_token=SNAPSHOT_TOKEN,
        open_tabs=[
            {
                "providerTabId": "provider-tab-other-guild",
                "title": "Discord | other guild",
                "url": (
                    "https://discord.com/channels/"
                    f"{SYNTHETIC_OTHER_GUILD_ID}/{SYNTHETIC_OTHER_CHANNEL_ID}"
                ),
                "tabGroup": "Codex",
            },
            {
                "providerTabId": "provider-tab-sibling",
                "title": "Discord | sibling channel",
                "url": (
                    "https://discord.com/channels/"
                    f"{SYNTHETIC_GUILD_ID}/{SYNTHETIC_SIBLING_CHANNEL_ID}"
                ),
                "tabGroup": "Other",
            },
        ],
    )

    assert payload["ok"] is True
    assert payload["decision"] == "claim_existing_discord_tab_then_navigate"
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is False
    assert payload["inventory"]["same_guild_tab_count"] == 1
    assert payload["inventory"]["selected_candidate_index"] == 1


def test_guard_opens_new_tab_only_when_no_reusable_discord_tab() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        binding_token=SNAPSHOT_TOKEN,
        open_tabs=[{"title": "Example", "url": "https://example.com"}],
    )

    assert payload["ok"] is True
    assert payload["decision"] == "open_new_tab_in_existing_window"
    assert payload["reason"] == "no_reusable_discord_tabs"
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is False
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is True


def test_guard_fails_closed_without_fresh_snapshot_or_claim_fields() -> None:
    missing_snapshot = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        binding_token="",
        open_tabs=[{"providerTabId": "tab", "title": "target", "url": TARGET}],
    )
    assert missing_snapshot["ok"] is False
    assert missing_snapshot["reason"] == "open_tabs_binding_token_missing"

    incomplete_claim = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        binding_token=SNAPSHOT_TOKEN,
        open_tabs=[{"title": "target", "url": TARGET}],
    )
    assert incomplete_claim["ok"] is False
    assert incomplete_claim["reason"] == "selected_claim_object_missing_required_fields"

    wrong_host = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET.replace("discord.com", "example.com"),
        binding_token=SNAPSHOT_TOKEN,
        open_tabs=[{"providerTabId": "tab", "title": "target", "url": TARGET}],
    )
    assert wrong_host["ok"] is False
    assert wrong_host["reason"] == "target_not_discord_channel_route"


def test_guard_omits_raw_urls_and_ids_from_cli_json(tmp_path: Path) -> None:
    tabs_path = tmp_path / "tabs.json"
    tabs_path.write_text(
        json.dumps(
            [
                {
                    "providerTabId": "provider-tab-target",
                    "title": "Discord | target",
                    "url": TARGET,
                    "tabGroup": "Codex",
                }
            ]
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/chrome_visible_fallback_guard.py",
            "--target-url",
            TARGET,
            "--open-tabs-json",
            str(tabs_path),
            "--binding-token",
            SNAPSHOT_TOKEN,
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert SYNTHETIC_GUILD_ID not in completed.stdout
    assert SYNTHETIC_TARGET_CHANNEL_ID not in completed.stdout
    assert TARGET not in completed.stdout
    assert SNAPSHOT_TOKEN not in completed.stdout
    assert "provider-tab-target" not in completed.stdout
    assert "raw_url_output" in completed.stdout
