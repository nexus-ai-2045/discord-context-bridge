#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


NORMAL_IMPORT = """
const target = process.argv[1];
const loaded = await import(target);
if (typeof loaded.setupBrowserRuntime !== "function") process.exit(3);
"""

PROTECTED_PROCESS_IMPORT = """
const target = process.argv[1];
Object.defineProperty(globalThis, "process", {
  value: process,
  writable: false,
  configurable: false,
});
const original = globalThis.process;
const loaded = await import(target);
if (typeof loaded.setupBrowserRuntime !== "function") process.exit(3);
if (globalThis.process !== original) process.exit(4);
"""


def _version_key(path: Path) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in path.parents[1].name.split("."))
    except ValueError:
        return ()


def resolve_latest_bundle(*, chrome_cache_root: Path | None = None) -> Path | None:
    root = chrome_cache_root or Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled" / "chrome"
    candidates = list(root.glob("*/scripts/browser-client.mjs")) if root.is_dir() else []
    return max(candidates, key=_version_key) if candidates else None


def _run_import(*, node: str, bundle: Path, protected: bool, timeout: float) -> tuple[str, int | None]:
    script = PROTECTED_PROCESS_IMPORT if protected else NORMAL_IMPORT
    try:
        completed = subprocess.run(
            [node, "--input-type=module", "-e", script, str(bundle)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return "node_missing", None
    except subprocess.TimeoutExpired:
        return "timeout", None
    if completed.returncode == 0:
        return "pass", 0
    combined = f"{completed.stdout}\n{completed.stderr}".lower()
    if "cannot redefine property: process" in combined or "read only property 'process'" in combined:
        return "protected_process_conflict", completed.returncode
    if completed.returncode == 3:
        return "setup_browser_runtime_export_missing", completed.returncode
    if completed.returncode == 4:
        return "ambient_process_identity_changed", completed.returncode
    return "import_error", completed.returncode


def build_probe(
    *,
    bundle: Path | None,
    node: str = "node",
    timeout: float = 5.0,
    bundle_source: str = "cli",
) -> dict[str, Any]:
    if bundle is None or not bundle.is_file():
        return {
            "schema": "codex_chrome_bundle_smoke.v1",
            "ok": False,
            "state": "bundle_missing",
            "bundle_source": bundle_source,
            "bundle_path_output": "omitted",
            "outbound_actions": "disabled",
        }
    normal_state, normal_code = _run_import(node=node, bundle=bundle, protected=False, timeout=timeout)
    protected_state = "not_run"
    protected_code: int | None = None
    if normal_state == "pass":
        protected_state, protected_code = _run_import(
            node=node,
            bundle=bundle,
            protected=True,
            timeout=timeout,
        )
    ok = normal_state == "pass" and protected_state == "pass"
    if ok:
        state = "ready_for_runtime_setup"
        next_action = "run_real_node_repl_extension_e2e"
    elif normal_state == "pass" and protected_state == "protected_process_conflict":
        state = "protected_process_conflict"
        next_action = "fix_bundle_process_shim_before_e2e"
    else:
        state = normal_state
        next_action = "repair_bundle_or_node_runtime"
    return {
        "schema": "codex_chrome_bundle_smoke.v1",
        "ok": ok,
        "state": state,
        "bundle_source": bundle_source,
        "checks": {
            "normal_import": {"state": normal_state, "exit_code": normal_code},
            "protected_process_import": {"state": protected_state, "exit_code": protected_code},
        },
        "next_action": next_action,
        "guarantee": {
            "bundle_import_checked": True,
            "node_repl_checked": False,
            "chrome_extension_checked": False,
            "discord_e2e_checked": False,
        },
        "bundle_path_output": "omitted",
        "raw_error_output": "omitted",
        "outbound_actions": "disabled",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex Chrome bundle の process shim 回帰をmetadata-onlyで検査する。")
    parser.add_argument("--bundle", type=Path, help="省略時はinstalled Chrome plugin cacheの最新版を自動検出する")
    parser.add_argument("--node", default="node")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    bundle = args.bundle or resolve_latest_bundle()
    payload = build_probe(
        bundle=bundle,
        node=args.node,
        timeout=args.timeout,
        bundle_source="cli" if args.bundle else "auto_detected_latest",
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"state: {payload['state']}")
        print(f"ok: {str(payload['ok']).lower()}")
        print(f"next_action: {payload.get('next_action', 'none')}")
        print("bundle_path_output: omitted")
        print("outbound_actions: disabled")
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
