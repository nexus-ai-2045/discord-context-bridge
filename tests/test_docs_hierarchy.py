import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def repository_markdown_files() -> list[Path]:
    paths = list(ROOT.glob("*.md"))
    for directory in ("docs", "references", "dist"):
        root = ROOT / directory
        if root.is_dir():
            paths.extend(root.rglob("*.md"))
    return sorted(paths)


def test_document_hierarchy_roles_are_explicit():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    issue_list = (ROOT / "ISSUE_LIST.md").read_text(encoding="utf-8")
    full_reference = (ROOT / "docs" / "full-reference.md").read_text(encoding="utf-8")

    assert "[詳細リファレンス](docs/full-reference.md)" in readme
    assert "[ロードマップ](ROADMAP.md)" in readme
    assert "[Issue一覧](ISSUE_LIST.md)" in readme
    assert "Active TODO は `ISSUE_LIST.md` を正本にする" in roadmap
    assert "このファイルを、リポジトリ内の active TODO 正本にする" in issue_list
    assert "## 最短の使い方" in full_reference
    assert "## 使い方" in full_reference


def test_historical_docs_point_to_current_todo_and_roadmap():
    historical_docs = sorted((ROOT / "docs").glob("chat-context-*.md"))
    historical_docs += sorted((ROOT / "docs").glob("2026-07-01-*.md"))

    assert historical_docs
    for path in historical_docs:
        body = path.read_text(encoding="utf-8")
        assert "Historical note" in body, path
        assert "ISSUE_LIST.md" in body, path
        assert "ROADMAP.md" in body, path


def test_primary_document_inline_markdown_links_resolve():
    broken_links: list[str] = []

    for path in repository_markdown_files():
        body = path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(body):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            parsed = urlsplit(target)
            if not target or target.startswith("#") or parsed.scheme or parsed.netloc:
                continue

            relative_target = unquote(target.split("#", 1)[0])
            if relative_target and not (path.parent / relative_target).exists():
                broken_links.append(f"{path.relative_to(ROOT)} -> {target}")

    assert not broken_links, "broken Markdown links:\n" + "\n".join(broken_links)
