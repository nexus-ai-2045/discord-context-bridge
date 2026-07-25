"""discord_route_retry_decider の spawn 失敗分類テスト。

実行ファイル欠如は例外 crash ではなく failed probe として返ること。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _load_decider():
    spec = importlib.util.spec_from_file_location(
        "route_retry_decider_spawn_test", ROOT / "scripts" / "discord_route_retry_decider.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_missing_executable_becomes_failed_probe():
    decider = _load_decider()

    result = decider.run_local_command("definitely-missing-command-xyz --json", timeout=5)

    assert result["ok"] is False
    assert result["failure_stage"] == "spawn_failed"
    assert result["returncode"] == 127
    assert result["text_output"] == "omitted"


def test_unparsable_command_becomes_failed_probe():
    decider = _load_decider()

    result = decider.run_local_command("", timeout=5)

    assert result["ok"] is False
    assert result["failure_stage"] == "spawn_failed"
