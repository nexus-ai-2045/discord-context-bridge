from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_audit():
    path = ROOT / "scripts" / "verify_context_claim_gate_head.py"
    spec = importlib.util.spec_from_file_location("verify_context_claim_gate_head", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_tree_passes_trusted_static_audit() -> None:
    report = _load_audit().verify(ROOT)
    assert report["status"] == "pass"
    assert report["candidate_executed"] is False


def test_gate_removal_is_blocked_without_executing_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    for relative in (
        "src/discord_context_bridge/claims",
        "scripts",
        "capability",
        "docs",
        "dist/skills",
        ".github/workflows",
    ):
        source = ROOT / relative
        if source.is_dir():
            shutil.copytree(source, candidate / relative, dirs_exist_ok=True)
        else:
            (candidate / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, candidate / relative)
    (candidate / "src" / "discord_context_bridge" / "claims" / "capture_claim.py").unlink()

    report = _load_audit().verify(candidate)

    assert report["status"] == "blocked"
    assert report["checks"]["capture"] is False
    assert report["candidate_executed"] is False
