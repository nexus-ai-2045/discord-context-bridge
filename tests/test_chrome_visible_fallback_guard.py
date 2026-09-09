from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_guard_claims_existing_exact_target_without_new_tab() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[
            {
                "title": "Discord | target",
                "id": "tab-target",
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
    assert payload["chrome_action_policy"]["canonical_identity"] == "url_route"
    assert payload["chrome_action_policy"]["title_used_for_matching"] is False
    assert payload["inventory"]["matching_target_tab_count"] == 1
    assert payload["inventory"]["selected_tab_group"] == "codex"


def test_guard_reuses_other_discord_tab_instead_of_stopping() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[
            {
                "title": "Discord | other channel",
                "id": "tab-other",
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
        open_tabs=[
            {
                "title": "Discord | other guild",
                "id": "tab-other",
                "url": (
                    "https://discord.com/channels/"
                    f"{SYNTHETIC_OTHER_GUILD_ID}/{SYNTHETIC_OTHER_CHANNEL_ID}"
                ),
                "tabGroup": "Codex",
            },
            {
                "title": "Discord | sibling channel",
                "id": "tab-sibling",
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
    assert payload["inventory"]["selected_tab_group"] == "other"


def test_guard_ignores_matching_title_when_url_route_differs() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[
            {
                "id": "tab-1",
                "providerTabId": "provider-tab-1",
                "title": "Discord | target",
                "url": (
                    "https://discord.com/channels/"
                    f"{SYNTHETIC_OTHER_GUILD_ID}/{SYNTHETIC_OTHER_CHANNEL_ID}"
                ),
                "tabGroup": "Codex",
            }
        ],
    )

    assert payload["decision"] == "claim_existing_discord_tab_then_navigate"
    assert payload["inventory"]["matching_target_tab_count"] == 0
    assert payload["inventory"]["selected_claim_handle_present"] is True
    assert payload["inventory"]["candidates"][0]["title_used_for_matching"] is False
    assert payload["inventory"]["candidates"][0]["matching_basis"] == "canonical_url_route"


def test_guard_opens_new_tab_only_when_no_reusable_discord_tab() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[{"title": "Example", "url": "https://example.com"}],
    )

    assert payload["ok"] is True
    assert payload["decision"] == "open_new_tab_in_existing_window"
    assert payload["reason"] == "no_reusable_discord_tabs"
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is False
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is True


def test_guard_omits_raw_urls_and_ids_from_cli_json(tmp_path: Path) -> None:
    tabs_path = tmp_path / "tabs.json"
    tabs_path.write_text(
        json.dumps(
            [
                {
                    "title": "Discord | target",
                    "id": "tab-target",
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
    assert "raw_url_output" in completed.stdout


def test_nested_threads_do_not_alias() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET + "/threads/666666666666666666",
        open_tabs=[{"id": "tab", "url": TARGET + "/threads/777777777777777777"}],
    )
    assert payload["inventory"]["matching_target_tab_count"] == 0
    assert payload["chrome_action_policy"]["navigate_after_claim"] is True


@pytest.mark.parametrize("handle", [None, "", "   ", False, [], {}])
def test_missing_or_invalid_handle_blocks_claim(handle: object) -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET, open_tabs=[{"url": TARGET, "id": handle}],
    )
    assert payload["ok"] is False
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is False
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is False


def test_group_name_is_never_returned() -> None:
    private_group = "private-group-marker"
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET, open_tabs=[{"id": "tab", "url": TARGET, "tabGroup": private_group}],
    )
    assert private_group not in json.dumps(payload)
    assert payload["inventory"]["selected_tab_group"] == "other"


def test_invalid_target_blocks_all_actions() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url="https://example.com/channels/1/2", open_tabs=[],
    )
    assert payload["ok"] is False
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is False


def test_original_inventory_index_and_numeric_handle_are_preserved() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[{"url": "https://example.com"}, {"id": 0, "url": TARGET}],
    )
    assert payload["inventory"]["selected_candidate_index"] == 1
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is True


def test_unclaimable_exact_tab_does_not_shadow_claimable_sibling() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[{"url": TARGET}, {"providerTabId": "valid", "id": {},
                                    "url": TARGET + "/threads/666666666666666666"}],
    )
    assert payload["inventory"]["selected_candidate_index"] == 1
    assert payload["chrome_action_policy"]["navigate_after_claim"] is True


def test_nested_and_direct_thread_routes_share_canonical_identity() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET + "/threads/666666666666666666",
        open_tabs=[{"id": "tab", "url": "https://canary.discord.com/channels/"
                    + SYNTHETIC_GUILD_ID + "/666666666666666666?view=1"}],
    )
    assert payload["decision"] == "claim_existing_target_tab"


def test_distinct_nested_messages_require_navigation() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET + "/threads/666666666666666666/888888888888888888",
        open_tabs=[{"id": "tab", "url": TARGET + "/threads/666666666666666666/999999999999999999"}],
    )
    assert payload["chrome_action_policy"]["navigate_after_claim"] is True


@pytest.mark.parametrize("target,tabs", [("https://[invalid", []), (TARGET, [None])])
def test_malformed_inputs_return_safe_blocked(target: str, tabs: list) -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=target, open_tabs=tabs,
    )
    assert payload["ok"] is False
    assert payload["chrome_action_policy"]["ok_to_claim_existing_tab"] is False
    assert payload["chrome_action_policy"]["ok_to_open_new_tab"] is False
