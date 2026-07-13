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
    assert "no_public_core_direct_discord_send" in manifest["stoplines"]
    assert "no_playwright_default_for_discord_context" in manifest["stoplines"]
    assert "no_unapproved_visible_ui_automation" in manifest["stoplines"]
    assert "no_cross_route_webhook_or_bot_guessing" in manifest["stoplines"]


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
    assert "この public core は Discord への send / 自動返信 / reaction / delete / edit を直接実行しない" in body
    assert "auto-send-preflight" in body
    assert "Playwright / headless browser / 新規 browser profile を既定経路にしない" in body
    assert "no_unapproved_visible_ui_automation" in body
    assert "Computer Use 的な画面操作" in body


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
    stale_skill.write_text(stale_skill.read_text(encoding="utf-8").replace("no_public_core_direct_discord_send", "mutated"), encoding="utf-8")

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
    target.write_text(body.replace("no_public_core_direct_discord_send", "mutated_stopline"), encoding="utf-8")

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


def test_lint_ingest_route_policy_requires_playwright_default_ban(tmp_path):
    module = load_script_module("lint_ingest_route_policy_ok", ROOT / "scripts" / "lint_ingest_route_policy.py")
    generated = tmp_path / "dist" / "skills" / "codex"
    generated.mkdir(parents=True)
    required = (
        "Playwright 既定経路にしない cic Discord Desktop cache macOS Accessibility "
        "ai-party 外部 MCP 自動探索しない Computer Use 的な画面操作 SendKeys "
        "AppActivate スクショ取得 Chromeを勝手に開く ユーザーの明示許可なしに実行しない "
        "no_unapproved_visible_ui_automation"
    )
    contract = tmp_path / "operating-contract.md"
    contract.write_text(required, encoding="utf-8")
    (generated / "SKILL.md").write_text(required, encoding="utf-8")
    local_skill = tmp_path / "local" / "SKILL.md"
    local_skill.parent.mkdir()
    local_skill.write_text(required, encoding="utf-8")

    report = module.lint_ingest_route_policy(
        contract=contract,
        generated_skills_dir=tmp_path / "dist" / "skills",
        local_claude_skill=local_skill,
    )

    assert report["overall"] == "ok"
    assert report["checks"]["contract"]["status"] == "ok"
    assert report["checks"]["generated:codex"]["status"] == "ok"
    assert report["checks"]["local:claude-code"]["status"] == "ok"


def test_lint_ingest_route_policy_detects_missing_phrase(tmp_path):
    module = load_script_module("lint_ingest_route_policy_missing", ROOT / "scripts" / "lint_ingest_route_policy.py")
    generated = tmp_path / "dist" / "skills" / "codex"
    generated.mkdir(parents=True)
    contract = tmp_path / "operating-contract.md"
    contract.write_text("cic Discord Desktop cache macOS Accessibility", encoding="utf-8")
    (generated / "SKILL.md").write_text(
        "Playwright 既定経路にしない cic Discord Desktop cache macOS Accessibility",
        encoding="utf-8",
    )

    report = module.lint_ingest_route_policy(
        contract=contract,
        generated_skills_dir=tmp_path / "dist" / "skills",
        include_local_claude_skill=False,
    )

    assert report["overall"] == "error"
    assert report["checks"]["contract"]["reason"] == "required_phrase_missing"
