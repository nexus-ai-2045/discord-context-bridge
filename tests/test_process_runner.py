from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pytest

from discord_context_bridge import cli
from discord_context_bridge.process_runner import ProcessResult, minimal_child_env, run_process


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "emit_japanese_utf8.py"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"process_contract_{name}", ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "script_name",
    [
        "ops_check",
        "discord_inventory_dashboard",
        "gh_pr_read",
        "repo_goal_status",
    ],
)
def test_scripts_delegate_child_execution_to_process_runner(monkeypatch, script_name: str):
    module = load_script(script_name)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> ProcessResult:
        calls.append((command, kwargs))
        return ProcessResult(tuple(command), 0, "日本語", "", 0.01, 9, 0)

    monkeypatch.setattr(module, "run_process", fake_run)
    if script_name == "ops_check":
        result = module.run_command("fixture", ["child"], env={"DCB_FIXTURE": "1"})
    else:
        result = module.run_command(["child"])

    if script_name == "ops_check":
        assert result.output == "日本語"
    else:
        assert result.stdout == "日本語"
    _, kwargs = calls[0]
    if script_name == "ops_check":
        assert kwargs["env"]["DCB_FIXTURE"] == "1"


def test_cli_source_command_delegates_to_process_runner(monkeypatch):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> ProcessResult:
        calls.append((command, kwargs))
        return ProcessResult(tuple(command), 0, "日本語", "", 0.01, 9, 0)

    monkeypatch.setattr(cli, "run_process", fake_run)

    assert cli.read_command_text("fixture-child") == "日本語"
    _, kwargs = calls[0]
    assert isinstance(kwargs["env"], dict)


def test_minimal_child_env_keeps_launch_requirements_without_arbitrary_parent_values():
    source = {
        "PATH": "tools",
        "SystemRoot": r"C:\Windows",
        "TEMP": r"C:\Temp",
        "APPDATA": r"C:\Users\tester\AppData\Roaming",
        "DCB_PARENT_SECRET": "must-not-leak",
    }

    result = minimal_child_env(source)

    assert result["PATH"] == "tools"
    assert result["SystemRoot"] == r"C:\Windows"
    assert result["TEMP"] == r"C:\Temp"
    assert result["APPDATA"] == r"C:\Users\tester\AppData\Roaming"
    assert "DCB_PARENT_SECRET" not in result


def test_cli_source_command_decodes_real_japanese_utf8_child():
    tokens = [sys.executable, str(FIXTURE)]
    command = subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)

    assert cli.read_command_text(command) == "日本語の子プロセス出力\n"


def test_process_runner_returns_bounded_timeout_result_with_metadata():
    result = run_process([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)

    assert result.ok is False
    assert result.returncode == 124
    assert result.failure_stage == "timeout"
    assert result.elapsed_seconds >= 0.2
    assert result.elapsed == result.elapsed_seconds


def test_process_runner_truncates_returned_result_and_reports_original_sizes():
    result = run_process([sys.executable, "-c", "print('x' * 70000)"])

    assert result.ok is False
    assert result.failure_stage == "output_limit"
    assert len(result.stdout.encode("utf-8")) <= 65536
    assert result.stdout_bytes > 65536
    assert result.stderr_bytes == 0
    assert result.output_truncated is True


def test_process_runner_stops_unbounded_output_during_execution():
    started = time.perf_counter()
    result = run_process(
        [
            sys.executable,
            "-c",
            "import os; chunk=b'x'*8192\nwhile True: os.write(1, chunk)",
        ],
        timeout=10,
    )

    assert result.failure_stage == "output_limit"
    assert result.returncode == 125
    assert result.elapsed_seconds < 5
    assert time.perf_counter() - started < 5
    assert len(result.stdout.encode("utf-8")) <= 65536
    assert result.stdout_bytes > 65536


@pytest.mark.parametrize("explicit_env", [False, True])
def test_process_runner_does_not_inherit_parent_secrets(monkeypatch, explicit_env: bool):
    monkeypatch.setenv("DCB_PARENT_SECRET", "must-not-leak")

    kwargs = {"env": {}} if explicit_env else {}
    result = run_process(
        [sys.executable, "-c", "import os; print(os.environ.get('DCB_PARENT_SECRET', 'absent'))"],
        **kwargs,
    )

    assert result.ok is True
    assert result.stdout.strip() == "absent"


@pytest.mark.parametrize(
    "name",
    ["GH_TOKEN", "API_KEY", "DISCORD_WEBHOOK_URL", "SESSION_COOKIE", "AUTHORIZATION"],
)
def test_process_runner_rejects_explicit_secret_like_environment(name: str):
    with pytest.raises(ValueError, match="secret-like environment"):
        run_process([sys.executable, "-c", "print('not-run')"], env={name: "synthetic-value"})


def test_process_runner_timeout_removes_grandchild(tmp_path):
    marker = tmp_path / "grandchild-survived.txt"
    child_code = (
        "import pathlib,time; "
        "time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('survived', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )

    result = run_process([sys.executable, "-c", parent_code], timeout=0.5)
    time.sleep(1.25)

    assert result.failure_stage == "timeout"
    assert not marker.exists()


def test_github_read_helpers_never_request_account_switch():
    source = (ROOT / "scripts" / "gh_pr_read.py").read_text(encoding="utf-8")
    dashboard = (ROOT / "scripts" / "discord_inventory_dashboard.py").read_text(encoding="utf-8")
    status = (ROOT / "scripts" / "repo_goal_status.py").read_text(encoding="utf-8")

    assert 'command.insert(2, "--switch")' not in source
    assert '"--switch"' not in dashboard
    assert '"--switch"' not in status


def test_compile_checks_use_distinct_temporary_cache_roots_and_clean_them(monkeypatch):
    module = load_script("ops_check")
    cache_roots: list[Path] = []

    def fake_run(name, command, *, env=None, timeout=None):
        cache_roots.append(Path(env["PYTHONPYCACHEPREFIX"]))
        return module.CheckResult(name, True, 0.0, command, "")

    monkeypatch.setattr(module, "run_command", fake_run)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: module.run_compile_check(), range(10)))

    assert all(result.ok for result in results)
    assert len(set(cache_roots)) == 10
    assert all(not path.exists() for path in cache_roots)
