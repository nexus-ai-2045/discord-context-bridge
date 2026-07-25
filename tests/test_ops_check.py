import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import ops_check  # noqa: E402
from discord_context_bridge.process_runner import minimal_child_env  # noqa: E402


def _child_env() -> dict[str, str]:
    env = minimal_child_env()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _args(profile: str = "full") -> argparse.Namespace:
    return argparse.Namespace(profile=profile, port=8025, http=False, gh=False, gh_account_only=False)


def test_capture_store_layout_lint_is_registered_in_full_profile():
    checks = ops_check.build_checks(_args("full"))
    assert "capture store layout lint" in checks


def test_capture_store_layout_lint_is_not_in_fast_profile():
    checks = ops_check.build_checks(_args("fast"))
    assert "capture store layout lint" not in checks


def test_capture_store_layout_lint_skips_when_baseline_missing(tmp_path):
    """H6: baseline 未生成の初回実行では fail させず、情報提供に留める。"""
    result = ops_check.run_capture_store_layout_lint(_child_env(), root=tmp_path)

    assert result.ok is True
    assert "baseline" in result.output


def test_capture_store_layout_lint_enforces_when_baseline_present(tmp_path):
    store_root = tmp_path / ".local" / "discord-context-bridge"
    store_root.mkdir(parents=True)
    (store_root / "lint-baseline.json").write_text(
        '{"schema": "dcb.lint_capture_store_layout_baseline.v1", "violations": []}',
        encoding="utf-8",
    )

    result = ops_check.run_capture_store_layout_lint(_child_env(), root=tmp_path)

    assert result.ok is True
    assert "lint_capture_store_layout.py" in " ".join(result.command)
