from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CONFIG_ENV = "DISCORD_CONTEXT_BRIDGE_CONFIG"
SHARED_SNAPSHOT_ROOT_ENV = "DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT"


class LocalConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedLocalPath:
    path: Path
    source: str


def default_config_path(*, home: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    env_value = env if env is not None else os.environ
    configured = str(env_value.get(CONFIG_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser()
    return (home or Path.home()) / ".config" / "discord-context-bridge" / "config.json"


def load_local_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalConfigError("local_config_unreadable") from exc
    if not isinstance(payload, dict):
        raise LocalConfigError("local_config_object_required")
    return payload


def resolve_shared_snapshot_root(
    *,
    explicit: Path | None = None,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> ResolvedLocalPath:
    if explicit is not None:
        return ResolvedLocalPath(explicit.expanduser(), "cli")

    env_value = env if env is not None else os.environ
    configured_env = str(env_value.get(SHARED_SNAPSHOT_ROOT_ENV, "")).strip()
    if configured_env:
        return ResolvedLocalPath(Path(configured_env).expanduser(), "env")

    selected_config = config_path or default_config_path(home=home, env=env_value)
    payload = load_local_config(selected_config)
    configured_file = str(payload.get("shared_snapshot_root", "")).strip()
    if configured_file:
        return ResolvedLocalPath(Path(configured_file).expanduser(), "config")

    default_root = (home or Path.home()) / "Projects" / "Documents" / "discord" / "raw-snapshots"
    return ResolvedLocalPath(default_root, "os_default")


def configured_discord_user_data_dir(*, config_path: Path) -> Path | None:
    payload = load_local_config(config_path)
    value = str(payload.get("discord_desktop_user_data_dir", "")).strip()
    return Path(value).expanduser() if value else None


def build_configure_local_cache(
    *,
    config_path: Path,
    shared_snapshot_root: Path | None,
    discord_desktop_user_data_dir: Path | None,
    apply: bool,
) -> dict[str, Any]:
    existing = load_local_config(config_path)
    updated = dict(existing)
    if shared_snapshot_root is not None:
        updated["shared_snapshot_root"] = str(shared_snapshot_root.expanduser())
    if discord_desktop_user_data_dir is not None:
        updated["discord_desktop_user_data_dir"] = str(discord_desktop_user_data_dir.expanduser())
    changed = updated != existing
    if apply and changed:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix="config.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(config_path)
    return {
        "language": "ja",
        "schema": "discord_context_bridge_local_cache_config.v1",
        "ok": True,
        "dry_run": not apply,
        "changed": changed if apply else False,
        "would_change": changed,
        "shared_snapshot_root_configured": bool(updated.get("shared_snapshot_root")),
        "discord_desktop_user_data_dir_configured": bool(updated.get("discord_desktop_user_data_dir")),
        "config_path_output": "omitted",
        "configured_paths_output": "omitted",
        "outbound_actions": "disabled",
    }
