from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import ModuleType

import pytest

from discord_context_bridge import cli


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
def test_script_process_runner_enforces_utf8_replace_and_child_environment(monkeypatch, script_name: str):
    module = load_script(script_name)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="日本語", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    if script_name == "ops_check":
        result = module.run_command("fixture", ["child"], env={"DCB_FIXTURE": "1"})
    else:
        result = module.run_command(["child"])

    if script_name == "ops_check":
        assert result.output == "日本語"
    else:
        assert result.stdout == "日本語"
    _, kwargs = calls[0]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    if script_name == "ops_check":
        assert kwargs["env"]["DCB_FIXTURE"] == "1"


def test_cli_source_command_enforces_utf8_replace_and_child_environment(monkeypatch):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="日本語", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.read_command_text("fixture-child") == "日本語"
    _, kwargs = calls[0]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"


def test_cli_source_command_decodes_real_japanese_utf8_child():
    tokens = [sys.executable, str(FIXTURE)]
    command = subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)

    assert cli.read_command_text(command) == "日本語の子プロセス出力\n"
