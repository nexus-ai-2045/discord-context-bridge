#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import report_latest_smoke

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas"


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="report-latest の公開 schema contract を合成 snapshot で確認する。")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def type_matches(expected: Any, value: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for expected_type in expected_types:
        if expected_type == "object" and isinstance(value, dict):
            return True
        if expected_type == "array" and isinstance(value, list):
            return True
        if expected_type == "string" and isinstance(value, str):
            return True
        if expected_type == "boolean" and isinstance(value, bool):
            return True
        if expected_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if expected_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if expected_type == "null" and value is None:
            return True
    return False


def validate_schema_subset(schema: dict[str, Any], value: Any, *, path: str = "$") -> list[str]:
    issues: list[str] = []
    if "type" in schema and not type_matches(schema["type"], value):
        return [f"{path}: type expected {schema['type']!r}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        issues.append(f"{path}: const expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        issues.append(f"{path}: enum expected one of {schema['enum']!r}, got {value!r}")
    if isinstance(value, dict):
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                issues.append(f"{path}.{key}: missing required property")
        properties = schema.get("properties") or {}
        for key, child_schema in properties.items():
            if key in value:
                issues.extend(validate_schema_subset(child_schema, value[key], path=f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            for key in extra:
                issues.append(f"{path}.{key}: additional property not allowed")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            issues.extend(validate_schema_subset(schema["items"], item, path=f"{path}[{index}]"))
    return issues


def build_latest_payload() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    report = report_latest_smoke.build_report(include_payload=True)
    return report, report.get("payload") if isinstance(report.get("payload"), dict) else None


def build_report() -> dict[str, Any]:
    smoke_report, payload = build_latest_payload()
    if not payload:
        return {
            "schema": "discord_bridge_report_latest_schema_check.v1",
            "ok": False,
            "failure_stage": "report_latest_smoke",
            "smoke_report": smoke_report,
        }

    checks = [
        ("latest_snapshot_report", "discord_bridge_latest_snapshot_report.v1.schema.json", payload),
        (
            "stored_acquisition_context",
            "discord_bridge_acquisition_context.v1.schema.json",
            (payload.get("source_summary") or {}).get("stored_acquisition_context") or {},
        ),
        (
            "report_acquisition_context",
            "discord_bridge_report_acquisition_context.v1.schema.json",
            (payload.get("source_summary") or {}).get("report_acquisition_context")
            or payload.get("report_acquisition_context")
            or {},
        ),
        (
            "snapshot_body_evidence",
            "discord_bridge_snapshot_body_evidence.v1.schema.json",
            ((payload.get("latest_saved_or_visible_message") or {}).get("body_evidence") or {}),
        ),
    ]
    issues: list[str] = []
    for label, schema_name, value in checks:
        schema = load_schema(schema_name)
        for issue in validate_schema_subset(schema, value, path=f"$.{label}"):
            issues.append(issue)

    return {
        "schema": "discord_bridge_report_latest_schema_check.v1",
        "ok": not issues,
        "failure_stage": None if not issues else "schema_contract",
        "issue_count": len(issues),
        "issues": issues,
        "validated_schema_count": len(checks),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report()
    if args.json:
        print(_json(report))
    else:
        status = "成功" if report["ok"] else "失敗"
        print(f"report-latest schema check: {status}")
        if not report["ok"]:
            print(_json(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
