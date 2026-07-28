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


def test_full_test_timeout_has_release_profile_headroom(monkeypatch):
    captured: dict[str, float | None] = {}

    def fake_run_command(name, command, *, env=None, timeout=None):
        captured["timeout"] = timeout
        return ops_check.CheckResult(name, True, 0.0, command, "")

    monkeypatch.setattr(ops_check, "run_command", fake_run_command)

    checks = ops_check.build_checks(_args("release"))
    checks["テスト"]()

    assert captured["timeout"] == ops_check.FULL_TEST_TIMEOUT
    assert ops_check.FULL_TEST_TIMEOUT >= 240.0


def test_main_runs_full_tests_before_parallel_smokes(monkeypatch):
    events: list[str] = []

    def result(name: str):
        events.append(name)
        return ops_check.CheckResult(name, True, 0.0, [name], "")

    monkeypatch.setattr(ops_check, "parse_args", lambda: _args("release"))
    monkeypatch.setattr(
        ops_check,
        "build_checks",
        lambda args: {
            "テスト": lambda: result("テスト"),
            "smoke-a": lambda: result("smoke-a"),
            "smoke-b": lambda: result("smoke-b"),
        },
    )

    assert ops_check.main() == 0
    assert events[0] == "テスト"
    assert set(events[1:]) == {"smoke-a", "smoke-b"}


def test_release_profile_repairs_github_account_drift(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run_command(name, command, *, env=None, timeout=None):
        captured[name] = command
        return ops_check.CheckResult(name, True, 0.0, command, "")

    monkeypatch.setattr(ops_check, "run_command", fake_run_command)

    checks = ops_check.build_checks(_args("release"))
    checks["GitHub account確認"]()

    assert "--switch" in captured["GitHub account確認"]
