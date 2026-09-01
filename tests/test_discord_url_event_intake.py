import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("discord_url_event_intake", ROOT / "scripts" / "discord_url_event_intake.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

URL = "https://discord.com/channels/11111111111111111/22222222222222222/33333333333333333"


def test_prompt_url_event_without_live_payload_enqueues_refresh(tmp_path: Path) -> None:
    payload = MODULE.process_event(
        url=URL,
        event_id="turn-1",
        source="prompt_url_received",
        state_path=tmp_path / "state.sqlite3",
        snapshot_store=tmp_path / "snapshots.ndjson",
    )
    assert payload["ok"] is True
    assert payload["decision"] == "enqueued"
    assert payload["job_id"]
    assert payload["route_priority"][0] == "gateway_live_event"
    assert payload["chrome_event_role"] == "supplemental"
    assert payload["reconciliation_required"] is True
    assert payload["outbound_actions"] == "disabled"


def test_live_event_refreshes_snapshot_and_is_idempotent(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    snapshot = tmp_path / "snapshots.ndjson"
    first = MODULE.process_event(
        url=URL,
        event_id="gateway-seq-10",
        source="gateway_message_create",
        text="user: 自動化の相談です",
        state_path=state,
        snapshot_store=snapshot,
    )
    second = MODULE.process_event(
        url=URL,
        event_id="gateway-seq-10",
        source="gateway_message_create",
        text="user: 自動化の相談です",
        state_path=state,
        snapshot_store=snapshot,
    )
    assert first["decision"] == "snapshot_refreshed"
    assert second["decision"] == "already_processed"
    assert len(snapshot.read_text(encoding="utf-8").splitlines()) == 1
    rendered = json.dumps(first, ensure_ascii=False)
    assert URL not in rendered
    assert "自動化の相談です" not in rendered


def test_invalid_event_does_not_write_state(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    payload = MODULE.process_event(
        url="https://example.com/not-discord",
        event_id="event-1",
        source="prompt_url_received",
        text="private",
        state_path=state,
        snapshot_store=tmp_path / "snapshots.ndjson",
    )
    assert payload["decision"] == "blocked"
    assert not state.exists()


def test_pending_event_can_be_completed_when_payload_arrives(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    snapshot = tmp_path / "snapshots.ndjson"
    queued = MODULE.process_event(
        url=URL, event_id="turn-2", source="prompt_url_received",
        state_path=state, snapshot_store=snapshot,
    )
    completed = MODULE.process_event(
        url=URL, event_id="turn-2", source="prompt_url_received", text="user: 最新本文",
        state_path=state, snapshot_store=snapshot,
    )
    assert queued["decision"] == "enqueued"
    assert completed["decision"] == "snapshot_refreshed"
    assert queued["job_id"] == completed["job_id"]


def test_concurrent_duplicate_event_writes_snapshot_once(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    snapshot = tmp_path / "snapshots.ndjson"

    def run_once():
        return MODULE.process_event(
            url=URL, event_id="gateway-seq-20", source="gateway_message_create",
            text="user: concurrent", state_path=state, snapshot_store=snapshot,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_once(), range(2)))

    decisions = [item["decision"] for item in results]
    assert decisions.count("snapshot_refreshed") == 1
    assert next(item for item in decisions if item != "snapshot_refreshed") in {"already_processed", "already_queued"}
    assert len(snapshot.read_text(encoding="utf-8").splitlines()) == 1


def test_worker_drains_url_only_job_and_recovers_expired_lease(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    snapshot = tmp_path / "snapshots.ndjson"
    queued = MODULE.process_event(
        url=URL, event_id="turn-worker", source="prompt_url_received",
        state_path=state, snapshot_store=snapshot,
    )
    connection = MODULE._connect(state)
    connection.execute(
        "UPDATE url_events SET status='processing', owner='dead-worker', lease_expires_at=0 WHERE job_id=?",
        (queued["job_id"],),
    )
    connection.close()
    producer = tmp_path / "producer.py"
    producer.write_text("import sys; sys.stdin.read(); print('user: fresh context')", encoding="utf-8")
    command = f'"{sys.executable}" "{producer}"'

    drained = MODULE.drain_once(
        state_path=state, source_command=command, snapshot_store=snapshot,
    )

    assert drained["decision"] == "snapshot_refreshed"
    assert drained["job_id"] == queued["job_id"]
    assert len(snapshot.read_text(encoding="utf-8").splitlines()) == 1
