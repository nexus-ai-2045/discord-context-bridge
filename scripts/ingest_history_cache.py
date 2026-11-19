#!/usr/bin/env python3
"""保存済みキャッシュを未知時点の履歴として取込み・照会する。"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from discord_context_bridge.capture.history_ingest import (
    MAX_BYTES, export_history_cache, history_cache_status, ingest_history_cache,
)
from discord_context_bridge.capture.store import CaptureCheckpointStore, CaptureStoreError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['ingest', 'status', 'export'])
    parser.add_argument('--store-root', type=Path, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--attempt-id')
    parser.add_argument('--capture-id')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        store = CaptureCheckpointStore(args.store_root)
        if args.action == 'ingest':
            if not args.input or not args.url or not args.attempt_id:
                parser.error('ingest requires --input, --url, --attempt-id')
            source = args.input.absolute()
            reader = CaptureCheckpointStore(Path(source.anchor))
            source_bytes = reader.read_managed_object(
                source.relative_to(source.anchor).as_posix(), max_bytes=MAX_BYTES,
            )
            payload = json.loads(source_bytes)
            result = ingest_history_cache(store, payload, url=args.url, attempt_id=args.attempt_id,
                                          apply=args.apply, input_file_sha256=sha256(source_bytes).hexdigest())
        elif args.action == 'status':
            result = history_cache_status(store, args.capture_id or '')
        else:
            if not args.output:
                parser.error('export requires --output')
            result = export_history_cache(store, args.capture_id or '', args.output.absolute())
    except (ValueError, OSError, CaptureStoreError):
        print(json.dumps({'ok': False, 'reason': 'historical_intake_validation_or_storage_failed', 'raw_text_returned': False}))
        return 2
    print(json.dumps({'ok': True, **result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
