from __future__ import annotations

import ctypes
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping

from .core import rest_backfill_config_safety

BOT_TOKEN_ENV = "DISCORD_" + "BOT_TOKEN"
TOKEN_COMMAND_ENV = "DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND"


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


def configured_bot_token_provider(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env if env is not None else os.environ
    env_token_set = bool(source.get(BOT_TOKEN_ENV, "").strip())
    command_set = bool(source.get(TOKEN_COMMAND_ENV, "").strip())
    provider = "env" if env_token_set else "secret_command" if command_set else "missing"
    return {
        "schema": "discord_bot_token_provider_status.v1",
        "ok": env_token_set or command_set,
        "provider": provider,
        "env_token_set": env_token_set,
        "secret_command_set": command_set,
        "token_set": env_token_set or command_set,
        "value_returned": False,
        "token_output": "omitted",
        "command_output": "omitted",
    }


def load_bot_token_from_provider(
    *,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 10,
) -> BotTokenLoadResult:
    source = env if env is not None else os.environ
    env_token = source.get(BOT_TOKEN_ENV, "").strip()
    if env_token:
        return BotTokenLoadResult(ok=True, provider="env", token=env_token)

    command = source.get(TOKEN_COMMAND_ENV, "").strip()
    if not command:
        return BotTokenLoadResult(ok=False, provider="missing", failure_stage="bot_token_missing")

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
