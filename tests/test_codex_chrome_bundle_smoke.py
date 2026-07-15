import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import codex_chrome_bundle_smoke


def test_smoke_detects_global_process_overwrite_regression(tmp_path: Path) -> None:
    bundle = tmp_path / "bad-browser-client.mjs"
    bundle.write_text(
        "globalThis.process = {}; export async function setupBrowserRuntime() {}\n",
        encoding="utf-8",
    )
    payload = codex_chrome_bundle_smoke.build_probe(bundle=bundle)
    assert payload["ok"] is False
    assert payload["state"] == "protected_process_conflict"
    assert payload["checks"]["normal_import"]["state"] == "pass"
    assert payload["guarantee"]["chrome_extension_checked"] is False


def test_smoke_accepts_lexical_process_shim_without_changing_host(tmp_path: Path) -> None:
    bundle = tmp_path / "good-browser-client.mjs"
    bundle.write_text(
        "const processShim = {}; const process = processShim; export async function setupBrowserRuntime() {}\n",
        encoding="utf-8",
    )
    payload = codex_chrome_bundle_smoke.build_probe(bundle=bundle)
    assert payload["ok"] is True
    assert payload["state"] == "ready_for_runtime_setup"
    assert payload["next_action"] == "run_real_node_repl_extension_e2e"


def test_smoke_reports_missing_bundle_without_path(tmp_path: Path) -> None:
    payload = codex_chrome_bundle_smoke.build_probe(bundle=tmp_path / "missing.mjs")
    assert payload["state"] == "bundle_missing"
    assert str(tmp_path) not in str(payload)


def test_resolve_latest_bundle_uses_highest_installed_version(tmp_path: Path) -> None:
    older = tmp_path / "26.707.71524" / "scripts" / "browser-client.mjs"
    latest = tmp_path / "26.707.72221" / "scripts" / "browser-client.mjs"
    for path in (older, latest):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export async function setupBrowserRuntime() {}\n", encoding="utf-8")

    assert codex_chrome_bundle_smoke.resolve_latest_bundle(chrome_cache_root=tmp_path) == latest
