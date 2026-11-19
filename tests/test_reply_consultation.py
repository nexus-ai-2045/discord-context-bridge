import json
from pathlib import Path
from unittest.mock import patch

import pytest

from discord_context_bridge import core
from discord_context_bridge.reply_consultation import prepare_reply_consultation, load_reply_consultation

URL = 'https://discord.com/channels/1/10'


@pytest.fixture
def consultation(tmp_path):
    root = tmp_path / 'lcs'
    docs = root / 'docs/knowledge/communication'
    docs.mkdir(parents=True)
    (docs / 'README.md').write_text('# 正本\n', encoding='utf-8')
    (docs / 'interpersonal.md').write_text('更新日: 2026-10-18\n## LCS-IP-001: 目的\n', encoding='utf-8')
    skill = tmp_path / 'SKILL.md'
    skill.write_text('LCS_REPO_ROOT\ndocs/knowledge/communication\n', encoding='utf-8')
    capture = {
        'source_url': URL, 'captured_at': core.utc_now(), 'history_exhausted': True,
        'messages': [
            {'id': '100', 'sequence': 0, 'author_label': 'a', 'visible_timestamp': core.utc_now(),
             'body_text': '引用: 攻撃されて困った', 'is_thread_root': True},
            {'id': '101', 'sequence': 1, 'author_label': 'b', 'visible_timestamp': core.utc_now(),
             'body_text': 'どの点を確認したいですか', 'is_reply_target': True},
        ],
    }
    return dict(url=URL, capture=capture, snapshot_store=tmp_path / 'snapshots.ndjson',
                draft='確認させてください', goal='意図を確認する', skill_file=skill,
                lcs_root=root, output=tmp_path / 'consultation.json')


def test_saved_receipt_gate_lcs_and_private_consumer(consultation):
    status = prepare_reply_consultation(**consultation)
    assert status['state'] == 'ready_for_ai_consultation'
    assert status['snapshot_saved'] and status['lcs_read'] and status['skill_read']
    assert not status['ai_advice_generated']
    output = consultation['output']
    assert output.stat().st_mode & 0o777 == 0o600
    packet = json.loads(output.read_text())
    assert '攻撃されて困った' not in output.read_text()  # canonicalのみ
    assert packet['lcs_references'][1]['stable_ids'] == ['LCS-IP-001']
    loaded = load_reply_consultation(output)
    assert '攻撃されて困った' in loaded['saved_context']
    assert loaded['reply_context_gate']['reply_generation_allowed']
    assert 'risky' not in json.dumps(status)  # 局所単語による人格・攻撃性判定を使わない


@pytest.mark.parametrize('observed', ['https://discord.com/login', 'https://discord.com/channels/1/20'])
def test_target_switch_and_login_do_not_save(consultation, observed):
    consultation['capture']['source_url'] = observed
    status = prepare_reply_consultation(**consultation)
    assert status['state'] == 'blocked'
    assert not status['snapshot_saved']
    assert not consultation['snapshot_store'].exists()
    assert not consultation['output'].exists()


@pytest.mark.parametrize('change', ['root', 'target', 'unresolved', 'history', 'body', 'timestamp', 'order'])
def test_insufficient_context_never_writes_candidate(consultation, change):
    capture = consultation['capture']
    if change == 'root':
        capture['messages'][0]['is_thread_root'] = False
    elif change == 'target':
        capture['messages'][1]['is_reply_target'] = False
    elif change == 'unresolved':
        capture['messages'][0]['unresolved_reference'] = True
    elif change == 'history':
        capture['history_exhausted'] = False
    elif change == 'body':
        capture['messages'][0]['body_text'] = ''
    elif change == 'timestamp':
        capture['captured_at'] = '2000-01-01T00:00:00+00:00'
    else:
        for row in capture['messages']:
            row.pop('sequence')
            row['visible_timestamp'] = 'unknown'
    status = prepare_reply_consultation(**consultation)
    assert status['state'] == 'blocked'
    assert not status['packet_written']
    assert not consultation['output'].exists()


def test_prior_ten_without_exhausted_history(consultation):
    capture = consultation['capture']
    capture['history_exhausted'] = False
    first, last = capture['messages']
    capture['messages'] = [first] + [dict(first, id=str(100+i), sequence=i, is_thread_root=False) for i in range(1, 10)] + [dict(last, id="110", sequence=10)]
    assert prepare_reply_consultation(**consultation)['state'] == 'ready_for_ai_consultation'


@pytest.mark.parametrize('failure', ['lcs', 'skill', 'empty-env', 'save', 'packet'])
def test_dependency_and_save_failures_are_explicit(consultation, failure, monkeypatch):
    if failure == 'lcs':
        consultation['lcs_root'] = Path('/nonexistent/lcs')
    elif failure == 'skill':
        consultation['skill_file'].write_text('旧版skill')
    elif failure == 'empty-env':
        consultation['lcs_root'] = None
        monkeypatch.setenv('LCS_REPO_ROOT', '')
    elif failure == 'save':
        monkeypatch.setattr(core, '_append_text_snapshot_transaction', lambda *a, **k: (_ for _ in ()).throw(OSError('synthetic')))
    else:
        consultation['output'].write_text('existing')
    status = prepare_reply_consultation(**consultation)
    assert status['state'] == 'blocked'
    assert not status['packet_written']
    if failure == 'save':
        assert not status['snapshot_saved']
    if failure in {'lcs', 'empty-env'}:
        assert not status['lcs_read']


@pytest.mark.parametrize('mutation', ['reference', 'ledger_replace', 'ledger_truncate', 'ledger_modify', 'gate'])
def test_consumer_revalidates_current_sources(consultation, mutation):
    prepare_reply_consultation(**consultation)
    if mutation == 'reference':
        consultation['skill_file'].write_text('changed')
    elif mutation == 'gate':
        packet = json.loads(consultation['output'].read_text())
        packet['reply_context_gate']['prior_message_count'] = 999
        consultation['output'].write_text(json.dumps(packet))
    elif mutation == 'ledger_modify':
        path = consultation['snapshot_store']
        path.write_text(path.read_text().replace('困った', '変えた'))
    else:
        consultation['snapshot_store'].write_text('' if mutation == 'ledger_truncate' else '{}\n')
    with pytest.raises((ValueError, RuntimeError)):
        load_reply_consultation(consultation['output'])


def test_bridge_intake_reuses_transaction_readback(consultation):
    original = core.load_text_snapshots
    with patch.object(core, 'load_text_snapshots', wraps=original) as reads:
        payload = core.build_bridge_intake(url=URL, text='a: synthetic', snapshot_store=consultation['snapshot_store'])
    assert payload['snapshot']['saved']
    assert reads.call_count == 2  # lock内の事前読取と追記後readbackのみ
    with patch.object(core, 'load_text_snapshots', wraps=original) as reads:
        core.build_bridge_intake(url=URL, snapshot_store=consultation['snapshot_store'])
    assert reads.call_count == 1  # 新規保存のない照会は一回検証


def test_new_append_does_not_change_pinned_consultation(consultation):
    prepare_reply_consultation(**consultation)
    core.snapshot_visible_text(text='later: different observation', url=URL, path=consultation['snapshot_store'])
    assert '困った' in load_reply_consultation(consultation['output'])['saved_context']


@pytest.mark.parametrize('case', ['wrong-target', 'ambiguous', 'duplicate-id', 'duplicate-sequence', 'missing-reference', 'attachment', 'future', 'naive'])
def test_review_regressions_reject_unsafe_context(consultation, case):
    capture = consultation['capture']
    if case in {'wrong-target', 'ambiguous'}:
        consultation['url'] = URL + '/999'
        capture['source_url'] = consultation['url']
        if case == 'wrong-target':
            capture['target_kind'] = 'message'
    elif case == 'duplicate-id':
        capture['messages'][1]['id'] = '100'
    elif case == 'duplicate-sequence':
        capture['messages'][1]['sequence'] = 0
    elif case == 'missing-reference':
        capture['messages'][1]['reply_to_message_id'] = '999'
    elif case == 'attachment':
        capture['messages'][0]['attachments'] = ['unread.txt']
    elif case == 'future':
        capture['captured_at'] = '2099-01-01T00:00:00+00:00'
    else:
        capture['captured_at'] = '2026-10-18T00:00:00'
    assert prepare_reply_consultation(**consultation)['state'] == 'blocked'
    assert not consultation['output'].exists()


def test_original_capture_freshness_is_not_extended_by_save(consultation, monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    consultation['capture']['captured_at'] = (now - timedelta(hours=23)).isoformat()
    assert prepare_reply_consultation(**consultation)['state'] == 'ready_for_ai_consultation'
    monkeypatch.setattr('discord_context_bridge.reply_consultation.utc_now', lambda: (now + timedelta(hours=2)).isoformat())
    with pytest.raises(ValueError, match='original_capture_refresh_required'):
        load_reply_consultation(consultation['output'])


@pytest.mark.parametrize("field,value", [("channel_id", "20"), ("guild_id", "2")])
def test_explicit_message_scope_conflict_blocks(consultation, field, value):
    consultation["capture"]["messages"][0][field] = value
    assert prepare_reply_consultation(**consultation)["reason"] == "message_scope_mismatch"
    assert not consultation["output"].exists()


def test_prepare_cli_and_target_lookup_return_metadata(consultation, capsys):
    from discord_context_bridge.cli import main
    capture_path = consultation['output'].with_name('capture.json')
    capture_path.write_text(json.dumps(consultation['capture']))
    assert main([
        'prepare-reply-consultation', '--url', URL, '--capture-file', str(capture_path),
        '--snapshot-store', str(consultation['snapshot_store']), '--goal', consultation['goal'],
        '--skill-file', str(consultation['skill_file']), '--lcs-root', str(consultation['lcs_root']),
        '--output', str(consultation['output']), '--json',
    ]) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout)['state'] == 'ready_for_ai_consultation'
    assert '困った' not in stdout and str(consultation['output']) not in stdout
    assert main(['target-cache-lookup', '--url', URL, '--snapshot-store', str(consultation['snapshot_store']), '--json']) == 0
    assert json.loads(capsys.readouterr().out)['lookup_scope']['full_inventory'] is False


def test_watch_guide_stops_without_relationship_context(monkeypatch, capsys):
    from discord_context_bridge import cli
    monkeypatch.setattr(cli, 'read_command_text', lambda *args, **kwargs: 'a: hello')
    assert cli.main(['watch-guide', '--source-command', 'unused', '--draft', 'hello',
                     '--understanding-confirmed', '--max-polls', '1', '--json']) == 0
    remaining = capsys.readouterr().out.strip()
    updates = []
    while remaining:
        item, end = json.JSONDecoder().raw_decode(remaining)
        updates.append(item)
        remaining = remaining[end:].strip()
    assert updates[0]['reply_review']['quick_verdict'] == 'reply-context-blocked'
    assert updates[0]['reply_review']['final_candidate'] == ''


def test_missing_lcs_root_stops_without_a_built_in_default(consultation, monkeypatch):
    consultation['lcs_root'] = None
    monkeypatch.delenv('LCS_REPO_ROOT', raising=False)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: consultation['output'].parent))

    status = prepare_reply_consultation(**consultation)

    assert status['state'] == 'blocked'
    assert status['reason'] == 'lcs_root_missing'
    assert not status['lcs_read']
