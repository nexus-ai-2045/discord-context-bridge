from __future__ import annotations

import argparse
import hashlib
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from discord_context_bridge.capture.parallel_closeout import (
    parent_target_key_digest,
    persist_parallel_producer_drain_receipt,
    persist_parallel_run_stop_receipt,
)
from discord_context_bridge.cli import main as cli_main
from discord_context_bridge.completeness_store import CompletenessStore


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _full_certificate() -> dict[str, object]:
    return {
        "schema": "discord_full_capture_completion_gate.v1",
        "capture_id": "smoke-capture",
        "status": "full",
        "full_capture_confirmed": True,
        "counts": {
            "messages": 1,
            "attachments_discovered": 0,
            "attachments_saved": 0,
            "attachments_manifested": 0,
        },
        "attachments_consistent": True,
        "unresolved_gap_count": 0,
        "blockers": [],
    }


def run_smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="dcb-parallel-closeout-") as temp_dir:
        run = Path(temp_dir) / "run"
        _write_json(
            run / "run-metadata.json",
            {
                "schema": "dcb.parallel-run.v1",
                "status": "failed",
                "stopped_reason": "producer_failed",
                "canonical_count": 1,
                "shard_counts": [1],
                "outbound_actions": "disabled",
            },
        )
        _write_json(
            run / "shards" / "worker-0.json",
            [{"private_value": "must-not-leak"}],
        )
        for producer in ("worker-0", "importer"):
            persist_parallel_producer_drain_receipt(
                run,
                producer=producer,
                event_id=f"smoke-{producer}-drained",
            )
        persist_parallel_run_stop_receipt(
            run,
            event_id="smoke-producer-quiesced",
            stopped_reason="producer_failed",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli_main(
                [
                    "closeout-parallel-run",
                    "--run-dir",
                    str(run),
                    "--finalize",
                    "--json",
                ]
            )
        payload = json.loads(output.getvalue())
        receipt_path = run / "audit" / "parallel-run-closeout.json"
        metadata = json.loads((run / "run-metadata.json").read_text(encoding="utf-8"))
        blocked_ok = bool(
            exit_code == 2
            and payload.get("terminal_state") == "blocked_closed"
            and payload.get("full_capture_confirmed") is False
            and payload.get("raw_text_returned") is False
            and payload.get("participant_names_returned") is False
            and payload.get("path_output") == "omitted"
            and payload.get("outbound_actions") == "disabled"
            and receipt_path.exists()
            and metadata.get("status") == "blocked_closed"
            and "must-not-leak" not in output.getvalue()
        )

        full_run = Path(temp_dir) / "full-run"
        parent_target = "smoke-parent"
        _write_json(
            full_run / "run-metadata.json",
            {
                "schema": "dcb.parallel-run.v1",
                "status": "running",
                "canonical_count": 1,
                "shard_counts": [1],
                "parent_target_key_sha256": parent_target_key_digest(parent_target),
                "outbound_actions": "disabled",
            },
        )
        _write_json(full_run / "shards" / "worker-0.json", [{"opaque": True}])
        ready_path = full_run / "spool" / "worker-0" / "0000.ready.json"
        _write_json(
            ready_path,
            {
                "schema": "dcb.browser-spool.v1",
                "status": "ready",
                "worker": 0,
                "index": 0,
            },
        )
        text_path = full_run / "spool" / "worker-0" / "0000.txt"
        text_path.write_text("smoke", encoding="utf-8")
        ready_hash = hashlib.sha256(ready_path.read_bytes()).hexdigest()
        text_hash = hashlib.sha256(text_path.read_bytes()).hexdigest()
        binding = {
            "worker": 0,
            "index": 0,
            "commit_state": "committed",
            "ready_sha256": ready_hash,
            "text_sha256": text_hash,
            "outbound_actions": "disabled",
        }
        _write_json(
            full_run / "committed" / "worker-0-item-0000" / "receipt.json",
            {
                **binding,
                "schema": "dcb-parallel-import-receipt.v1",
                "source_status": "ready",
            },
        )
        ledger_path = full_run / "committed" / "commit-ledger.ndjson"
        ledger_path.write_text(
            json.dumps({**binding, "schema": "dcb-parallel-commit-ledger.v1"}) + "\n",
            encoding="utf-8",
        )
        for producer in ("worker-0", "importer"):
            persist_parallel_producer_drain_receipt(
                full_run,
                producer=producer,
                event_id=f"full-smoke-{producer}-drained",
            )
        persist_parallel_run_stop_receipt(
            full_run,
            event_id="full-smoke-producer-quiesced",
            stopped_reason="completed",
        )
        database = Path(temp_dir) / "capture.sqlite3"
        store = CompletenessStore(database)
        store.initialize()
        scopes = {"active": True, "archived_public": True, "archived_private": True}
        for scan in (1, 2):
            store.record_inventory_scan(
                parent_target_key=parent_target,
                scan_id=f"scan-{scan}",
                observed_at=f"2026-09-01T00:0{scan}:00+00:00",
                thread_ids=["thread-1"],
                scopes=scopes,
                pagination_exhausted=True,
            )
        store.record_child_certificate(parent_target, "thread-1", _full_certificate())
        full_output = io.StringIO()
        with redirect_stdout(full_output):
            full_exit = cli_main(
                [
                    "closeout-parallel-run",
                    "--run-dir",
                    str(full_run),
                    "--completeness-db",
                    str(database),
                    "--parent-target-key",
                    parent_target,
                    "--json",
                ]
            )
        full_payload = json.loads(full_output.getvalue())
        full_ok = bool(
            full_exit == 0
            and full_payload.get("terminal_state") == "full_closed"
            and full_payload.get("full_capture_confirmed") is True
            and full_payload.get("persistence_confirmed") is True
            and (full_run / "audit" / "parallel-run-closeout.json").exists()
        )
        ok = blocked_ok and full_ok
        return {
            "schema": "dcb.parallel-closeout-metadata-smoke.v1",
            "ok": ok,
            "blocked_path_ok": blocked_ok,
            "canonical_full_path_ok": full_ok,
            "terminal_state": payload.get("terminal_state"),
            "full_capture_confirmed": payload.get("full_capture_confirmed"),
            "raw_text_returned": False,
            "participant_names_returned": False,
            "path_output": "omitted",
            "outbound_actions": "disabled",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run_smoke()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print("parallel closeout metadata smoke: " + ("OK" if result["ok"] else "NG"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
