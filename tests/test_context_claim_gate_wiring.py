from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_lint():
    path = ROOT / "scripts" / "lint_context_claim_gate_wiring.py"
    spec = importlib.util.spec_from_file_location("lint_context_claim_gate_wiring", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_claim_gate_wiring_is_mandatory_in_every_runtime() -> None:
    report = _load_lint().lint(ROOT)
    assert report["ok"] is True


def test_claim_gate_wiring_fails_when_one_runtime_omits_it(tmp_path: Path) -> None:
    required = [
        tmp_path / "capability" / "manifest.yaml",
        tmp_path / "docs" / "operating-contract.md",
        tmp_path / "scripts" / "ops_check.py",
    ]
    required.extend(
        tmp_path / "dist" / "skills" / runtime / "SKILL.md"
        for runtime in ("codex", "claude-code", "grok", "antigravity")
    )
    for path in required:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("context_claim_gate.py mandatory_context_claim_gate context claim gate smoke", encoding="utf-8")
    (tmp_path / "dist" / "skills" / "grok" / "SKILL.md").write_text("missing", encoding="utf-8")

    report = _load_lint().lint(tmp_path)

    assert report["ok"] is False
    assert report["checks"]["runtime:grok"]["reason"] == "claim_gate_wiring_missing"
