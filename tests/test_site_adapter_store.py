import json
from pathlib import Path

import pytest
from concurrent.futures import ThreadPoolExecutor

from discord_context_bridge.site_adapter_runtime import MAX_INPUT_BYTES, build_capture
from discord_context_bridge.site_adapter_store import store_capture


URL = "https://discord.com/channels/1/2/3"


def test_store_is_atomic_idempotent_and_public_safe(tmp_path: Path):
    capture = build_capture(URL, body_text="PRIVATE BODY")
    first = store_capture(capture, tmp_path / ".local")
    second = store_capture(capture, tmp_path / ".local")
    assert first == second
    assert first["capture_state"] == "partial"
    assert "PRIVATE BODY" not in json.dumps(first)
    assert str(tmp_path) not in json.dumps(first)
    assert not list(tmp_path.rglob("*.tmp"))


def test_store_rejects_outside_local_root(tmp_path: Path):
    capture = build_capture(URL, body_text="text")
    with pytest.raises(ValueError, match=".local"):
        store_capture(capture, tmp_path / "public")
    with pytest.raises(ValueError, match=".local"):
        store_capture(capture, tmp_path / ".local-public")


def test_store_rejects_direct_oversized_capture_before_persistence(tmp_path: Path):
    capture = build_capture(URL, body_text="text")
    capture["untrusted_extra"] = "x" * MAX_INPUT_BYTES
    with pytest.raises(ValueError, match="aggregate input limit"):
        store_capture(capture, tmp_path / ".local")
    assert not (tmp_path / ".local").exists()


def test_store_rejects_unc_root_before_creating_directories(monkeypatch):
    root = Path(r"\\server\share\.local\captures")
    with pytest.raises(ValueError, match="network"):
        store_capture(build_capture(URL, body_text="PRIVATE"), root)


def test_store_rejects_symlink_root(tmp_path: Path):
    target = tmp_path / ".local-target"
    target.mkdir()
    link = tmp_path / ".local"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlink"):
        store_capture(build_capture(URL, body_text="text"), link)


def test_store_rejects_nested_symlink(tmp_path: Path):
    root = tmp_path / ".local"
    outside = tmp_path / "outside"
    root.mkdir(); outside.mkdir()
    try:
        (root / "raw").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlink"):
        store_capture(build_capture(URL, body_text="text"), root)


def test_write_failure_returns_metadata_only_blocked_receipt(tmp_path: Path):
    capture = build_capture(URL, body_text="PRIVATE BODY")
    def fail(*_args, **_kwargs):
        raise OSError(f"PRIVATE BODY {tmp_path}")
    result = store_capture(capture, tmp_path / ".local", writer=fail)
    rendered = json.dumps(result)
    assert result["capture_state"] == "blocked"
    assert result["recoverable"] is True
    assert "PRIVATE BODY" not in rendered
    assert str(tmp_path) not in rendered


def test_concurrent_store_has_no_temp_collision(tmp_path: Path):
    capture = build_capture(URL, body_text="text")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store_capture(capture, tmp_path / ".local"), range(24)))
    assert len({item["capture_id"] for item in results}) == 1
    assert not list(tmp_path.rglob("*.tmp"))


def test_corrupt_existing_raw_is_replaced(tmp_path: Path):
    capture = build_capture(URL, body_text="text")
    first = store_capture(capture, tmp_path / ".local")
    raw = tmp_path / ".local" / "raw" / f"{first['capture_id']}.json"
    raw.write_text("corrupt", encoding="utf-8")
    store_capture(capture, tmp_path / ".local")
    assert json.loads(raw.read_text(encoding="utf-8"))["schema"] == "dcb.raw_capture.v1"


def test_valid_but_wrong_existing_artifacts_are_replaced(tmp_path: Path):
    capture = build_capture(URL, body_text="text")
    result = store_capture(capture, tmp_path / ".local")
    raw = tmp_path / ".local/raw" / f"{result['capture_id']}.json"
    payload = json.loads(raw.read_text(encoding="utf-8")); payload["source_url"] = URL + "/wrong"
    raw.write_text(json.dumps(payload), encoding="utf-8")
    store_capture(capture, tmp_path / ".local")
    assert json.loads(raw.read_text(encoding="utf-8"))["source_url"] == URL
