#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_entry(
    *,
    route: str,
    status: str,
    elapsed_ms: float,
    parsed: int | None = None,
    gate_verdict: str | None = None,
    stage: str | None = None,
    source_ready: bool | None = None,
    note: str = "",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "schema": "discord_context_bridge_route_timing.v1",
        "observed_at": now_iso(),
        "route": route,
        "status": status,
        "elapsed_ms": round(float(elapsed_ms), 3),
        "text_output": "omitted",
        "outbound_actions": "disabled",
    }
    if parsed is not None:
        entry["parsed"] = int(parsed)
    if gate_verdict is not None:
        entry["gate_verdict"] = gate_verdict
    if stage is not None:
        entry["stage"] = stage
    if source_ready is not None:
        entry["source_ready"] = bool(source_ready)
    if note:
        entry["note"] = note
    return entry


def append_entry(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_route: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_route.setdefault(str(entry.get("route", "unknown")), []).append(entry)

    routes: dict[str, Any] = {}
    for route, route_entries in sorted(by_route.items()):
        elapsed = [float(item.get("elapsed_ms", 0)) for item in route_entries]
        passes = [item for item in route_entries if item.get("status") == "pass"]
        routes[route] = {
            "count": len(route_entries),
            "pass_count": len(passes),
            "last_status": route_entries[-1].get("status"),
            "last_elapsed_ms": route_entries[-1].get("elapsed_ms"),
            "min_elapsed_ms": round(min(elapsed), 3) if elapsed else None,
            "max_elapsed_ms": round(max(elapsed), 3) if elapsed else None,
            "avg_elapsed_ms": round(sum(elapsed) / len(elapsed), 3) if elapsed else None,
        }

    return {
        "schema": "discord_context_bridge_route_timing_summary.v1",
        "entry_count": len(entries),
        "routes": routes,
        "text_output": "omitted",
        "outbound_actions": "disabled",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discord route の速度比較用 JSONL を追記/要約する。")
    sub = parser.add_subparsers(dest="command", required=True)

    append = sub.add_parser("append", help="route timing entry を JSONL に追記する")
    append.add_argument("--log", type=Path, required=True)
    append.add_argument("--route", required=True)
    append.add_argument("--status", choices=["pass", "fail", "warn"], required=True)
    append.add_argument("--elapsed-ms", type=float, required=True)
    append.add_argument("--parsed", type=int)
    append.add_argument("--gate-verdict")
    append.add_argument("--stage")
    append.add_argument("--source-ready", choices=["true", "false"])
    append.add_argument("--note", default="")

    summary = sub.add_parser("summary", help="route timing JSONL を route 別に要約する")
    summary.add_argument("--log", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "append":
        entry = compact_entry(
            route=args.route,
            status=args.status,
            elapsed_ms=args.elapsed_ms,
            parsed=args.parsed,
            gate_verdict=args.gate_verdict,
            stage=args.stage,
            source_ready=None if args.source_ready is None else args.source_ready == "true",
            note=args.note,
        )
        append_entry(args.log, entry)
        print(json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print(json.dumps(summarize_entries(read_entries(args.log)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
