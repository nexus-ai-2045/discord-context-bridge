#!/usr/bin/env python3
"""fixtureでアーカイブ棚卸しのメタデータ限定closeoutを検証する。"""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from discord_archived_thread_inventory import main as inventory_main

from discord_context_bridge.cli import JapaneseArgumentParser


def _thread(thread_id: str, timestamp: str) -> dict[str, object]:
    return {
        "id": thread_id,
        "name": "must-not-leak",
        "thread_metadata": {"archive_timestamp": timestamp},
    }


def _invoke(arguments: list[str]) -> tuple[int, dict[str, object], str]:
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = inventory_main(arguments)
    rendered = output.getvalue()
    return exit_code, json.loads(rendered), rendered


def run_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="dcb-archive-inventory-") as temp_dir:
        root = Path(temp_dir)
        fixture = root / "fixture.json"
        fixture.write_text(
            json.dumps(
                {
                    "public": [
                        {
                            "threads": [_thread("1", "2026-09-01T00:00:00Z")],
                            "has_more": True,
                        },
                        {
                            "threads": [_thread("2", "2026-08-01T00:00:00Z")],
                            "has_more": False,
                        },
                    ],
                    "private": [{"threads": [], "has_more": False}],
                    "joined_private": [{"threads": [], "has_more": False}],
                }
            ),
            encoding="utf-8",
        )
        private_inventory = root / "private" / "inventory.json"
        exit_code, report, rendered = _invoke(
            [
                "--url",
                "https://discord.com/channels/1/2",
                "--fixture-input",
                str(fixture),
                "--output",
                str(private_inventory),
                "--json",
            ]
        )
        metadata = private_inventory.stat()
        # Windowsのstat modeはDACLの証明にならない。fixture動作と権限保証を分ける。
        permission_verification = (
            "not_verified" if os.name == "nt"
            else "passed" if stat.S_IMODE(metadata.st_mode) == 0o600
            else "failed"
        )
        stored = json.loads(private_inventory.read_text(encoding="utf-8"))
        complete_ok = bool(
            exit_code == 0
            and report.get("pagination_exhausted") is True
            and report.get("identifiers_returned") is False
            and report.get("raw_text_returned") is False
            and report.get("path_output") == "omitted"
            and report.get("outbound_actions") == "disabled"
            and "must-not-leak" not in rendered
            and stat.S_ISREG(metadata.st_mode)
            and permission_verification != "failed"
            and stored.get("parent_target_key") == "2"
            and len(stored.get("scopes", {}).get("public", {}).get("threads", [])) == 2
        )

        limited_exit, limited_report, limited_rendered = _invoke(
            [
                "--url",
                "https://discord.com/channels/1/2",
                "--scope",
                "public",
                "--max-pages",
                "1",
                "--fixture-input",
                str(fixture),
                "--output",
                str(root / "limited.json"),
                "--json",
            ]
        )
        fail_closed_ok = bool(
            limited_exit == 2
            and limited_report.get("pagination_exhausted") is False
            and "archive_pagination_page_limit_reached"
            in (limited_report.get("blockers") or [])
            and "must-not-leak" not in limited_rendered
        )
        return {
            "schema": "dcb.archived-thread-inventory-smoke.v1",
            "ok": complete_ok and fail_closed_ok,
            "verification_scope": "fixture_behavior_only",
            "permission_verification": permission_verification,
            "all_scopes_exhausted_ok": complete_ok,
            "page_limit_fail_closed_ok": fail_closed_ok,
            "raw_text_returned": False,
            "participant_names_returned": False,
            "identifiers_returned": False,
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }


def main() -> int:
    parser = JapaneseArgumentParser(description="アーカイブ棚卸しsmokeを実行します")
    parser.add_argument("--json", action="store_true", help="メタデータをJSONで表示します")
    args = parser.parse_args()
    result = run_smoke()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("アーカイブ棚卸しsmoke: " + ("成功" if result["ok"] else "失敗"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
