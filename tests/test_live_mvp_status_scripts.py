import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import discord_bot_route_preflight
import e2e_private_adapter_check
import live_mvp_status
import ops_preflight


def test_public_safe_payload_omits_nested_store_paths():
    payload = {
        "live_smoke": {
            "store": "/tmp/private/events.ndjson",
            "nested": [{"store": "/tmp/other/events.ndjson"}],
        }
    }

    assert e2e_private_adapter_check.public_safe_payload(payload) == {
        "live_smoke": {
            "store": "omitted",
            "nested": [{"store": "omitted"}],
        }
    }
    assert live_mvp_status.public_safe_payload(payload)["live_smoke"]["store"] == "omitted"


def test_bot_route_env_status_detects_token_without_returning_value(tmp_path: Path):
    env_path = tmp_path / ".env"
    token_key = "DISCORD_" + "BOT_TOKEN"
    env_path.write_text(f"{token_key}=synthetic-secret\n", encoding="utf-8")

    status = discord_bot_route_preflight.read_env_status(env_path)

    assert status == {"exists": True, "token_set": True}
    assert "synthetic-secret" not in str(status)


def test_ops_preflight_reports_system_events_timeout(monkeypatch):
    monkeypatch.setattr(ops_preflight.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ops_preflight, "run_osascript", lambda *args, **kwargs: (False, "timeout"))

    status = ops_preflight.discord_process_status("Discord")

    assert status == {
        "checked": False,
        "running": False,
        "window_count": 0,
        "reason": "system_events_timeout",
    }
