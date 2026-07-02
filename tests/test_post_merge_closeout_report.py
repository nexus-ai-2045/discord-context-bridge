import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_post_merge_closeout_requires_merged_pr(monkeypatch):
    module = load_script_module("post_merge_closeout_report", ROOT / "scripts" / "post_merge_closeout_report.py")

    monkeypatch.setattr(module, "load_pr", lambda pr: ({"state": "OPEN", "mergeCommit": None}, None))
    monkeypatch.setattr(module, "rev_parse", lambda ref: "abc123")

    class Completed:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, cwd=module.ROOT):
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed(stdout="main\n")
        if command[:3] == ["git", "status", "--short"]:
            return Completed(stdout="")
        return Completed()

    monkeypatch.setattr(module, "run", fake_run)

    report = module.build_report("12")

    assert report["overall"] == "error"
    assert report["checks"]["pr_merged"]["status"] == "error"


def test_post_merge_closeout_passes_for_merged_synced_main(monkeypatch):
    module = load_script_module("post_merge_closeout_report_ok", ROOT / "scripts" / "post_merge_closeout_report.py")

    monkeypatch.setattr(module, "load_pr", lambda pr: ({"state": "MERGED", "mergeCommit": {"oid": "abc123"}}, None))
    monkeypatch.setattr(module, "rev_parse", lambda ref: "abc123")
    monkeypatch.setattr(module, "remote_branch_exists", lambda head_ref: (False, None))

    class Completed:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, cwd=module.ROOT):
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed(stdout="main\n")
        if command[:3] == ["git", "status", "--short"]:
            return Completed(stdout="")
        return Completed()

    monkeypatch.setattr(module, "run", fake_run)

    report = module.build_report("12", head_ref="codex/merged-branch")

    assert report["overall"] == "ok"
    assert report["checks"]["main_sync"]["status"] == "ok"
    assert report["checks"]["remote_head_branch_deleted"]["status"] == "ok"


def test_post_merge_closeout_flags_remaining_remote_head_branch(monkeypatch):
    module = load_script_module("post_merge_closeout_report_branch", ROOT / "scripts" / "post_merge_closeout_report.py")

    monkeypatch.setattr(
        module,
        "load_pr",
        lambda pr: (
            {"state": "MERGED", "mergeCommit": {"oid": "abc123"}, "headRefName": "codex/stale-branch"},
            None,
        ),
    )
    monkeypatch.setattr(module, "rev_parse", lambda ref: "abc123")
    monkeypatch.setattr(module, "remote_branch_exists", lambda head_ref: (True, "abc123 refs/heads/codex/stale-branch"))

    class Completed:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(command, cwd=module.ROOT):
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed(stdout="main\n")
        if command[:3] == ["git", "status", "--short"]:
            return Completed(stdout="")
        return Completed()

    monkeypatch.setattr(module, "run", fake_run)

    report = module.build_report("12", head_ref="codex/merged-branch")

    assert report["overall"] == "error"
    assert report["checks"]["remote_head_branch_deleted"]["status"] == "error"
