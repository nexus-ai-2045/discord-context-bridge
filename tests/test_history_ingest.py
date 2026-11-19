from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import pytest

from discord_context_bridge.capture.history_ingest import ingest_history_cache, history_cache_status, export_history_cache
from discord_context_bridge.capture.store import (
    CaptureCheckpointStore, CaptureStoreError, canonical_capture_digest,
)

URL = 'https://discord.com/channels/111111111111111111/222222222222222222'


def payload(body='original'):
    return {'schema': 'dcb.chrome_http_cache_candidates.v1', 'records': [
        {'target_url': URL, 'guild_id': '111111111111111111', 'message': {
            'channel_id': '222222222222222222', 'id': '333333333333333333',
            'content': body, 'timestamp': '2026-01-01T00:00:00+00:00', 'attachments': []}}]}


def test_dry_run_and_same_batch_resume_and_edit_export(tmp_path):
    store = CaptureCheckpointStore(tmp_path/'store')
    assert ingest_history_cache(store,payload(),url=URL,attempt_id='test')['dry_run']
    assert not store.root.exists()
    first = ingest_history_cache(store,payload(),url=URL,attempt_id='test',apply=True)
    capture_id = first['capture_id']
    assert first['message_count'] == 1
    assert ingest_history_cache(CaptureCheckpointStore(store.root),payload(),url=URL,attempt_id='test',apply=True)['window_count'] == 1
    second = ingest_history_cache(store,payload('edited'),url=URL,attempt_id='test',apply=True)
    assert second['capture_id'] == capture_id
    assert second['version_count'] == 2
    assert second['message_count'] == 1
    assert not second['latest_version_determined']
    assert not second['full_capture_confirmed']
    assert store.load_checkpoint(capture_id)['route'] == 'saved_artifacts'
    destination=tmp_path/'private'/'history.json'
    assert export_history_cache(store,capture_id,destination)['private_export_saved']
    exported=json.loads(destination.read_text())
    assert {b['records'][0]['message']['content'] for b in exported['batches']} == {'original','edited'}
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('point',['merge','coverage'])
def test_resume_after_body_or_ledger_save(tmp_path,monkeypatch,point):
    import discord_context_bridge.capture.history_ingest as module
    store=CaptureCheckpointStore(tmp_path/'store')
    if point == 'merge':
        owner=module;name='merge_persisted_capture_window'
    else:
        owner=store;name='save_coverage'
    original=getattr(owner,name)
    def interrupt(*a,**k):
        raise OSError('simulated stop')
    monkeypatch.setattr(owner,name,interrupt)
    with pytest.raises(OSError):
        ingest_history_cache(store,payload(),url=URL,attempt_id='resume',apply=True)
    if point == 'coverage':
        capture_id = next((store.root / 'checkpoints').glob('*.json')).stem
        with pytest.raises(CaptureStoreError, match='coverage'):
            history_cache_status(store, capture_id)
    monkeypatch.setattr(owner,name,original)
    result=ingest_history_cache(store,payload(),url=URL,attempt_id='resume',apply=True)
    assert result['message_count']==1 and result['window_count']==1
    assert len(store.load_message_ledger(result['capture_id'])['events'])==1


def test_wrong_binding_rejected_before_writes(tmp_path):
    store=CaptureCheckpointStore(tmp_path/'store')
    bad=payload();bad['records'][0]['message']['channel_id']='444444444444444444'
    with pytest.raises(ValueError):
        ingest_history_cache(store,bad,url=URL,attempt_id='x',apply=True)
    assert not store.root.exists()


def test_symlink_store_and_export_rejected(tmp_path):
    real=tmp_path/'real';real.mkdir();link=tmp_path/'link';link.symlink_to(real, target_is_directory=True)
    with pytest.raises(Exception):
        ingest_history_cache(CaptureCheckpointStore(link),payload(),url=URL,attempt_id='x',apply=True)
    assert not list(real.iterdir())


@pytest.mark.parametrize('kind', ['store', 'export'])
def test_existing_public_directory_is_rejected_without_chmod(tmp_path, kind):
    public = tmp_path / 'existing'
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    if kind == 'store':
        with pytest.raises(ValueError, match='0700'):
            ingest_history_cache(CaptureCheckpointStore(public), payload(),
                                 url=URL, attempt_id='permissions', apply=True)
    else:
        store = CaptureCheckpointStore(tmp_path / 'store')
        result = ingest_history_cache(store, payload(), url=URL,
                                      attempt_id='permissions', apply=True)
        with pytest.raises(ValueError, match='0700'):
            export_history_cache(store, result['capture_id'], public / 'export.json')
    assert public.stat().st_mode & 0o777 == 0o755
    assert not list(public.iterdir())


@pytest.mark.parametrize('kind', ['parent', 'ancestor', 'destination'])
def test_export_rejects_links_without_writing_target(tmp_path, kind):
    store = CaptureCheckpointStore(tmp_path / 'store')
    result = ingest_history_cache(store, payload(), url=URL,
                                  attempt_id='links', apply=True)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    link = tmp_path / 'linked'
    if kind == 'destination':
        output = private / 'export.json'
        output.symlink_to(tmp_path / 'missing.json')
    else:
        link.symlink_to(private, target_is_directory=True)
        output = link / ('nested/export.json' if kind == 'ancestor' else 'export.json')
    with pytest.raises((ValueError, CaptureStoreError)):
        export_history_cache(store, result['capture_id'], output)
    assert not (private / 'export.json').is_file()
    assert not (private / 'nested').exists()
    assert not (tmp_path / 'missing.json').exists()


def test_new_nested_directories_and_body_are_private(tmp_path):
    store = CaptureCheckpointStore(tmp_path / 'new' / 'store')
    result = ingest_history_cache(store, payload(), url=URL,
                                  attempt_id='private', apply=True)
    output = tmp_path / 'exports' / 'nested' / 'history.json'
    export_history_cache(store, result['capture_id'], output)
    for directory in [store.root, store.root.parent, output.parent, output.parent.parent]:
        assert directory.stat().st_mode & 0o777 == 0o700
    for body in (store.root / 'history-inputs').rglob('*.json'):
        assert body.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('field', ['target_key', 'upper_watermark'])
def test_status_and_export_reject_misbound_ledger(tmp_path, field):
    store = CaptureCheckpointStore(tmp_path / 'store')
    result = ingest_history_cache(store, payload(), url=URL,
                                  attempt_id='binding', apply=True)
    capture_id = result['capture_id']
    ledger = store.load_message_ledger(capture_id)
    ledger[field] = 'wrong-run'
    store.save_receipt(store.message_ledger_path(capture_id), ledger)
    with pytest.raises(CaptureStoreError):
        history_cache_status(store, capture_id)
    output = tmp_path / 'export' / 'history.json'
    with pytest.raises(CaptureStoreError):
        export_history_cache(store, capture_id, output)
    assert not output.parent.exists()


def test_dry_run_rejects_invalid_attempt_without_writes(tmp_path):
    store = CaptureCheckpointStore(tmp_path / 'store')
    with pytest.raises(ValueError):
        ingest_history_cache(store, payload(), url=URL, attempt_id='../invalid')
    assert not store.root.exists()


def test_same_batch_variants_and_other_channel_filtering(tmp_path):
    data = payload()
    data['records'].append(deepcopy(payload('edited')['records'][0]))
    unrelated = deepcopy(payload('other channel')['records'][0])
    unrelated['target_url'] = 'https://discord.com/channels/111111111111111111/444444444444444444'
    unrelated['message']['channel_id'] = '444444444444444444'
    data['records'].append(unrelated)
    store = CaptureCheckpointStore(tmp_path / 'store')
    result = ingest_history_cache(store, data, url=URL,
                                  attempt_id='variants', apply=True)
    assert result['records_selected'] == 2
    assert result['message_count'] == 1
    assert result['version_count'] == 2
    replay = ingest_history_cache(store, data, url=URL,
                                  attempt_id='variants', apply=True)
    assert replay['window_count'] == 1
    assert len(store.load_message_ledger(result['capture_id'])['events']) == 2


@pytest.mark.parametrize('mutation', ['duplicate_attachment', 'unicode_id', 'missing_timestamp'])
def test_invalid_selected_message_rejected_before_writes(tmp_path, mutation):
    data = payload()
    message = data['records'][0]['message']
    if mutation == 'duplicate_attachment':
        message['attachments'] = [{'id': '555555555555555555'}] * 2
    elif mutation == 'unicode_id':
        message['id'] = '３' * 18
    else:
        del message['timestamp']
    store = CaptureCheckpointStore(tmp_path / 'store')
    with pytest.raises(ValueError):
        ingest_history_cache(store, data, url=URL, attempt_id='bad', apply=True)
    assert not store.root.exists()


def run_cli(*arguments):
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'ingest_history_cache.py'
    return subprocess.run([sys.executable, str(script), *map(str, arguments)],
                          capture_output=True, text=True, check=False)


def test_cli_ingest_status_export_return_only_metadata(tmp_path):
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(payload('PRIVATE_MARKER_avoid_stdout')))
    root = tmp_path / 'store'
    args = ['--store-root', root, '--input', source, '--url', URL, '--attempt-id', 'cli']
    dry = run_cli('ingest', *args)
    assert dry.returncode == 0
    assert json.loads(dry.stdout)['dry_run']
    assert not root.exists()
    applied = run_cli('ingest', *args, '--apply')
    assert applied.returncode == 0, applied.stderr
    capture_id = json.loads(applied.stdout)['capture_id']
    status = run_cli('status', '--store-root', root, '--capture-id', capture_id)
    output = tmp_path / 'private' / 'history.json'
    exported = run_cli('export', '--store-root', root, '--capture-id', capture_id,
                       '--output', output)
    for result in [dry, applied, status, exported]:
        assert result.returncode == 0, result.stderr
        metadata = json.loads(result.stdout)
        assert not metadata['raw_text_returned']
        assert not metadata['full_capture_confirmed']
        assert not metadata['current_snapshot_written']
        assert metadata['outbound_actions'] == 'disabled'
        assert 'PRIVATE_MARKER' not in result.stdout + result.stderr
    assert 'PRIVATE_MARKER' in output.read_text()
    assert json.loads(output.read_text())['batches'][0]['input_file_sha256'] == sha256(source.read_bytes()).hexdigest()


def test_valid_ledger_prefix_rollback_is_rejected_by_status_and_export(tmp_path):
    store = CaptureCheckpointStore(tmp_path / 'store')
    first = ingest_history_cache(store, payload(), url=URL,
                                 attempt_id='rollback', apply=True)
    capture_id = first['capture_id']
    prefix = store.load_message_ledger(capture_id)
    ingest_history_cache(store, payload('edited'), url=URL,
                         attempt_id='rollback', apply=True)
    store.save_receipt(store.message_ledger_path(capture_id), prefix)
    with pytest.raises(CaptureStoreError, match='coverage'):
        history_cache_status(store, capture_id)
    output = tmp_path / 'private' / 'history.json'
    with pytest.raises(CaptureStoreError, match='coverage'):
        export_history_cache(store, capture_id, output)
    assert not output.parent.exists()


def test_partial_batch_ledger_prefix_is_rejected(tmp_path):
    data = payload()
    data['records'].append(payload('edited')['records'][0])
    store = CaptureCheckpointStore(tmp_path / 'store')
    result = ingest_history_cache(store, data, url=URL,
                                  attempt_id='partial-batch', apply=True)
    capture_id = result['capture_id']
    ledger = store.load_message_ledger(capture_id)
    ledger['events'] = ledger['events'][:1]
    ledger['tip_hash'] = ledger['events'][0]['event_hash']
    store.save_receipt(store.message_ledger_path(capture_id), ledger)
    with pytest.raises(CaptureStoreError, match='coverage'):
        history_cache_status(store, capture_id)


@pytest.mark.parametrize('field', ['windows', 'messages'])
def test_coverage_rollback_is_rejected(tmp_path, field):
    store = CaptureCheckpointStore(tmp_path / 'store')
    result = ingest_history_cache(store, payload(), url=URL,
                                  attempt_id='coverage', apply=True)
    coverage = store.load_coverage(result['capture_id'])
    coverage[field] = [] if field == 'windows' else {}
    store.save_receipt(store.coverage_path(result['capture_id']), coverage)
    with pytest.raises(CaptureStoreError, match='coverage'):
        history_cache_status(store, result['capture_id'])


def provenance_payload():
    data = payload()
    data['sources'] = [
        {'guild_id': '111111111111111111', 'channel_id': '444444444444444444',
         'cache_file': 'unselected-private-cache', 'cache_file_sha256': 'a' * 64},
        {'guild_id': '111111111111111111', 'channel_id': '222222222222222222',
         'cache_file': 'selected-private-cache', 'cache_file_sha256': 'b' * 64},
    ]
    data['records'][0]['source_indexes'] = [1]
    data['records'][0]['selected_payload_sha256'] = canonical_capture_digest(data['records'][0]['message'])
    return data


def test_selected_source_provenance_survives_export_with_original_indexes(tmp_path):
    data = provenance_payload()
    original = deepcopy(data)
    store = CaptureCheckpointStore(tmp_path / 'store')
    result = ingest_history_cache(store, data, url=URL,
                                  attempt_id='provenance', apply=True)
    output = tmp_path / 'private' / 'history.json'
    export_history_cache(store, result['capture_id'], output)
    batch = json.loads(output.read_text())['batches'][0]
    assert batch['input_payload_sha256'] == canonical_capture_digest(original)
    assert batch['scope_selected_source_indexes'] == [1]
    assert batch['sources'] == [original['sources'][1]]
    assert batch['records'][0]['source_indexes'] == [1]
    assert 'unselected-private-cache' not in output.read_text()
    assert data == original


@pytest.mark.parametrize('mutation', ['out_of_range', 'missing_indexes', 'wrong_channel', 'invalid_hash', 'wrong_payload_hash'])
def test_invalid_source_provenance_rejected_before_writes(tmp_path, mutation):
    data = provenance_payload()
    if mutation == 'out_of_range':
        data['records'][0]['source_indexes'] = [2]
    elif mutation == 'missing_indexes':
        del data['records'][0]['source_indexes']
    elif mutation == 'wrong_channel':
        data['sources'][1]['channel_id'] = '444444444444444444'
    elif mutation == 'invalid_hash':
        data['sources'][1]['cache_file_sha256'] = 'bad'
    else:
        data['records'][0]['selected_payload_sha256'] = 'a' * 64
    store = CaptureCheckpointStore(tmp_path / 'store')
    with pytest.raises(ValueError):
        ingest_history_cache(store, data, url=URL, attempt_id='bad', apply=True)
    assert not store.root.exists()


def test_cli_rejects_input_beneath_link_without_raw_text(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    source = real / 'source.json'
    source.write_text(json.dumps(payload('PRIVATE_MARKER')))
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    root = tmp_path / 'store'
    result = run_cli('ingest', '--store-root', root, '--input', link / 'source.json',
                     '--url', URL, '--attempt-id', 'cli', '--apply')
    assert result.returncode == 2
    assert not json.loads(result.stdout)['ok']
    assert 'PRIVATE_MARKER' not in result.stdout + result.stderr
    assert not root.exists()
