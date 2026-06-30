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


def test_parse_github_remote_accepts_https_and_ssh():
    module = load_script_module("pr_readiness_preflight", ROOT / "scripts" / "pr_readiness_preflight.py")

    assert module.parse_github_remote("https://github.com/nexus-ai-2045/discord-context-bridge.git") == (
        "nexus-ai-2045",
        "discord-context-bridge",
    )
    assert module.parse_github_remote("git@github.com:nexus-ai-2045/discord-context-bridge.git") == (
        "nexus-ai-2045",
        "discord-context-bridge",
    )


def test_pr_base_selection_error_is_human_readable(monkeypatch):
    module = load_script_module("pr_readiness_preflight_base", ROOT / "scripts" / "pr_readiness_preflight.py")

    class Completed:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, cwd=module.ROOT):
        joined = " ".join(command)
        if command[:4] == ["git", "remote", "get-url", "origin"]:
            return Completed(stdout="https://github.com/nexus-ai-2045/discord-context-bridge.git\n")
        if command[:3] == ["git", "branch", "--show-current"]:
            return Completed(stdout="codex/example\n")
        if command[:3] == ["git", "status", "--short"]:
            return Completed(stdout="")
        if command[:4] == ["gh", "api", "user", "--jq"]:
            return Completed(stdout="nexus-ai-2045\n")
        if command[:3] == ["gh", "repo", "view"]:
            return Completed(stdout="main\n")
        if command[:3] == ["git", "rev-parse", "--verify"]:
            return Completed(stdout="abc123\n")
        if joined == "git merge-base --is-ancestor origin/main HEAD":
            return Completed(returncode=1)
        return Completed()

    monkeypatch.setattr(module, "run", fake_run)

    report = module.build_report()

    assert report["overall"] == "error"
    assert report["checks"]["pr_base_selection"]["status"] == "error"
    assert "基準ブランチ" in report["checks"]["pr_base_selection"]["detail"]
