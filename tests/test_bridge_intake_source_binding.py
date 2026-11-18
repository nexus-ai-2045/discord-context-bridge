import json

import pytest

from discord_context_bridge.cli import main
from discord_context_bridge.core import build_bridge_intake

URL = 'https://discord.com/channels/1/10'
TEXT = 'synthetic-member: synthetic visible message'


def test_legacy_browser_source_requires_observed_target_before_save(tmp_path):
    store = tmp_path / 'snapshots.ndjson'
    payload = build_bridge_intake(url=URL, text=TEXT, source='in_app_browser_accessibility', snapshot_store=store)
    assert payload['snapshot']['saved'] is False
    assert payload['blocked_reason'] == 'observed_url_missing'
    assert not store.exists()


@pytest.mark.parametrize('observed,reason', [('', 'observed_url_missing'), ('https://discord.com/login', 'observed_url_invalid'), ('https://discord.com/channels/1/20', 'observed_target_mismatch')])
def test_browser_binding_blocks_even_with_existing_snapshot(tmp_path, observed, reason):
    store = tmp_path / 'snapshots.ndjson'
    build_bridge_intake(url=URL, text=TEXT, snapshot_store=store)
    before = store.read_bytes()
    payload = build_bridge_intake(url=URL, text=TEXT, snapshot_store=store, capture_route='in_app_browser', observed_url=observed)
    assert payload['blocked_reason'] == reason
    assert payload['snapshot']['saved'] is False
    assert payload['context_passport']['built'] is False
    assert payload['coverage']['target_match'] is False
    assert payload['pipeline']['completed'] == []
    assert store.read_bytes() == before
    rendered = json.dumps(payload)
    assert TEXT not in rendered
    if observed:
        assert observed not in rendered


@pytest.mark.parametrize('observed', [URL, 'https://ptb.discord.com/channels/1/10/?view=visible'])
def test_same_target_browser_binding_saves(tmp_path, observed):
    payload = build_bridge_intake(url=URL, text=TEXT, snapshot_store=tmp_path / 'snapshots.ndjson', capture_route='in_app_browser', observed_url=observed)
    assert payload['snapshot']['saved'] is True
    assert payload['source_binding']['target_match'] is True
    assert payload['source_binding']['input_text_present'] is True
    assert payload['decision'] == 'passport_ready'
    assert payload['source_binding']['body_verified'] is False


def test_cli_browser_binding_rejects_redirect_without_leaking_input(tmp_path, capsys):
    visible = tmp_path / 'visible.txt'
    visible.write_text(TEXT)
    store = tmp_path / 'snapshots.ndjson'
    result = main(['bridge-intake', '--url', URL, '--input', str(visible), '--snapshot-store', str(store), '--capture-route', 'in_app_browser', '--observed-url', 'https://discord.com/login', '--json'])
    output = capsys.readouterr().out
    assert result == 2
    assert json.loads(output)['blocked_reason'] == 'observed_url_invalid'
    assert not store.exists()
    assert TEXT not in output
    assert URL not in output


def test_message_target_cannot_be_replaced_by_channel_observation(tmp_path):
    payload = build_bridge_intake(url=URL + '/30', observed_url=URL, capture_route='in_app_browser', text=TEXT, snapshot_store=tmp_path / 'snapshots.ndjson')
    assert payload['blocked_reason'] == 'observed_target_mismatch'
    assert payload['snapshot']['saved'] is False


@pytest.mark.parametrize('route', ['in_app_browser', 'chrome_extension'])
def test_declared_browser_route_does_not_depend_on_source_label(tmp_path, route):
    payload = build_bridge_intake(url=URL, text=TEXT, source='arbitrary-label', capture_route=route, snapshot_store=tmp_path / 'snapshots.ndjson')
    assert payload['blocked_reason'] == 'observed_url_missing'


def test_manual_label_with_browser_word_is_not_inferred_as_browser(tmp_path):
    payload = build_bridge_intake(url=URL, text=TEXT, source='manual_browser_notes', snapshot_store=tmp_path / 'snapshots.ndjson')
    assert payload['snapshot']['saved'] is True
    assert payload['source_binding']['required'] is False


def test_unknown_route_fails_closed_without_touching_store(tmp_path):
    store = tmp_path / 'snapshots.ndjson'
    payload = build_bridge_intake(url=URL, text=TEXT, capture_route='undeclared_adapter', snapshot_store=store)
    assert payload['blocked_reason'] == 'unsupported_capture_route'
    assert not store.exists()


@pytest.mark.parametrize('text', ['', '  \n\t '])
def test_browser_body_missing_does_not_read_or_write_cached_snapshot(tmp_path, monkeypatch, text):
    store = tmp_path / 'snapshots.ndjson'
    build_bridge_intake(url=URL, text=TEXT, snapshot_store=store)
    before = store.read_bytes()

    def forbidden_snapshot_access(*args, **kwargs):
        pytest.fail('unverified browser body must not read/write snapshots')

    for function in ['snapshot_visible_text', 'build_coverage_report', 'select_latest_snapshot', 'build_latest_snapshot_report']:
        monkeypatch.setattr('discord_context_bridge.core.' + function, forbidden_snapshot_access)
    payload = build_bridge_intake(url=URL, observed_url=URL, capture_route='in_app_browser', text=text, snapshot_store=store)
    assert payload['blocked_reason'] == 'visible_text_missing'
    assert payload['snapshot']['saved'] is False
    assert payload['context_passport']['built'] is False
    assert payload['source_binding']['target_match'] is True
    assert payload['source_binding']['body_verified'] is False
    assert payload['source_binding']['input_text_present'] is False
    assert payload['pipeline']['completed'] == []
    assert store.read_bytes() == before


def test_manual_cache_first_intake_retains_existing_contract(tmp_path):
    store = tmp_path / 'snapshots.ndjson'
    build_bridge_intake(url=URL, text=TEXT, snapshot_store=store)
    payload = build_bridge_intake(url=URL, snapshot_store=store)
    assert payload['snapshot']['saved'] is False
    assert payload['decision'] == 'passport_ready'


@pytest.mark.parametrize('requested,observed,matched', [
    (URL + '/30', URL + '/30', True),
    (URL + '/30', URL + '/31', False),
    (URL + '/30/40', URL + '/30/41', False),
    ('https://discord.com/channels/1/20/threads/10', URL, True),
    ('https://discord.com/channels/1/20/threads/10', 'https://discord.com/channels/1/20', False),
])
def test_exact_message_and_explicit_thread_target_binding(tmp_path, requested, observed, matched):
    payload = build_bridge_intake(url=requested, observed_url=observed, capture_route='in_app_browser', text=TEXT, snapshot_store=tmp_path / 'snapshots.ndjson')
    assert payload['snapshot']['saved'] is matched
    assert payload['source_binding']['target_match'] is matched


@pytest.mark.parametrize('requested,observed,matched', [
    ('https://discord.com/channels/@me/10', 'https://discord.com/channels/@me/10', True),
    ('https://discord.com/channels/@me/10/30', 'https://discord.com/channels/@me/10/30', True),
    ('https://discord.com/channels/@me/10', 'https://discord.com/channels/@me/20', False),
    ('https://discord.com/channels/1/20/threads/10/30', 'https://discord.com/channels/1/10/30', True),
    ('https://discord.com/channels/1/20/threads/10/30', 'https://discord.com/channels/1/10/31', False),
])
def test_supported_dm_and_explicit_thread_message_routes(tmp_path, requested, observed, matched):
    payload = build_bridge_intake(url=requested, observed_url=observed, capture_route='in_app_browser', text=TEXT, snapshot_store=tmp_path / 'snapshots.ndjson')
    assert payload['snapshot']['saved'] is matched
    assert payload['source_binding']['target_match'] is matched


@pytest.mark.parametrize('observed', [
    'https://discord.com/channels/1/@me',
    'https://discord.com/channels/1/10/@me',
    'https://discord.com/channels/1/20/threads/10/invalid',
    'https://[invalid/channels/1/10',
])
def test_reserved_dm_marker_and_malformed_urls_remain_rejected(tmp_path, observed):
    payload = build_bridge_intake(url=URL, observed_url=observed, capture_route='in_app_browser', text=TEXT, snapshot_store=tmp_path / 'snapshots.ndjson')
    assert payload['blocked_reason'] == 'observed_url_invalid'
    assert payload['snapshot']['saved'] is False
