import importlib.util
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def copy_boundary_files(target_root: Path) -> None:
    files = [
        "src/discord_context_bridge/core.py",
        "scripts/pr_scope_guard.py",
        "scripts/verify_ssot_projection.py",
        "scripts/pr_readiness_preflight.py",
        "README.md",
        "PROCESS_BOUNDARY.md",
        "docs/discord-send-operation-runbook.md",
        ".github/pull_request_template.md",
    ]
    for relative in files:
        source = ROOT / relative
        destination = target_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def test_boundary_logic_check_passes_current_repo():
    module = load_script_module("boundary_logic_check", ROOT / "scripts" / "boundary_logic_check.py")

    report = module.build_report(ROOT)

    assert report["schema"] == "discord_context_bridge_boundary_logic_check.v1"
    assert report["overall"] == "ok"
    assert report["groups"]["discord_send_boundary"]["overall"] == "ok"
    assert report["groups"]["public_private_artifact_boundary"]["overall"] == "ok"
    assert report["groups"]["account_boundary"]["overall"] == "ok"


def test_boundary_logic_check_blocks_enabled_send_api(tmp_path):
    module = load_script_module("boundary_logic_check_send", ROOT / "scripts" / "boundary_logic_check.py")
    copy_boundary_files(tmp_path)
    core_path = tmp_path / "src" / "discord_context_bridge" / "core.py"
    core_text = core_path.read_text(encoding="utf-8")
    core_path.write_text(
        core_text.replace(
            'def send_message(*_: Any, **__: Any) -> None:\n    raise DisabledCapability("Discord への送信機能は、この public nucleus では意図的に無効です。")\n',
            'def send_message(*_: Any, **__: Any) -> None:\n    return None\n',
        ),
        encoding="utf-8",
    )

    report = module.build_report(tmp_path)

    assert report["overall"] == "error"
    assert report["groups"]["discord_send_boundary"]["checks"]["send_api_disabled"]["status"] == "error"


def test_boundary_logic_check_blocks_missing_private_artifact_gate(tmp_path):
    module = load_script_module("boundary_logic_check_private", ROOT / "scripts" / "boundary_logic_check.py")
    copy_boundary_files(tmp_path)
    pr_scope_path = tmp_path / "scripts" / "pr_scope_guard.py"
    pr_scope_text = pr_scope_path.read_text(encoding="utf-8")
    pr_scope_path.write_text(pr_scope_text.replace("SENSITIVE_PATH_PATTERNS", "RENAMED_PATTERNS"), encoding="utf-8")

    report = module.build_report(tmp_path)

    assert report["overall"] == "error"
    assert (
        report["groups"]["public_private_artifact_boundary"]["checks"]["pr_scope_guard_private_paths"]["status"]
        == "error"
    )
