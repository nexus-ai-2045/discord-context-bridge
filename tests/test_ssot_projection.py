import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_loads_runtime_targets_and_stoplines():
    module = load_script_module("export_runtime_skills_manifest", ROOT / "scripts" / "export_runtime_skills.py")

    manifest = module.load_manifest(ROOT / "capability" / "manifest.yaml")

    assert manifest["schema"] == "discord_context_bridge_capability_manifest.v1"
    assert manifest["ssot_repo"] == "nexus-ai-2045/discord-context-bridge"
    assert {"codex", "claude-code", "grok", "antigravity"} <= set(manifest["runtime_targets"])
    assert "no_discord_send" in manifest["stoplines"]


def test_export_runtime_skills_writes_provenance_and_contract(tmp_path):
    export_module = load_script_module("export_runtime_skills_tmp", ROOT / "scripts" / "export_runtime_skills.py")

    output_dir = tmp_path / "dist" / "skills"
    report = export_module.export_runtime_skills(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=output_dir,
        ssot_commit="testcommit123",
    )

    codex_skill = output_dir / "codex" / "SKILL.md"
    body = codex_skill.read_text(encoding="utf-8")
    assert report["changed"] is True
    assert report["generated_count"] == 4
    assert "ssot_repo: nexus-ai-2045/discord-context-bridge" in body
    assert "ssot_commit: testcommit123" in body
    assert "manifest_checksum:" in body
    assert "Discord への send / 自動返信 / reaction / delete / edit はしない" in body


def test_verify_projection_detects_stale_generated_skill(tmp_path):
    export_module = load_script_module("export_runtime_skills_verify_fixture", ROOT / "scripts" / "export_runtime_skills.py")
    verify_module = load_script_module("verify_ssot_projection_tmp", ROOT / "scripts" / "verify_ssot_projection.py")

    output_dir = tmp_path / "dist" / "skills"
    export_module.export_runtime_skills(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=output_dir,
        ssot_commit="testcommit123",
    )
    stale_skill = output_dir / "grok" / "SKILL.md"
    stale_skill.write_text(stale_skill.read_text(encoding="utf-8").replace("no_discord_send", "mutated"), encoding="utf-8")

    report = verify_module.verify_projection(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=output_dir,
        ssot_commit="testcommit123",
    )

    assert report["overall"] == "error"
    assert report["checks"]["generated_files"]["status"] == "error"
    assert "grok/SKILL.md" in report["checks"]["generated_files"]["detail"]


def test_checked_in_projection_is_current():
    verify_module = load_script_module("verify_ssot_projection_current", ROOT / "scripts" / "verify_ssot_projection.py")

    report = verify_module.verify_projection(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=ROOT / "dist" / "skills",
    )

    assert report["overall"] == "ok"


def test_lint_runtime_skill_sync_accepts_matching_runtime_skill(tmp_path):
    module = load_script_module("lint_runtime_skill_sync_ok", ROOT / "scripts" / "lint_runtime_skill_sync.py")

    target = tmp_path / "codex" / "SKILL.md"
    target.parent.mkdir()
    target.write_text((ROOT / "dist" / "skills" / "codex" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")

    report = module.lint_runtime_skill_sync(targets={"codex": target})

    assert report["overall"] == "ok"
    assert report["checks"]["codex"]["status"] == "ok"
    assert report["checks"]["codex"]["read_only"] is True


def test_lint_runtime_skill_sync_detects_drift(tmp_path):
    module = load_script_module("lint_runtime_skill_sync_drift", ROOT / "scripts" / "lint_runtime_skill_sync.py")

    target = tmp_path / "claude-code" / "SKILL.md"
    target.parent.mkdir()
    body = (ROOT / "dist" / "skills" / "claude-code" / "SKILL.md").read_text(encoding="utf-8")
    target.write_text(body.replace("no_discord_send", "mutated_stopline"), encoding="utf-8")

    report = module.lint_runtime_skill_sync(targets={"claude-code": target})

    assert report["overall"] == "error"
    assert report["checks"]["claude-code"]["status"] == "error"
    assert report["checks"]["claude-code"]["reason"] == "content_mismatch"


def test_lint_runtime_skill_sync_can_allow_missing_optional_target(tmp_path):
    module = load_script_module("lint_runtime_skill_sync_missing", ROOT / "scripts" / "lint_runtime_skill_sync.py")

    report = module.lint_runtime_skill_sync(targets={"grok": tmp_path / "missing" / "SKILL.md"}, allow_missing=True)

    assert report["overall"] == "ok"
    assert report["checks"]["grok"]["status"] == "warning"
    assert report["checks"]["grok"]["reason"] == "target_missing"
