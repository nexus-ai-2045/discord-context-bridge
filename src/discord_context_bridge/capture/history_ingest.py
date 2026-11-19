"""Private historical cache intake using the existing capture ledger and store."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .orchestrator import _digest, capture_watermark_digest
from .service import _validate_run_ledger_binding, merge_persisted_capture_window, start_capture_loop
from .virtual_scroll import merge_capture_window, new_virtual_scroll_coverage
from .store import (
    CaptureCheckpointStore, CheckpointCorruptError, canonical_capture_digest,
    _open_store_directory_chain, _close_store_directory_chain,
)

MAX_BYTES = 32_000_000
_ID = re.compile(r"[0-9]{15,21}")


def _private_directory(path: Path) -> None:
    """Create private parents through the store's no-follow directory walker."""
    absolute = path.absolute()
    if '..' in absolute.parts:
        raise ValueError('historical destination path contains parent traversal')
    descriptors, _ = _open_store_directory_chain(
        Path(absolute.anchor), absolute.parts[1:], create=True,
    )
    try:
        if stat.S_IMODE(os.fstat(descriptors[-1]).st_mode) != 0o700:
            raise ValueError('historical destination directory must have mode 0700')
    finally:
        _close_store_directory_chain(descriptors)


def _target(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    parts = parsed.path.strip('/').split('/')
    if (parsed.scheme != 'https' or parsed.netloc != 'discord.com'
            or parsed.query or parsed.fragment or len(parts) != 3
            or parts[0] != 'channels' or not _ID.fullmatch(parts[1])
            or not _ID.fullmatch(parts[2])):
        raise ValueError('historical target must be an exact guild channel URL')
    return parts[1], parts[2]


def _batch(payload: Any, url: str, *, input_payload_sha256: str | None = None,
           input_file_sha256: str | None = None,
           source_indexes: list[int] | None = None) -> dict[str, Any]:
    guild, channel = _target(url)
    if not isinstance(payload, dict) or payload.get('schema') != 'dcb.chrome_http_cache_candidates.v1':
        raise ValueError('unsupported historical cache schema')
    records = payload.get('records')
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError('invalid historical record list')
    sources = payload.get('sources', [])
    if not isinstance(sources, list):
        raise ValueError('invalid historical source list')
    indexes = source_indexes if source_indexes is not None else list(range(len(sources)))
    if (len(indexes) != len(sources)
            or any(type(i) is not int or i < 0 for i in indexes)
            or len(set(indexes)) != len(indexes)):
        raise ValueError('invalid historical source mapping')
    source_map = dict(zip(indexes, sources))
    selected_indexes = set()
    selected = []
    for row in records:
        if not isinstance(row, dict):
            raise ValueError('invalid historical record')
        if row.get('target_url') != url:
            continue
        message = row.get('message')
        if (row.get('guild_id') != guild or not isinstance(message, dict)
                or message.get('channel_id') != channel
                or not isinstance(message.get('id'), str) or not _ID.fullmatch(message['id'])
                or not isinstance(message.get('content'), str)
                or not isinstance(message.get('timestamp'), str)
                or not isinstance(message.get('attachments'), list)):
            raise ValueError('historical message target or shape invalid')
        ids = [item.get('id') for item in message['attachments'] if isinstance(item, dict)]
        if len(ids) != len(message['attachments']) or any(not isinstance(x, str) or not _ID.fullmatch(x) for x in ids) or len(set(ids)) != len(ids):
            raise ValueError('historical attachment identifiers invalid')
        row_indexes = row.get('source_indexes', [])
        if (not isinstance(row_indexes, list)
                or any(type(i) is not int or i not in source_map for i in row_indexes)
                or len(set(row_indexes)) != len(row_indexes)
                or (('source_indexes' in row or sources) and not row_indexes)):
            raise ValueError('historical source indexes invalid')
        if row.get('selected_payload_sha256', canonical_capture_digest(message)) != canonical_capture_digest(message):
            raise ValueError('historical selected payload hash mismatch')
        for index in row_indexes:
            source = source_map[index]
            if (not isinstance(source, dict) or source.get('guild_id') != guild
                    or source.get('channel_id') != channel
                    or not isinstance(source.get('cache_file_sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', source['cache_file_sha256'])):
                raise ValueError('historical source target or hash invalid')
        selected_indexes.update(row_indexes)
        selected.append(row)
    if not selected:
        raise ValueError('no historical records for requested target')
    # Source variants remain separate observations, never a claim about latest.
    selected.sort(key=lambda r: (int(r['message']['id']), canonical_capture_digest(r)))
    payload_hash = input_payload_sha256 or canonical_capture_digest(payload)
    if (not isinstance(payload_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', payload_hash)
            or (input_file_sha256 is not None and (
                not isinstance(input_file_sha256, str)
                or not re.fullmatch(r'[0-9a-f]{64}', input_file_sha256)))):
        raise ValueError('historical input hash invalid')
    selected_indexes = sorted(selected_indexes)
    return {'schema': 'dcb.historical-cache-batch.v1', 'target_url': url,
            'freshness': 'unknown', 'current_snapshot': False, 'records': selected,
            'input_payload_sha256': payload_hash, 'input_file_sha256': input_file_sha256,
            'scope_selected_source_indexes': selected_indexes,
            'sources': [source_map[index] for index in selected_indexes]}


def _observation(batch: dict[str, Any], ref: str) -> dict[str, Any]:
    return {'window_id': canonical_capture_digest(batch), 'source': 'saved_cache',
            'direction': 'stationary',
            'messages': [{'message_id': r['message']['id'],
                          'content_hash': canonical_capture_digest(r['message']),
                          'content_ref': ref,
                          'attachment_ids': [a['id'] for a in r['message']['attachments']]}
                         for r in batch['records']]}


def _identity(store: CaptureCheckpointStore, url: str, attempt_id: str) -> str:
    return start_capture_loop(
        store, 'historical-cache:' + url, 'saved_artifacts', 'historical-time-unknown',
        attempt_id='history-' + attempt_id, tag_context={'scope': 'thread_only'},
    )['capture_id']


def ingest_history_cache(store: CaptureCheckpointStore, payload: Any, *, url: str,
                         attempt_id: str, apply: bool = False,
                         input_file_sha256: str | None = None) -> dict[str, Any]:
    batch = _batch(payload, url, input_file_sha256=input_file_sha256)
    if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,100}', attempt_id):
        raise ValueError('invalid historical attempt identifier')
    digest = canonical_capture_digest(batch)
    result = {'schema': 'dcb.historical-cache-intake.v1', 'dry_run': not apply,
              'records_selected': len(batch['records']), 'freshness': 'unknown',
              'current_snapshot_written': False, 'full_capture_confirmed': False,
              'raw_text_returned': False, 'outbound_actions': 'disabled'}
    if not apply:
        return result
    _private_directory(store.root)
    capture_id = _identity(store, url, attempt_id)
    ref = f'history-inputs/{capture_id}/{digest}.json'
    existing = store.read_managed_object_if_present(ref, max_bytes=MAX_BYTES)
    if existing is not None and json.loads(existing[0]) != batch:
        raise CheckpointCorruptError('historical body object differs')
    if existing is None:
        store.save_receipt(store.root / ref, batch)
    # The immutable body precedes ledger writes. Replaying the same input repairs
    # interruptions here and between the existing ledger and coverage writes.
    observation = _observation(batch, ref)
    coverage = store.load_coverage(capture_id)
    merged = merge_persisted_capture_window(store, capture_id, observation,
                                          expected_window_count=len((coverage or {}).get('windows', [])))
    verified = history_cache_status(store, capture_id)
    return {**result, **verified, 'capture_id': capture_id,
            'window_count': merged['coverage']['window_count']}


def history_cache_status(store: CaptureCheckpointStore, capture_id: str) -> dict[str, Any]:
    run = store.load_checkpoint(capture_id)
    if (run is None or run['route'] != 'saved_artifacts'
            or run.get('upper_watermark_digest') != capture_watermark_digest('historical-time-unknown')
            or not str((run.get('capture_identity') or {}).get('attempt_id', '')).startswith('history-')):
        raise ValueError('historical capture missing')
    ledger = store.load_message_ledger(capture_id)
    if ledger is None:
        raise ValueError('historical ledger pending; replay original input')
    _validate_run_ledger_binding(run, ledger)
    batches = {}
    unique = set()
    versions: dict[str, set[str]] = {}
    observed_rows = set()
    for event in ledger['events']:
        ref = event.get('content_ref', '')
        if not isinstance(ref, str) or not ref.startswith(f'history-inputs/{capture_id}/'):
            raise CheckpointCorruptError('historical content reference invalid')
        if ref not in batches:
            batch = json.loads(store.read_managed_object(ref, max_bytes=MAX_BYTES))
            if not isinstance(batch, dict):
                raise CheckpointCorruptError('historical body shape invalid')
            try:
                validated = _batch(
                    {'schema': 'dcb.chrome_http_cache_candidates.v1',
                     'records': batch.get('records'), 'sources': batch.get('sources')},
                    batch.get('target_url', ''),
                    input_payload_sha256=batch.get('input_payload_sha256'),
                    input_file_sha256=batch.get('input_file_sha256'),
                    source_indexes=batch.get('scope_selected_source_indexes'),
                )
            except (ValueError, TypeError, AttributeError) as error:
                raise CheckpointCorruptError('historical body shape invalid') from error
            digest = canonical_capture_digest(validated)
            if batch != validated or ref != f'history-inputs/{capture_id}/{digest}.json':
                raise CheckpointCorruptError('historical body hash mismatch')
            if _digest('target', 'historical-cache:' + batch['target_url']) != run['target_digest']:
                raise CheckpointCorruptError('historical run target mismatch')
            batches[ref] = batch
        matches = [r for r in batches[ref]['records'] if r['message']['id'] == event['message_id'] and canonical_capture_digest(r['message']) == event['content_hash']]
        if (not matches or event.get('source') != 'saved_cache'
                or event.get('window_id') != ref.rsplit('/', 1)[-1][:-5]
                or event.get('attachment_ids') != [a['id'] for a in matches[0]['message']['attachments']]):
            raise CheckpointCorruptError('historical ledger body mismatch')
        index = event.get('window_index')
        rows = batches[ref]['records']
        if (type(index) is not int or not 0 <= index < len(rows)
                or rows[index] not in matches or (ref, index) in observed_rows):
            raise CheckpointCorruptError('historical ledger observation mismatch')
        observed_rows.add((ref, index))
        unique.add(event['message_id'])
        versions.setdefault(event['message_id'], set()).add(event['content_hash'])
    expected_rows = {(ref, index) for ref, body in batches.items()
                     for index in range(len(body['records']))}
    expected_coverage = new_virtual_scroll_coverage(capture_id)
    for ref, body in batches.items():
        expected_coverage = merge_capture_window(expected_coverage, _observation(body, ref))
    if (observed_rows != expected_rows
            or store.load_coverage(capture_id) != expected_coverage):
        raise CheckpointCorruptError('historical ledger and coverage differ; replay original input')
    return {'schema': 'dcb.historical-cache-status.v1', 'capture_id': capture_id,
            'message_count': len(unique), 'version_count': sum(map(len, versions.values())),
            'observation_batches': len(batches), 'freshness': 'unknown',
            'latest_version_determined': False, 'full_capture_confirmed': False,
            'current_snapshot_written': False, 'raw_text_returned': False,
            'outbound_actions': 'disabled'}


def export_history_cache(store: CaptureCheckpointStore, capture_id: str, output: Path) -> dict[str, Any]:
    status = history_cache_status(store, capture_id)
    ledger = store.load_message_ledger(capture_id)
    refs = sorted({e['content_ref'] for e in ledger['events']})
    bodies = [json.loads(store.read_managed_object(ref, max_bytes=MAX_BYTES)) for ref in refs]
    # Reuse the store's secure no-link atomic writer, including private modes.
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('historical export destination already exists')
    _private_directory(output.parent)
    destination = CaptureCheckpointStore(Path(output.anchor))
    destination.save_receipt(output, {'schema': 'dcb.historical-cache-export.v1', 'status': status, 'batches': bodies})
    return {**status, 'private_export_saved': True}
