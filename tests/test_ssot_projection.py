import importlib.util
import sys
from pathlib import Path

import pytest


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
    ssot_commit = export_module.git_commit()
    report = export_module.export_runtime_skills(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=output_dir,
        ssot_commit=ssot_commit,
    )

    codex_skill = output_dir / "codex" / "SKILL.md"
    body = codex_skill.read_text(encoding="utf-8")
    assert report["changed"] is True
    assert report["generated_count"] == 4
    assert "ssot_repo: nexus-ai-2045/discord-context-bridge" in body
    assert f"ssot_commit: {ssot_commit}" in body
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
    ssot_commit = export_module.git_commit()
    export_module.export_runtime_skills(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=output_dir,
        ssot_commit=ssot_commit,
    )
    stale_skill = output_dir / "grok" / "SKILL.md"
    stale_skill.write_text(stale_skill.read_text(encoding="utf-8").replace("no_public_core_direct_discord_send", "mutated"), encoding="utf-8")

    report = verify_module.verify_projection(
        manifest_path=ROOT / "capability" / "manifest.yaml",
        contract_path=ROOT / "docs" / "operating-contract.md",
        output_dir=output_dir,
        ssot_commit=ssot_commit,
    )

    assert report["overall"] == "error"
    assert report["checks"]["generated_files"]["status"] == "error"
    assert "grok/SKILL.md" in report["checks"]["generated_files"]["detail"]


def test_verify_projection_detects_non_reproducible_generated_at(tmp_path):
    verify_module = load_script_module("verify_ssot_projection_generated_at", ROOT / "scripts" / "verify_ssot_projection.py")

    output_dir = tmp_path / "dist" / "skills"
    for source in (ROOT / "dist" / "skills").glob("*/SKILL.md"):
        target = output_dir / source.parent.name / source.name
        target.parent.mkdir(parents=True)
        body = source.read_text(encoding="utf-8")
        generated_at = next(line for line in body.splitlines() if line.startswith("generated_at: "))
        target.write_text(
            body.replace(generated_at, "generated_at: 2099-01-01T00:00:00+00:00"),
            encoding="utf-8",
        )

    report = verify_module.verify_projection(output_dir=output_dir)

    assert report["overall"] == "error"
    assert report["checks"]["provenance"]["status"] == "error"


def test_verify_projection_reports_unresolvable_ssot_commit(tmp_path):
    verify_module = load_script_module("verify_ssot_projection_bad_commit", ROOT / "scripts" / "verify_ssot_projection.py")

    report = verify_module.verify_projection(
        output_dir=tmp_path / "dist" / "skills",
        ssot_commit="not-a-real-commit",
    )

    assert report["overall"] == "error"
    assert report["checks"]["provenance"]["status"] == "error"
    assert report["checks"]["generated_files"]["detail"] == "ssot_commit:unresolvable"


def test_sync_runtime_skills_is_read_only_until_apply(tmp_path):
    module = load_script_module("sync_runtime_skills_tmp", ROOT / "scripts" / "sync_runtime_skills.py")
    target = tmp_path / "claude-code" / "discord-context-bridge" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("stale", encoding="utf-8")

    checked = module.sync_runtime_skills(targets={"claude-code": target})
    assert checked["overall"] == "error"
    assert checked["applied"] is False
    assert target.read_text(encoding="utf-8") == "stale"

    applied = module.sync_runtime_skills(targets={"claude-code": target}, apply=True)
    assert applied["overall"] == "ok"
    assert applied["applied"] is True
    assert target.read_text(encoding="utf-8") == (
        ROOT / "dist" / "skills" / "claude-code" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_sync_runtime_skills_rejects_unsafe_apply_target(tmp_path):
    module = load_script_module("sync_runtime_skills_unsafe", ROOT / "scripts" / "sync_runtime_skills.py")
    target = tmp_path / "unrelated.md"
    target.write_text("keep", encoding="utf-8")

    report = module.sync_runtime_skills(targets={"claude-code": target}, apply=True)

    assert report["overall"] == "error"
    assert report["reason"] == "unsafe_target"
    assert target.read_text(encoding="utf-8") == "keep"


def test_sync_runtime_skills_rejects_all_targets_before_mixed_apply(tmp_path):
    module = load_script_module("sync_runtime_skills_mixed", ROOT / "scripts" / "sync_runtime_skills.py")
    valid = tmp_path / "valid" / "discord-context-bridge" / "SKILL.md"
    unknown = tmp_path / "unknown" / "discord-context-bridge" / "SKILL.md"
    valid.parent.mkdir(parents=True)
    unknown.parent.mkdir(parents=True)
    valid.write_text("valid-stale", encoding="utf-8")
    unknown.write_text("unknown-keep", encoding="utf-8")

    report = module.sync_runtime_skills(
        targets={"claude-code": valid, "unknown": unknown},
        apply=True,
    )

    assert report["overall"] == "error"
    assert report["applied"] is False
    assert valid.read_text(encoding="utf-8") == "valid-stale"
    assert unknown.read_text(encoding="utf-8") == "unknown-keep"


def test_sync_runtime_skills_rejects_absolute_runtime_source(tmp_path):
    module = load_script_module("sync_runtime_skills_absolute_runtime", ROOT / "scripts" / "sync_runtime_skills.py")
    source = tmp_path / "attacker-source"
    source.mkdir()
    (source / "SKILL.md").write_text("attacker-controlled", encoding="utf-8")
    target = tmp_path / "target" / "discord-context-bridge" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("keep", encoding="utf-8")

    report = module.sync_runtime_skills(
        targets={str(source.resolve()): target},
        apply=True,
    )

    assert report["overall"] == "error"
    assert report["applied"] is False
    assert target.read_text(encoding="utf-8") == "keep"


def test_sync_runtime_skills_rejects_symlink_target(tmp_path):
    module = load_script_module("sync_runtime_skills_symlink", ROOT / "scripts" / "sync_runtime_skills.py")
    shared = tmp_path / "shared" / "SKILL.md"
    shared.parent.mkdir()
    shared.write_text("shared-stale", encoding="utf-8")
    target = tmp_path / "runtime" / "discord-context-bridge" / "SKILL.md"
    target.parent.mkdir(parents=True)
    try:
        target.symlink_to(shared)
    except OSError as exc:
        pytest.skip(f"symlink is unavailable: {exc}")

    report = module.sync_runtime_skills(targets={"claude-code": target}, apply=True)

    assert report["overall"] == "error"
    assert report["applied"] is False
    assert target.is_symlink()
    assert shared.read_text(encoding="utf-8") == "shared-stale"


def test_sync_runtime_skills_rejects_symlink_ancestor(tmp_path):
    module = load_script_module("sync_runtime_skills_symlink_ancestor", ROOT / "scripts" / "sync_runtime_skills.py")
    victim = tmp_path / "victim"
    target_in_victim = victim / "discord-context-bridge" / "SKILL.md"
    target_in_victim.parent.mkdir(parents=True)
    target_in_victim.write_text("victim-keep", encoding="utf-8")
    linked_root = tmp_path / "runtime-root"
    try:
        linked_root.symlink_to(victim, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink is unavailable: {exc}")
    target = linked_root / "discord-context-bridge" / "SKILL.md"

    report = module.sync_runtime_skills(targets={"claude-code": target}, apply=True)

    assert report["overall"] == "error"
    assert report["applied"] is False
    assert target_in_victim.read_text(encoding="utf-8") == "victim-keep"


def test_sync_runtime_skills_apply_requires_single_target(tmp_path):
    module = load_script_module("sync_runtime_skills_single_apply", ROOT / "scripts" / "sync_runtime_skills.py")
    claude = tmp_path / "claude" / "discord-context-bridge" / "SKILL.md"
    codex = tmp_path / "codex" / "discord-context-bridge" / "SKILL.md"
    claude.parent.mkdir(parents=True)
    codex.parent.mkdir(parents=True)
    claude.write_text("claude-stale", encoding="utf-8")
    codex.write_text("codex-stale", encoding="utf-8")

    report = module.sync_runtime_skills(
        targets={"claude-code": claude, "codex": codex},
        apply=True,
    )

    assert report["overall"] == "error"
    assert report["reason"] == "apply_requires_single_target"
    assert report["applied"] is False
    assert claude.read_text(encoding="utf-8") == "claude-stale"
    assert codex.read_text(encoding="utf-8") == "codex-stale"


def test_sync_runtime_skills_reports_single_target_io_failure(tmp_path):
    module = load_script_module("sync_runtime_skills_io_failure", ROOT / "scripts" / "sync_runtime_skills.py")
    blocker = tmp_path / "blocker"
    blocker.write_text("not-a-directory", encoding="utf-8")
    target = blocker / "discord-context-bridge" / "SKILL.md"

    report = module.sync_runtime_skills(targets={"claude-code": target}, apply=True)

    assert report["overall"] == "error"
    assert report["reason"] == "apply_failed"
    assert report["error_type"] == "NotADirectoryError"
    assert report["applied"] is False
    assert blocker.read_text(encoding="utf-8") == "not-a-directory"


@pytest.mark.parametrize("apply", [False, True])
def test_sync_runtime_skills_rejects_directory_target(tmp_path, apply):
    module = load_script_module(f"sync_runtime_skills_directory_{apply}", ROOT / "scripts" / "sync_runtime_skills.py")
    target = tmp_path / "runtime" / "discord-context-bridge" / "SKILL.md"
    target.mkdir(parents=True)

    report = module.sync_runtime_skills(targets={"claude-code": target}, apply=apply)

    assert report["overall"] == "error"
    assert report["reason"] == "unsafe_target"
    assert report["applied"] is False
    assert target.is_dir()


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
        "no_unapproved_visible_ui_automation no_ocr_for_dcb_text_intake "
        "no_clipboard_without_explicit_clipboard_request "
        "raw本文は private artifact / local store に保存する visible output には raw本文を貼らず"
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
