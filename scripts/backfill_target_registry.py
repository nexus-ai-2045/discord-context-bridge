#!/usr/bin/env python3
"""ADR-0162 Phase 1 W3: 既存 capture artifact から target registry を backfill する。

`.local/discord-context-bridge/` 配下 (`--store-root` で変更可) を走査し、
target_key <-> url / label の対応を集めて `target_registry` へ登録する。

収集元:
- `text-snapshots.ndjson` の各行 (`url` / `target_key` / `title`)
- `*.json` artifact のうち `source_url_hash` を持つもの (`dcb.capture_manifest.v1` 形)
  は先頭16桁を target_key とし、`source_url` / `url` が併記されていればそちらを優先する
- その他の `*.json` artifact (`raw/` / `captures/` / closeout 等) は
  `url` / `source_url` / `target_key` フィールドを再帰探索する

既定は dry-run。`--apply` を付けた時だけ台帳へ書き込む。stdout は
metadata-only とし、url の値そのものは出力しない。dry-run 時も
`resolve_target` で pre-registration state を読み、`would_register` /
`already_registered` を報告する (R4)。

再帰探索 (`_find_first`) は既知 schema 値を持つ JSON artifact からのみ
候補を集める。schema フィールドが無い / 未知の JSON は無関係な url を
拾わないようスキップする (M7)。symlink 経由のファイルは走査対象から
除外する (M6)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from discord_context_bridge.core import stable_text_hash  # noqa: E402
from discord_context_bridge.target_registry import (  # noqa: E402
    DEFAULT_TARGET_REGISTRY_STORE,
    register_target,
    resolve_target,
)

DEFAULT_STORE_ROOT = Path(".local/discord-context-bridge")
_RECURSIVE_SEARCH_KEYS = ("url", "source_url", "target_key")
# 再帰探索の対象にしてよい既知 schema 値 (M7)。schema フィールドを持たない、
# または未知の JSON からは url / target_key を拾わない。
_RECURSIVE_SEARCH_KNOWN_SCHEMAS = {
    "dcb.raw_capture.v1",
    "dcb.capture_manifest.v1",
    "dcb.visible_message_record.v1",
    "dcb.incremental_visible_message.v1",
}


def _iter_files_excluding_symlinks(root: Path, pattern: str) -> list[Path]:
    """symlink ファイル / symlink 経由の親ディレクトリ / root 外への resolve を除外して列挙する。

    `site_adapter_store.py` の `_safe_artifact_path` と同じ判定方針。
    """
    resolved_root = root.resolve()
    results: list[Path] = []
    for path in root.rglob(pattern):
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        relative = path.relative_to(root)
        current = root
        escaped = False
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                escaped = True
                break
        if escaped:
            continue
        try:
            if not path.resolve().is_relative_to(resolved_root):
                continue
        except OSError:
            continue
        results.append(path)
    return sorted(results)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="既存 capture artifact から target registry を backfill する。"
    )
    parser.add_argument("--store-root", type=Path, default=DEFAULT_STORE_ROOT)
    parser.add_argument(
        "--registry-store",
        type=Path,
        default=None,
        help="既定は <store-root>/targets.ndjson",
    )
    parser.add_argument("--apply", action="store_true", help="実際に台帳へ登録する (既定は dry-run)")
    parser.add_argument("--json", action="store_true", help="機械処理用に JSON で出力する")
    return parser.parse_args(argv)


def _load_ndjson(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            records.append(loaded)
    return records


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _find_first(payload: Any, keys: tuple[str, ...]) -> tuple[str, str] | None:
    """payload を再帰探索し、最初に見つかった (key, value) を返す。"""
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return key, value.strip()
        for value in payload.values():
            found = _find_first(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first(item, keys)
            if found:
                return found
    return None


def _classify_bare_target_key(value: str) -> tuple[str, str]:
    """url を伴わない target_key 候補の桁数から key_scheme を推定する。"""
    stripped = value.strip()
    if len(stripped) == 64:
        return stripped[:16], "source_url_hash_64"
    if len(stripped) == 24:
        return stripped, "content_hash_24"
    return stripped, "url_hash_16"


def collect_from_text_snapshots(store_root: Path) -> tuple[list[dict[str, Any]], int]:
    candidates: list[dict[str, Any]] = []
    skipped = 0
    for path in _iter_files_excluding_symlinks(store_root, "text-snapshots.ndjson"):
        for record in _load_ndjson(path):
            target_key = str(record.get("target_key") or "").strip()
            url = str(record.get("url") or "").strip()
            title = str(record.get("title") or "").strip()
            if not target_key:
                skipped += 1
                continue
            if url:
                candidates.append(
                    {
                        "target_key": target_key,
                        "key_scheme": "url_hash_16",
                        "url": url,
                        "channel_label": title or None,
                        "source_ref": path.name,
                    }
                )
            elif title:
                candidates.append(
                    {
                        "target_key": target_key,
                        "key_scheme": "title_fallback_16",
                        "url": None,
                        "channel_label": title,
                        "source_ref": path.name,
                    }
                )
            else:
                skipped += 1
    return candidates, skipped


def collect_from_json_artifacts(store_root: Path) -> tuple[list[dict[str, Any]], int]:
    candidates: list[dict[str, Any]] = []
    skipped = 0
    for path in _iter_files_excluding_symlinks(store_root, "*.json"):
        payload = _load_json(path)
        if payload is None:
            skipped += 1
            continue

        schema_value = payload.get("schema")
        known_schema = isinstance(schema_value, str) and schema_value in _RECURSIVE_SEARCH_KNOWN_SCHEMAS

        # source_url_hash は dcb.capture_manifest.v1 専用のフィールド。known-schema
        # ガードより先に信用すると、`*.json` を無差別に走査する都合上、無関係な
        # artifact (schema フィールド無し・未知 schema) が偶然同名フィールドを持つ
        # だけで誤登録されてしまう (codex review #3)。manifest schema であることを
        # 先に確認してから使う。
        source_url_hash = payload.get("source_url_hash")
        if (
            schema_value == "dcb.capture_manifest.v1"
            and isinstance(source_url_hash, str)
            and source_url_hash.strip()
        ):
            explicit_url = payload.get("source_url") or payload.get("url")
            label = payload.get("title_safe_label")
            label = label if isinstance(label, str) and label.strip() else None
            if isinstance(explicit_url, str) and explicit_url.strip():
                candidates.append(
                    {
                        "target_key": stable_text_hash(explicit_url.strip()),
                        "key_scheme": "url_hash_16",
                        "url": explicit_url.strip(),
                        "channel_label": label,
                        "source_ref": path.name,
                    }
                )
            else:
                candidates.append(
                    {
                        "target_key": source_url_hash.strip()[:16],
                        "key_scheme": "source_url_hash_64",
                        "url": None,
                        "channel_label": label,
                        "source_ref": path.name,
                    }
                )
            continue

        if not known_schema:
            # 既知 schema 値を持たない JSON からは再帰探索で url を拾わない
            # (M7)。無関係な artifact の url フィールドを誤登録する事故を防ぐ。
            skipped += 1
            continue

        found = _find_first(payload, _RECURSIVE_SEARCH_KEYS)
        if not found:
            skipped += 1
            continue
        key, value = found
        if key in ("url", "source_url"):
            candidates.append(
                {
                    "target_key": stable_text_hash(value),
                    "key_scheme": "url_hash_16",
                    "url": value,
                    "channel_label": None,
                    "source_ref": path.name,
                }
            )
        else:
            target_key, key_scheme = _classify_bare_target_key(value)
            candidates.append(
                {
                    "target_key": target_key,
                    "key_scheme": key_scheme,
                    "url": None,
                    "channel_label": None,
                    "source_ref": path.name,
                }
            )
    return candidates, skipped


def build_report(
    *,
    store_root: Path = DEFAULT_STORE_ROOT,
    registry_store: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    registry_path = registry_store or (store_root / "targets.ndjson")
    if not store_root.exists():
        return {
            "schema": "dcb_backfill_target_registry_report.v1",
            "ok": False,
            "dry_run": not apply,
            "reason": "store_root_missing",
            "candidates_total": 0,
            "registered": 0,
            "skipped_unresolvable": 0,
            "path_output": "omitted",
            "url_output": "omitted",
            "outbound_actions": "disabled",
        }

    snapshot_candidates, snapshot_skipped = collect_from_text_snapshots(store_root)
    artifact_candidates, artifact_skipped = collect_from_json_artifacts(store_root)
    all_candidates = snapshot_candidates + artifact_candidates
    skipped = snapshot_skipped + artifact_skipped

    # R4: apply の有無に関わらず、mutate 前の registry 状態を基準に
    # would_register / already_registered を数える。dry-run でも判断材料に
    # なるようにする (register_target() の重複判定と同じ簡易ルール)。
    would_register = 0
    already_registered = 0
    for candidate in all_candidates:
        existing = resolve_target(candidate["target_key"], registry_path)
        is_duplicate = bool(existing) and (candidate["url"] or None) == (existing.get("url") or None) and not (
            candidate["channel_label"] and candidate["channel_label"] != existing.get("channel_label")
        )
        if is_duplicate:
            already_registered += 1
        else:
            would_register += 1

    registered = 0
    if apply:
        for candidate in all_candidates:
            result = register_target(
                target_key=candidate["target_key"],
                key_scheme=candidate["key_scheme"],
                url=candidate["url"],
                channel_label=candidate["channel_label"],
                source="backfill",
                source_ref=candidate["source_ref"],
                path=registry_path,
            )
            if result.get("registered"):
                registered += 1

    return {
        "schema": "dcb_backfill_target_registry_report.v1",
        "ok": True,
        "dry_run": not apply,
        "candidates_total": len(all_candidates),
        "text_snapshot_candidates": len(snapshot_candidates),
        "json_artifact_candidates": len(artifact_candidates),
        "registered": registered,
        "would_register": would_register,
        "already_registered": already_registered,
        "skipped_unresolvable": skipped,
        "path_output": "omitted",
        "url_output": "omitted",
        "outbound_actions": "disabled",
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        store_root=args.store_root,
        registry_store=args.registry_store,
        apply=args.apply,
    )
    if args.json:
        print(_json(report))
    else:
        status = "成功" if report["ok"] else "失敗"
        mode = "dry-run" if report["dry_run"] else "apply"
        print(f"target registry backfill ({mode}): {status}")
        print(_json(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
