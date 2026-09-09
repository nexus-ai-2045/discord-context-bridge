from __future__ import annotations

import ctypes
import os
import re
import shlex
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import rest_backfill_config_safety

BOT_TOKEN_ENV = "DISCORD_" + "BOT_TOKEN"
TOKEN_COMMAND_ENV = "DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND"
CHANNEL_DIR_ENV = "DISCORD_CONTEXT_BRIDGE_CHANNEL_DIR"
DEFAULT_CHANNEL_ENV = Path.home() / ".claude" / "channels" / "discord" / ".env"
_MAX_CHANNEL_ENV_BYTES = 64 * 1024
_TOKEN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class BotTokenLoadResult:
    ok: bool
    provider: str
    token: str = ""
    failure_stage: str = ""
    exit_code: int | None = None

    def public_status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "discord_bot_token_provider.v1",
            "ok": self.ok,
            "provider": self.provider,
            "token_set": bool(self.token) if self.ok else False,
            "value_returned": False,
            "token_output": "omitted",
            "command_output": "omitted",
        }
        if self.failure_stage:
            payload["failure_stage"] = self.failure_stage
        if self.exit_code is not None:
            payload["exit_code"] = self.exit_code
        return payload


def _channel_env_path(
    source: Mapping[str, str], channel_env_path: Path | str | None
) -> Path:
    if channel_env_path is not None:
        return Path(channel_env_path)
    configured_dir = source.get(CHANNEL_DIR_ENV, "").strip()
    return Path(configured_dir) / ".env" if configured_dir else DEFAULT_CHANNEL_ENV


def _load_token_from_channel_env(path: Path) -> BotTokenLoadResult:
    """shell評価せず、通常fileの特定token keyだけを読む。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return BotTokenLoadResult(
            ok=False, provider="missing", failure_stage="bot_token_missing"
        )
    except OSError:
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_unreadable"
        )
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_not_regular_file"
        )
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) != 0o600:
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_mode_0600_required"
        )
    if metadata.st_size > _MAX_CHANNEL_ENV_BYTES:
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_too_large"
        )

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            current = os.fstat(handle.fileno())
            if not stat.S_ISREG(current.st_mode):
                return BotTokenLoadResult(
                    ok=False,
                    provider="channel_env",
                    failure_stage="channel_env_not_regular_file",
                )
            if os.name != "nt" and stat.S_IMODE(current.st_mode) != 0o600:
                return BotTokenLoadResult(
                    ok=False,
                    provider="channel_env",
                    failure_stage="channel_env_mode_0600_required",
                )
            text = handle.read(_MAX_CHANNEL_ENV_BYTES + 1)
    except (OSError, UnicodeError):
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_unreadable"
        )
    if len(text.encode("utf-8")) > _MAX_CHANNEL_ENV_BYTES:
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_too_large"
        )

    token_values: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key == BOT_TOKEN_ENV:
            candidate = value.strip()
            if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
                candidate = candidate[1:-1]
            token_values.append(candidate)
    if not token_values or not token_values[0]:
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="bot_token_missing"
        )
    if len(token_values) != 1:
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_duplicate_token_key"
        )
    token = token_values[0]
    if not _TOKEN_VALUE_PATTERN.fullmatch(token):
        return BotTokenLoadResult(
            ok=False, provider="channel_env", failure_stage="channel_env_token_value_invalid"
        )
    return BotTokenLoadResult(ok=True, provider="channel_env", token=token)


def split_secret_command(command: str) -> list[str]:
    if os.name == "nt":
        ctypes.windll.shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        ctypes.windll.shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
        argc = ctypes.c_int()
        argv = ctypes.windll.shell32.CommandLineToArgvW(command, ctypes.byref(argc))
        if not argv:
            raise ValueError("secret_command_parse_failed")
        try:
            return [argv[index] for index in range(argc.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(argv)
    return shlex.split(command, posix=True)


def configured_bot_token_provider(
    env: Mapping[str, str] | None = None,
    *,
    channel_env_path: Path | str | None = None,
) -> dict[str, Any]:
    source = env if env is not None else os.environ
    env_token_set = bool(source.get(BOT_TOKEN_ENV, "").strip())
    command_set = bool(source.get(TOKEN_COMMAND_ENV, "").strip())
    if env_token_set:
        channel_result = BotTokenLoadResult(ok=False, provider="not_checked")
        provider = "env"
        ok = True
    elif command_set:
        channel_result = BotTokenLoadResult(ok=False, provider="not_checked")
        provider = "secret_command"
        ok = True
    else:
        channel_result = _load_token_from_channel_env(
            _channel_env_path(source, channel_env_path)
        )
        provider = channel_result.provider
        ok = channel_result.ok
    return {
        "schema": "discord_bot_token_provider_status.v1",
        "ok": ok,
        "provider": provider,
        "env_token_set": env_token_set,
        "secret_command_set": command_set,
        "channel_env_token_set": channel_result.ok,
        "token_set": ok,
        "failure_stage": channel_result.failure_stage if not ok else "",
        "value_returned": False,
        "token_output": "omitted",
        "command_output": "omitted",
    }


def load_bot_token_from_provider(
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 10,
    channel_env_path: Path | str | None = None,
) -> BotTokenLoadResult:
    source = env if env is not None else os.environ
    env_token = source.get(BOT_TOKEN_ENV, "").strip()
    if env_token:
        return BotTokenLoadResult(ok=True, provider="env", token=env_token)

    command = source.get(TOKEN_COMMAND_ENV, "").strip()
    if not command:
        return _load_token_from_channel_env(_channel_env_path(source, channel_env_path))

    command_safety = rest_backfill_config_safety(command)
    if not command_safety["ok"]:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="unsafe_token_command_config")

    try:
        tokens = split_secret_command(command)
    except ValueError:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="token_command_parse_failed")
    if not tokens:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="token_command_empty")

    try:
        completed = subprocess.run(
            tokens,
            check=False,
            capture_output=True,
            text=True,
            env=dict(source),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="token_command_timeout")
    except FileNotFoundError:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="token_command_not_found")
    except OSError:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="token_command_start_failed")

    if completed.returncode != 0:
        return BotTokenLoadResult(
            ok=False,
            provider="secret_command",
            failure_stage="token_command_failed",
            exit_code=completed.returncode,
        )
    token = completed.stdout.strip()
    if not token:
        return BotTokenLoadResult(ok=False, provider="secret_command", failure_stage="token_command_empty_output")
    return BotTokenLoadResult(ok=True, provider="secret_command", token=token)
