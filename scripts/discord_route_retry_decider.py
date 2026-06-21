#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import discord_bot_route_preflight
import discord_channel_event_probe
from route_timing_log import append_entry, compact_entry


CommandRunner = Callable[[str, float], dict[str, Any]]


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def run_local_command(command: str, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if hasattr(os, "killpg"):
                os.killpg(process.pid, signal.SIGKILL)
            elif platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process.kill()
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        return {
            "ok": False,
            "failure_stage": "timeout",
            "returncode": 124,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "stdout_chars": 0,
            "stderr_chars": len(stderr or ""),
            "text_output": "omitted",
        }

    stdout_chars = len(stdout or "")
    stderr_chars = len(stderr or "")
    if process.returncode != 0:
        failure_stage = "command_failed"
    elif stdout_chars == 0:
        failure_stage = "source_empty"
    else:
        failure_stage = None

    return {
        "ok": failure_stage is None,
        "failure_stage": failure_stage,
        "returncode": process.returncode,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "stdout_chars": stdout_chars,
        "stderr_chars": stderr_chars,
        "text_output": "omitted",
    }


def route_attempt_payload(route: str, index: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempt": index,
        "ok": bool(result.get("ok")),
        "failure_stage": result.get("failure_stage"),
        "returncode": result.get("returncode"),
        "elapsed_ms": round(float(result.get("elapsed_ms", 0)), 3),
        "stdout_chars": int(result.get("stdout_chars", 0)),
        "stderr_chars": int(result.get("stderr_chars", 0)),
        "text_output": "omitted",
        "route": route,
    }


def probe_command_route(
    *,
    route: str,
    command: str | None,
    attempts: int,
    timeout: float,
    interval: float,
    runner: CommandRunner = run_local_command,
    timing_log: Path | None = None,
) -> dict[str, Any]:
    if not command:
        return {
            "route": route,
            "ok": False,
            "configured": False,
            "attempted": 0,
            "attempts": [],
            "failure_stage": "not_configured",
            "reason": "local command が未設定です。",
            "text_output": "omitted",
        }

    observed_attempts: list[dict[str, Any]] = []
    last_stage: str | None = None
    for index in range(1, max(1, attempts) + 1):
        result = runner(command, timeout)
        attempt = route_attempt_payload(route, index, result)
        observed_attempts.append(attempt)
        status = "pass" if attempt["ok"] else "fail"
        last_stage = attempt.get("failure_stage")
        if timing_log:
            append_entry(
                timing_log,
                compact_entry(
                    route=route,
                    status=status,
                    elapsed_ms=attempt["elapsed_ms"],
                    stage=last_stage,
                    source_ready=attempt["ok"],
                ),
            )
        if attempt["ok"]:
            return {
                "route": route,
                "ok": True,
                "configured": True,
                "attempted": index,
                "attempts": observed_attempts,
                "failure_stage": None,
                "source_ready": True,
                "text_output": "omitted",
            }
        if index < attempts and interval > 0:
            time.sleep(interval)

    return {
        "route": route,
        "ok": False,
        "configured": True,
        "attempted": len(observed_attempts),
        "attempts": observed_attempts,
        "failure_stage": last_stage or "source_unavailable",
        "source_ready": False,
        "text_output": "omitted",
    }


def probe_inbox_route(channel_dir: Path, *, timing_log: Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    probe = discord_channel_event_probe.build_probe(channel_dir)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if timing_log:
        append_entry(
            timing_log,
            compact_entry(
                route="bot_text_event_inbox",
                status="pass" if probe["ok"] else "fail",
                elapsed_ms=elapsed_ms,
                stage=probe.get("failure_stage"),
                source_ready=probe["ok"],
            ),
        )
    return {
        "route": "bot_text_event_inbox",
        "ok": bool(probe["ok"]),
        "source_ready": bool(probe["source_ready"]),
        "preflight_ok": bool(probe["preflight_ok"]),
        "text_event_candidates": int(probe["text_event_candidates"]),
        "media_inbox_count": int(probe["media_inbox_count"]),
        "failure_stage": probe.get("failure_stage"),
        "elapsed_ms": round(elapsed_ms, 3),
        "text_output": "omitted",
        "file_names_output": "omitted",
    }


def fallback_message(routes: dict[str, Any]) -> str:
    route_states = []
    for name in ("gateway_live_event", "rest_backfill", "bot_text_event_inbox"):
        route = routes.get(name, {})
        stage = route.get("failure_stage") or "unavailable"
        route_states.append(f"{name}:{stage}")
    tried = " / ".join(route_states)
    return f"{tried} では Discord 本文を取得できませんでした。Chrome / visible fallback に切り替えますか？"


def build_decision(
    *,
    channel_dir: Path,
    gateway_command: str | None,
    rest_command: str | None,
    attempts: int = 5,
    timeout: float = 5.0,
    interval: float = 0.2,
    runner: CommandRunner = run_local_command,
    timing_log: Path | None = None,
) -> dict[str, Any]:
    route_attempts: dict[str, Any] = {}

    gateway = probe_command_route(
        route="gateway_live_event",
        command=gateway_command,
        attempts=attempts,
        timeout=timeout,
        interval=interval,
        runner=runner,
        timing_log=timing_log,
    )
    route_attempts["gateway_live_event"] = gateway
    if gateway["ok"]:
        selected_route = "gateway_live_event"
        decision = "use_api_route"
    else:
        rest = probe_command_route(
            route="rest_backfill",
            command=rest_command,
            attempts=attempts,
            timeout=timeout,
            interval=interval,
            runner=runner,
            timing_log=timing_log,
        )
        route_attempts["rest_backfill"] = rest
        if rest["ok"]:
            selected_route = "rest_backfill"
            decision = "use_api_route"
        else:
            inbox = probe_inbox_route(channel_dir, timing_log=timing_log)
            route_attempts["bot_text_event_inbox"] = inbox
            if inbox["ok"]:
                selected_route = "bot_private_ingest"
                decision = "use_bot_private_ingest"
            else:
                selected_route = None
                decision = "ask_browser_fallback"

    prompt = fallback_message(route_attempts) if decision == "ask_browser_fallback" else None
    return {
        "schema": "discord_route_retry_decider.v1",
        "ok": decision != "ask_browser_fallback",
        "decision": decision,
        "selected_route": selected_route,
        "route_priority": ["gateway_live_event", "rest_backfill", "bot_text_event_inbox", "chrome_visible_fallback"],
        "attempt_budget": {
            "api_route_attempts": max(1, attempts),
            "api_route_timeout_seconds": timeout,
            "retry_interval_seconds": interval,
        },
        "routes": route_attempts,
        "fallback_prompt": prompt,
        "popup": None
        if prompt is None
        else {
            "title": "Discord route fallback",
            "message": prompt,
            "requires_user_go": True,
        },
        "chrome_fallback": {
            "auto_open": False,
            "requires_user_go": decision == "ask_browser_fallback",
            "reason": "api_or_inbox_route_unavailable" if decision == "ask_browser_fallback" else None,
        },
        "text_output": "omitted",
        "outbound_actions": "disabled",
        "safety_boundary": {
            "raw_discord_text_output": "omitted",
            "token_output": "omitted",
            "snowflake_values_output": "omitted",
            "participant_names_output": "omitted",
            "browser_auto_fallback": "disabled",
            "send_capability": "disabled",
        },
    }


def notify_user(title: str, message: str) -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"attempted": False, "ok": False, "reason": "unsupported_platform"}
    try:
        script = (
            f"display notification {json.dumps(message, ensure_ascii=False)} "
            f"with title {json.dumps(title, ensure_ascii=False)}"
        )
        completed = subprocess.run(
            ["osascript", "-e", script],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"attempted": True, "ok": False, "reason": "notification_timeout"}
    if completed.returncode != 0:
        return {
            "attempted": True,
            "ok": False,
            "reason": "notification_failed",
            "returncode": completed.returncode,
            "stderr_chars": len(completed.stderr or ""),
            "text_output": "omitted",
        }
    return {"attempted": True, "ok": True, "reason": "sent", "returncode": 0}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord route 1/2 を retry し、失敗時は Chrome fallback を明示確認で止める。")
    parser.add_argument("--channel-dir", type=Path, default=discord_bot_route_preflight.DEFAULT_CHANNEL_DIR)
    parser.add_argument("--gateway-command", default=os.environ.get("DISCORD_CONTEXT_BRIDGE_GATEWAY_COMMAND"))
    parser.add_argument("--rest-command", default=os.environ.get("DISCORD_CONTEXT_BRIDGE_REST_COMMAND"))
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--timing-log", type=Path)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def print_human(payload: dict[str, Any]) -> None:
    print("Discord route retry decider")
    print(f"ok: {str(payload['ok']).lower()}")
    print(f"decision: {payload['decision']}")
    print(f"selected_route: {payload['selected_route']}")
    if payload.get("fallback_prompt"):
        print(f"fallback_prompt: {payload['fallback_prompt']}")
    print("chrome_auto_open: false")
    print("text_output: omitted")
    print("outbound_actions: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_decision(
        channel_dir=args.channel_dir,
        gateway_command=args.gateway_command,
        rest_command=args.rest_command,
        attempts=args.attempts,
        timeout=args.timeout,
        interval=args.interval,
        timing_log=args.timing_log,
    )
    if args.notify and payload.get("popup"):
        payload["notification"] = notify_user(payload["popup"]["title"], payload["popup"]["message"])
    if args.json:
        print(_json(payload))
    else:
        print_human(payload)
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
