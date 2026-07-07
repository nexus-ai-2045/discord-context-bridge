from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import chrome_visible_fallback_guard


TARGET = "https://discord.com/channels/1523174533052235907/1523174533513875568"


def test_guard_claims_existing_exact_target_without_new_tab() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[
            {
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


def test_guard_reuses_other_discord_tab_instead_of_stopping() -> None:
    payload = chrome_visible_fallback_guard.decide_visible_fallback(
        target_url=TARGET,
        open_tabs=[
            {
                "title": "Discord | other channel",
                "url": "https://discord.com/channels/111111111111111111/222222222222222222",
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
                "url": "https://discord.com/channels/111111111111111111/222222222222222222",
                "tabGroup": "Codex",
            },
            {
                "title": "Discord | sibling channel",
                "url": "https://discord.com/channels/1523174533052235907/999999999999999999",
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
    assert "1523174533052235907" not in completed.stdout
    assert "1523174533513875568" not in completed.stdout
    assert TARGET not in completed.stdout
    assert "raw_url_output" in completed.stdout
