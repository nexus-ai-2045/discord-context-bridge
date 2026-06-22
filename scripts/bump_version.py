from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r'(?m)^(version\s*=\s*)"([^"]+)"')
RELEASE_HEADING_RE = re.compile(r"(?m)^##\s+(\d+\.\d+\.\d+)(?:\s+-\s+\d{4}-\d{2}-\d{2})?\s*$")
UNRELEASED_HEADING_RE = re.compile(r"(?m)^## Unreleased\s*$")


def parse_semver(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"semver は X.Y.Z 形式で指定してください: {value}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def format_semver(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def bump_semver(current: str, part: str) -> str:
    major, minor, patch = parse_semver(current)
    if part == "major":
        return format_semver((major + 1, 0, 0))
    if part == "minor":
        return format_semver((major, minor + 1, 0))
    if part == "patch":
        return format_semver((major, minor, patch + 1))
    raise ValueError(f"未対応の version part です: {part}")


def read_project_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if not match:
        raise ValueError("pyproject.toml に project version が見つかりません。")
    return match.group(2)


def write_project_version(pyproject_path: Path, new_version: str) -> None:
    text = pyproject_path.read_text(encoding="utf-8")
    updated, count = VERSION_RE.subn(rf'\g<1>"{new_version}"', text, count=1)
    if count != 1:
        raise ValueError("pyproject.toml の project version を一意に更新できません。")
    pyproject_path.write_text(updated, encoding="utf-8", newline="\n")


def release_versions_from_changelog(changelog_path: Path) -> list[str]:
    text = changelog_path.read_text(encoding="utf-8")
    return [match.group(1) for match in RELEASE_HEADING_RE.finditer(text)]


def update_changelog(changelog_path: Path, new_version: str, release_date: str) -> None:
    text = changelog_path.read_text(encoding="utf-8")
    unreleased = UNRELEASED_HEADING_RE.search(text)
    if not unreleased:
        raise ValueError("CHANGELOG.md に '## Unreleased' が見つかりません。")

    next_heading = re.search(r"(?m)^##\s+", text[unreleased.end() :])
    if next_heading:
        body_start = unreleased.end()
        body_end = unreleased.end() + next_heading.start()
        after = text[body_end:]
    else:
        body_start = unreleased.end()
        body_end = len(text)
        after = ""

    body = text[body_start:body_end].strip()
    released_body = body if body else "- バージョン採番のみ。"
    new_unreleased = "## Unreleased\n\n- 次の変更をここに記録します。"
    release_heading = f"## {new_version} - {release_date}"
    updated = text[: unreleased.start()] + f"{new_unreleased}\n\n{release_heading}\n\n{released_body}\n\n" + after.lstrip()
    changelog_path.write_text(updated, encoding="utf-8", newline="\n")


def latest_git_tag_version(root: Path) -> str | None:
    completed = subprocess.run(
        ["git", "tag", "--list", "v[0-9]*"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    versions = []
    for line in completed.stdout.splitlines():
        value = line.strip().removeprefix("v")
        try:
            versions.append(parse_semver(value))
        except ValueError:
            continue
    if not versions:
        return None
    return format_semver(max(versions))


def check_version_consistency(root: Path) -> list[str]:
    pyproject_path = root / "pyproject.toml"
    changelog_path = root / "CHANGELOG.md"
    issues: list[str] = []
    current = read_project_version(pyproject_path)
    changelog_versions = release_versions_from_changelog(changelog_path)
    if current not in changelog_versions:
        issues.append(f"CHANGELOG.md に current version {current} の release 見出しがありません。")
    if "## Unreleased" not in changelog_path.read_text(encoding="utf-8"):
        issues.append("CHANGELOG.md に '## Unreleased' がありません。")
    latest_tag = latest_git_tag_version(root)
    if latest_tag and parse_semver(current) < parse_semver(latest_tag):
        issues.append(f"pyproject.toml version {current} が最新 git tag v{latest_tag} より古いです。")
    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pyproject と CHANGELOG の version を自動採番します。")
    parser.add_argument("--part", choices=("patch", "minor", "major"), default="patch", help="現在 version から増やす単位")
    parser.add_argument("--version", help="自動計算ではなく、この X.Y.Z を使う")
    parser.add_argument("--date", default=date.today().isoformat(), help="CHANGELOG に書く release date")
    parser.add_argument("--write", action="store_true", help="pyproject.toml と CHANGELOG.md を更新する")
    parser.add_argument("--check", action="store_true", help="version と CHANGELOG の整合性だけ確認する")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        issues = check_version_consistency(ROOT)
        if issues:
            for issue in issues:
                print(f"NG: {issue}")
            return 1
        print("OK: pyproject.toml version / CHANGELOG / git tag が整合しています。")
        return 0

    pyproject_path = ROOT / "pyproject.toml"
    current = read_project_version(pyproject_path)
    next_version = args.version or bump_semver(current, args.part)
    parse_semver(next_version)
    payload = {
        "current_version": current,
        "next_version": next_version,
        "part": args.part if not args.version else "explicit",
        "date": args.date,
        "write": args.write,
    }
    if args.write:
        write_project_version(pyproject_path, next_version)
        update_changelog(ROOT / "CHANGELOG.md", next_version, args.date)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
