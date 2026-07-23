import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pdca_e2e_inventory


def _result(*, returncode: int, payload: dict | None = None, failure_stage: str | None = None):
    return pdca_e2e_inventory.ExecutionResult(
        returncode=returncode,
        stdout=json.dumps(payload or {}),
        stderr="private error omitted",
        failure_stage=failure_stage,
        elapsed_seconds=0.01,
    )


def _spec(case_id: str = "case"):
    return pdca_e2e_inventory.CaseSpec(case_id, "test", ("python",), 1)


def test_classifies_projection_stopline_as_human_review() -> None:
    payload = {
        "overall": "error",
        "checks": {"provenance": {"status": "error"}},
    }
    classified = pdca_e2e_inventory.classify_case(_spec("projection"), _result(returncode=2, payload=payload))
    assert classified["classification"] == "human_review_required"
    assert classified["failure_signal"] == "projection_provenance_invalid"
    assert "commit" in classified["next_action"]


def test_classifies_snapshot_missing_as_partial_evidence() -> None:
    classified = pdca_e2e_inventory.classify_case(
        _spec("cache"),
        _result(returncode=0, payload={"ok": True, "state": "snapshot_missing"}),
    )
    assert classified["classification"] == "partial_evidence"
    assert classified["retryable"] is False


def test_classifies_unconfigured_main_route_as_environment_blocked() -> None:
    classified = pdca_e2e_inventory.classify_case(
        _spec("route_fixture"),
        _result(returncode=2, payload={"ok": False, "failure_stage": "main_route_not_ready"}),
    )
    assert classified["classification"] == "environment_blocked"
    assert "route" in classified["next_action"]


def test_retries_timeout_once_but_not_human_gate() -> None:
    calls = []

    def transient_executor(spec):
        calls.append(spec.case_id)
        if len(calls) == 1:
            return _result(returncode=124, failure_stage="timeout")
        return _result(returncode=0, payload={"ok": True})

    passed = pdca_e2e_inventory.run_case(_spec("transient"), executor=transient_executor, max_retries=1)
    assert passed["classification"] == "passed"
    assert passed["attempt_count"] == 2

    human_calls = []

    def human_executor(spec):
        human_calls.append(spec.case_id)
        return _result(
            returncode=2,
            payload={"checks": {"provenance": {"status": "error"}}},
        )

    blocked = pdca_e2e_inventory.run_case(_spec("human"), executor=human_executor, max_retries=1)
    assert blocked["classification"] == "human_review_required"
    assert blocked["attempt_count"] == 1


def test_previous_report_delta_splits_resolved_persisting_and_new() -> None:
    previous = {
        "cases": [
            {"case_id": "fixed", "classification": "code_repair_required"},
            {"case_id": "still", "classification": "human_review_required"},
        ]
    }
    current = [
        {"case_id": "fixed", "classification": "passed"},
        {"case_id": "still", "classification": "human_review_required"},
        {"case_id": "new", "classification": "environment_blocked"},
    ]
    assert pdca_e2e_inventory.compare_previous(current, previous) == {
        "resolved": ["fixed"],
        "persisting": ["still"],
        "new_issues": ["new"],
    }


def test_dry_run_lists_all_cases_without_url_or_paths(capsys) -> None:
    result = pdca_e2e_inventory.main(["--dry-run", "--json"])
    rendered = capsys.readouterr().out
    payload = json.loads(rendered)
    assert result == 0
    assert payload["case_count"] == 8
    assert {case["case_id"] for case in payload["cases"]} >= {
        "fixture_13_step",
        "ops_fast",
        "ssot_projection",
        "cache_inventory",
    }
    assert str(ROOT) not in rendered
    ops = next(case for case in payload["cases"] if case["case_id"] == "ops_fast")
    assert ops["enabled"] is False
    assert ops["skip_reason"] == "nested_orchestrator_not_requested"


def test_ops_fast_is_opt_in_to_avoid_nested_orchestrators() -> None:
    default_specs = pdca_e2e_inventory.build_case_specs(
        python_executable=sys.executable,
        url=None,
        include_desktop_cache=False,
    )
    explicit_specs = pdca_e2e_inventory.build_case_specs(
        python_executable=sys.executable,
        url=None,
        include_desktop_cache=False,
        include_ops_fast=True,
    )
    assert next(spec for spec in default_specs if spec.case_id == "ops_fast").enabled is False
    assert next(spec for spec in explicit_specs if spec.case_id == "ops_fast").enabled is True


def test_cache_inventory_case_only_uses_supported_cli_flags() -> None:
    specs = pdca_e2e_inventory.build_case_specs(
        python_executable=sys.executable,
        url="https://discord.com/channels/11111111111111111/22222222222222222",
        include_desktop_cache=False,
    )
    command = next(spec.command for spec in specs if spec.case_id == "cache_inventory")
    assert "--max-scan-seconds" not in command


def test_execute_case_preserves_src_for_child_imports(monkeypatch) -> None:
    captured = {}

    def fake_run_process(command, *, cwd, env, timeout):
        captured.update(command=command, cwd=cwd, env=env, timeout=timeout)
        return type("Result", (), {"returncode": 0, "stdout": "{}", "stderr": "", "failure_stage": None})()

    monkeypatch.setattr(pdca_e2e_inventory, "run_process", fake_run_process)
    pdca_e2e_inventory.execute_case(_spec("child"))
    assert str(ROOT / "src") in captured["env"]["PYTHONPATH"].split(pdca_e2e_inventory.os.pathsep)


def test_json_case_rejects_unparseable_or_explicit_failure_payload() -> None:
    invalid = pdca_e2e_inventory.ExecutionResult(0, "not-json", "", None, 0.01)
    classified_invalid = pdca_e2e_inventory.classify_case(_spec("invalid"), invalid)
    assert classified_invalid["classification"] == "code_repair_required"
    assert classified_invalid["failure_signal"] == "invalid_json_output"

    classified_failure = pdca_e2e_inventory.classify_case(
        _spec("blocked"),
        _result(returncode=0, payload={"ok": False, "state": "blocked"}),
    )
    assert classified_failure["classification"] == "code_repair_required"


def test_report_is_metadata_only() -> None:
    report = pdca_e2e_inventory.build_report(
        [
            {
                "case_id": "one",
                "classification": "code_repair_required",
                "command_output": "omitted",
                "local_paths_output": "omitted",
            }
        ]
    )
    rendered = json.dumps(report, ensure_ascii=False)
    assert report["overall"] == "failed"
    assert "private error" not in rendered
    assert report["safety"]["discord_write"] == "disabled"
