import json
from pathlib import Path

from discord_context_bridge.cache_inventory import build_cache_inventory
from discord_context_bridge.cli import main as cli_main


URL = "https://discord.com/channels/11111111111111111/22222222222222222"


def _write_record(path: Path, *, captured_at: str, title: str = "交流広場B") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "url": URL,
                "captured_at": captured_at,
                "title": title,
                "content_hash": "safe-hash",
                "text": "private body",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_cache_inventory_returns_counts_title_evidence_and_recent_route(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    target = root / "discord" / "servers" / "11111111111111111" / "channels" / "22222222222222222"
    _write_record(target / "text-snapshots.ndjson", captured_at="2026-07-15T01:00:00+00:00")
    (target / "INDEX.md").write_text(
        "---\ntarget_key: ignored-here\ntitle: 交流広場B\n---\n",
        encoding="utf-8",
    )
    (root / "other.md").write_text("# other\n", encoding="utf-8")

    payload = build_cache_inventory(
        url=URL,
        cache_root=root,
        snapshot_store=tmp_path / "missing.ndjson",
        generated_at="2026-07-15T02:00:00+00:00",
    )
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["decision"] == "use_local_snapshot"
    assert payload["inventory"]["markdown_file_count"] == 2
    assert payload["inventory"]["exact_snapshot_record_count"] == 1
    assert payload["title"] == {
        "available": True,
        "source": "snapshot_record",
        "value_output": "omitted",
    }
    assert "private body" not in rendered
    assert "交流広場B" not in rendered
    assert str(tmp_path) not in rendered
    assert "11111111111111111" not in rendered


def test_cache_inventory_can_include_private_title_and_marks_stale(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    record = root / "discord" / "servers" / "11111111111111111" / "channels" / "22222222222222222" / "text-snapshots.ndjson"
    _write_record(record, captured_at="2026-07-10T01:00:00+00:00")

    payload = build_cache_inventory(
        url=URL,
        cache_root=root,
        snapshot_store=tmp_path / "missing.ndjson",
        include_private_title=True,
        generated_at="2026-07-15T02:00:00+00:00",
    )

    assert payload["decision"] == "refresh_exact_url_snapshot"
    assert payload["freshness"]["status"] == "stale"
    assert payload["title"]["value"] == "交流広場B"


def test_cache_inventory_missing_target_requests_capture(tmp_path: Path) -> None:
    root = tmp_path / "raw-snapshots"
    root.mkdir()
    payload = build_cache_inventory(
        url=URL,
        cache_root=root,
        snapshot_store=tmp_path / "missing.ndjson",
        generated_at="2026-07-15T02:00:00+00:00",
    )
    assert payload["state"] == "snapshot_missing"
    assert payload["decision"] == "capture_visible_or_read_only_adapter"
    assert payload["stale_policy"]["usable_for_reply"] is False


def test_cache_inventory_accepts_saved_snapshot_when_raw_root_is_absent(tmp_path: Path) -> None:
    snapshot_store = tmp_path / "text-snapshots.ndjson"
    _write_record(snapshot_store, captured_at="2026-07-15T01:00:00+00:00")
    payload = build_cache_inventory(
        url=URL,
        cache_root=tmp_path / "missing-raw-root",
        snapshot_store=snapshot_store,
        generated_at="2026-07-15T02:00:00+00:00",
    )
    assert payload["ok"] is True
    assert payload["state"] == "ready"
    assert payload["decision"] == "use_local_snapshot"


def test_cache_inventory_cli_is_metadata_only_end_to_end(tmp_path: Path, capsys) -> None:
    root = tmp_path / "raw-snapshots"
    record = root / "discord" / "target" / "text-snapshots.ndjson"
    _write_record(record, captured_at="2026-07-15T01:00:00+00:00")

    result = cli_main(
        [
            "cache-inventory",
            "--url",
            URL,
            "--cache-root",
            str(root),
            "--snapshot-store",
            str(tmp_path / "missing.ndjson"),
            "--json",
        ]
    )
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)

    assert result == 0
    assert payload["inventory"]["exact_snapshot_record_count"] == 1
    assert payload["raw_text_returned"] is False
    assert "private body" not in rendered
    assert "交流広場B" not in rendered
    assert str(tmp_path) not in rendered
    assert "11111111111111111" not in rendered


def test_target_lookup_uses_canonical_only_and_omits_private_data(tmp_path, monkeypatch):
    from discord_context_bridge import cache_inventory, core

    store = tmp_path / "snapshots.ndjson"
    core.snapshot_visible_text(text="private body", url=URL, title="交流広場B", path=store)

    def forbidden(*args, **kwargs):
        raise AssertionError("target lookup must not scan cache root or markdown")

    monkeypatch.setattr(cache_inventory, "_safe_files", forbidden)
    monkeypatch.setattr(cache_inventory, "_frontmatter", forbidden)
    payload = cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)
    assert payload["state"] == "ready"
    assert payload["exact_snapshot_record_count"] == 1
    assert payload["lookup_scope"]["external_cache_discovery"] is False
    assert payload["lookup_scope"]["full_inventory"] is False
    rendered = json.dumps(payload, ensure_ascii=False)
    for private in ("private body", "交流広場B", str(tmp_path), "11111111111111111"):
        assert private not in rendered


def test_target_lookup_revalidates_ledger_after_same_size_edit(tmp_path):
    import os
    import pytest
    from discord_context_bridge import cache_inventory, core
    from discord_context_bridge.capture.store import CheckpointCorruptError

    store = tmp_path / "snapshots.ndjson"
    core.snapshot_visible_text(text="first", url=URL, path=store)
    assert cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)["ok"]
    stat = store.stat()
    original = store.read_bytes()
    tampered = original.replace(b'"text": "first"', b'"text": "other"')
    assert tampered != original and len(tampered) == len(original)
    store.write_bytes(tampered)
    os.utime(store, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(CheckpointCorruptError):
        cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)


def test_target_lookup_observes_append_replace_truncate_and_target_switch(tmp_path):
    from discord_context_bridge import cache_inventory, core

    store = tmp_path / "snapshots.ndjson"
    other_url = "https://discord.com/channels/11111111111111111/33333333333333333"
    core.snapshot_visible_text(text="one", url=URL, path=store)
    first = store.read_bytes()
    core.snapshot_visible_text(text="two", url=URL, path=store)
    assert cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)["exact_snapshot_record_count"] == 2
    assert cache_inventory.build_target_cache_lookup(url=other_url, snapshot_store=store)["state"] == "snapshot_missing"
    # A valid historical prefix remains valid; no persistent index may return the removed row.
    store.write_bytes(first)
    assert cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)["exact_snapshot_record_count"] == 1
    replacement = tmp_path / "replacement.ndjson"
    core.snapshot_visible_text(text="other target", url=other_url, path=replacement)
    replacement.replace(store)
    assert cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)["state"] == "snapshot_missing"
    store.write_bytes(b"")
    assert cache_inventory.build_target_cache_lookup(url=other_url, snapshot_store=store)["state"] == "snapshot_missing"


def test_target_lookup_invalid_url_does_not_read_store(tmp_path, monkeypatch):
    from discord_context_bridge import cache_inventory

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid URL must stop before reading")

    monkeypatch.setattr(cache_inventory, "matching_snapshot_records", forbidden)
    payload = cache_inventory.build_target_cache_lookup(url="https://example.invalid", snapshot_store=tmp_path / "missing")
    assert payload["state"] == "invalid_url"


def test_frontmatter_reads_at_most_8192_characters(tmp_path, monkeypatch):
    import io
    from discord_context_bridge.cache_inventory import _frontmatter

    path = tmp_path / "large.md"
    raw = "---\ntitle: fixture\n---\n" + "x" * 1_000_000
    reads = []

    class BoundedReader(io.StringIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: BoundedReader(raw))
    assert _frontmatter(path)["title"] == "fixture"
    assert reads == [8192]


def test_target_lookup_read_volume_excludes_unrelated_files(tmp_path, monkeypatch):
    from discord_context_bridge import cache_inventory

    root = tmp_path / "external-cache"
    root.mkdir()
    for index in range(20):
        (root / f"unrelated-{index}.md").write_text("---\ntitle: unrelated\n---\n" + "x" * 20000)
        (root / f"unrelated-{index}.ndjson").write_text(json.dumps({"url": "https://example.invalid", "text": "x" * 20000}) + "\n")
    store = tmp_path / "canonical.ndjson"
    _write_record(store, captured_at="2026-07-15T01:00:00+00:00")
    original_open = Path.open
    reads = []

    class MeteredReader:
        def __init__(self, handle, path):
            self.handle, self.path = handle, path

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, size=-1):
            value = self.handle.read(size)
            reads.append((self.path, len(value.encode("utf-8") if isinstance(value, str) else value)))
            return value

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def metered_open(path, *args, **kwargs):
        return MeteredReader(original_open(path, *args, **kwargs), path)

    monkeypatch.setattr(Path, "open", metered_open)
    full = cache_inventory.build_cache_inventory(url=URL, snapshot_store=store, cache_root=root)
    full_reads = list(reads)
    reads.clear()
    target = cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)
    assert full["inventory"]["exact_snapshot_record_count"] == target["exact_snapshot_record_count"] == 1
    assert len(full_reads) == 41
    assert len(reads) == 1
    assert all(path == store for path, _ in reads)
    assert sum(size for _, size in reads) < sum(size for _, size in full_reads) / 100


def test_target_lookup_honors_alias_continuation_and_revocation(tmp_path):
    import pytest
    from discord_context_bridge import cache_inventory, core
    from discord_context_bridge.capture.store import CheckpointCorruptError

    nested = "https://discord.com/channels/12345678901234567/23456789012345678/threads/34567890123456789"
    flat = "https://discord.com/channels/12345678901234567/34567890123456789"
    store = tmp_path / "snapshots.ndjson"
    core.snapshot_visible_text(text="first", url=nested, path=store)
    core.snapshot_visible_text(text="second", url=nested, path=store)
    prior = core.load_text_snapshots(store)[-1]
    alias_key = core.target_key_for_url(flat)
    alias = dict(prior)
    alias.update(
        event_id="alias-lookup-test", url=flat, target_key=alias_key, stream_id=alias_key,
        subject=alias_key, stream_sequence=3, expected_previous_stream_sequence=2,
        observation_index_for_target=3, previous_event_hash=prior["event_hash"],
        previous_content_hash=prior["content_hash"], text="alias observation",
        content_hash=core.stable_text_hash("alias observation"),
    )
    alias["event_hash"] = core.canonical_event_hash(alias)
    with store.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(alias) + "\n")
    core.acknowledge_alias_continuations(store)
    assert cache_inventory.build_target_cache_lookup(url=flat, snapshot_store=store)["exact_snapshot_record_count"] == 1
    # A malformed foreign observation cannot be used even if the requested target is valid.
    previous = core.load_text_snapshots(store)[-1]
    bad = dict(alias)
    bad.update(event_id="revoked-lookup-test", url=nested, stream_id=core.target_key_for_url(nested),
               stream_sequence=5, expected_previous_stream_sequence=4,
               previous_event_hash=previous["event_hash"], previous_content_hash=previous["content_hash"],
               text="bad observation", content_hash=core.stable_text_hash("bad observation"))
    for key in ("target_key", "subject", "dataschema", "type", "datacontenttype"):
        bad.pop(key, None)
    with store.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(bad) + "\n")
    with pytest.raises(CheckpointCorruptError):
        cache_inventory.build_target_cache_lookup(url=flat, snapshot_store=store)
    core.revoke_invalid_snapshot_observation(store)
    assert cache_inventory.build_target_cache_lookup(url=flat, snapshot_store=store)["exact_snapshot_record_count"] == 1


def test_canonical_target_lookup_rejects_url_only_in_other_target_body(tmp_path):
    from discord_context_bridge import core
    from discord_context_bridge.cache_inventory import build_target_cache_lookup
    store = tmp_path / 'canonical.ndjson'
    other = 'https://discord.com/channels/1/20'
    core.snapshot_visible_text(text='reference: ' + URL, url=other, path=store)
    lookup = build_target_cache_lookup(url=URL, snapshot_store=store)
    assert lookup['state'] == 'snapshot_missing'
    assert lookup['exact_snapshot_record_count'] == 0
    intake = core.build_bridge_intake(url=URL, snapshot_store=store)
    assert intake['coverage']['exact_coverage'] is False
    assert intake['latest_snapshot_report']['ok'] is False
    assert core.matching_snapshot_records(store, url=URL, target_key=core.target_key_for_url(URL), canonical=False)


def test_frontmatter_preserves_existing_unicode_character_limit(tmp_path):
    from discord_context_bridge.cache_inventory import _frontmatter
    path = tmp_path / 'unicode.md'
    text = '---\nnote: ' + 'あ' * 3000 + '\ntitle: retained\n---\n' + 'x' * 100000
    assert text.index('title:') < 8192
    assert len(text[:text.index('title:')].encode('utf-8')) > 8192
    path.write_text(text, encoding='utf-8')
    assert _frontmatter(path)['title'] == 'retained'


def test_target_lookup_uses_original_capture_time_of_reply_context(tmp_path):
    from datetime import datetime, timedelta, timezone

    from discord_context_bridge import cache_inventory, core

    store = tmp_path / "snapshots.ndjson"
    captured = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    core.build_bridge_intake(
        url=URL,
        messages=[
            {
                "author_label": "member-a",
                "text": "古い取得の本文",
                "sequence": 0,
                "timestamp": "",
                "is_thread_root": True,
                "is_reply_target": True,
                "id": "1",
            }
        ],
        history_exhausted=True,
        capture_observed_at=captured,
        snapshot_store=store,
    )

    payload = cache_inventory.build_target_cache_lookup(url=URL, snapshot_store=store)

    assert payload["exact_snapshot_record_count"] == 1
    assert payload["freshness"]["status"] != "recent"
    assert payload["decision"] != "use_local_snapshot"


def test_target_lookup_ignores_original_capture_time_without_timezone(tmp_path):
    from datetime import datetime, timedelta

    from discord_context_bridge import cache_inventory

    record = {
        "captured_at": "2026-11-19T01:00:00+00:00",
        "reply_context_evidence": {
            "capture_observed_at": (datetime(2026, 11, 18, 1, 0) - timedelta(hours=1)).isoformat()
        },
    }

    assert cache_inventory._record_with_original_capture_time(record) == record
    assert cache_inventory._record_with_original_capture_time({**record, "reply_context_evidence": "bad"}) == {
        **record,
        "reply_context_evidence": "bad",
    }
