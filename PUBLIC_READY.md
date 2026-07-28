# 公開準備状況

`discord-context-bridge` の公開前レビュー用記録です。この文書だけで公開可とは
判定せず、`PUBLIC_RELEASE_CHECKLIST.md` と現在の GitHub 状態を併せて確認します。

- `schema_version`: `fact-provenance/v1`
- `recorded_at`: `2026-07-28T12:33:28+09:00`
- `recorded_by`: `codex`

## ローカル確認

| 主張 | source | actor | event_time | observed_at | scope |
|---|---|---|---|---|---|
| 必須公開文書が存在する | Git tree `1017f3f` | git | commit時刻 | 2026-07-28T12:33:28+09:00 | `README.md`、`SECURITY.md`、`LICENSE`、`PUBLIC_RELEASE_CHECKLIST.md` |
| 全テストが成功した | `python -m pytest -q` 出力 | codex | 2026-07-28T12:29:00+09:00 | 2026-07-28T12:29:00+09:00 | `620 passed, 6 skipped` |
| release運用チェックが成功した | `ops_check.py --profile release --skip-http --fail-fast` 出力 | codex | 2026-07-28T12:31:00+09:00 | 2026-07-28T12:31:00+09:00 | worktree `1017f3f` |
| package/MCP smokeが成功した | venv installとMCP起動確認 | codex | 2026-07-28T12:32:00+09:00 | 2026-07-28T12:32:00+09:00 | `.[mcp]`、stdio server、HTTP help |
| 個人パスとcredential形式を走査した | checklist記載の `rg` と追加 `git grep` | codex | 2026-07-28T12:31:00+09:00 | 2026-07-28T12:31:00+09:00 | tracked tree |
| Discord ID形式のテスト定数を合成値へ置換した | このbranchのdiff | codex | 2026-07-28T12:33:28+09:00 | 2026-07-28T12:33:28+09:00 | 2 test files |

## GitHub側確認

| 主張 | source | actor | event_time | observed_at | scope |
|---|---|---|---|---|---|
| `main` の CI と CodeQL が成功した | GitHub Actions API | GitHub Actions | 2026-07-28T12:26:53+09:00 | 2026-07-28T12:29:00+09:00 | merge commit `1017f3f` |
| Secret scanning open alertは0件 | GitHub REST API | GitHub | unknown | 2026-07-28T12:31:00+09:00 | `nexus-ai-2045/discord-context-bridge` |
| Code scanning open alertは0件 | GitHub REST API | GitHub | unknown | 2026-07-28T12:31:00+09:00 | `nexus-ai-2045/discord-context-bridge` |
| Secret scanningとpush protectionは有効 | GitHub repository API | GitHub | unknown | 2026-07-28T12:26:00+09:00 | repository settings |

## 未完了と人間判断

- release version は未確定です。`Unreleased` の機能追加を含むため `0.11.0` を推奨します。
- `v0.11.0` tag と GitHub Release は未作成です。
- `LICENSE` の `Copyright (c) 2026 yas` を維持するか、組織名義へ変えるかは法的帰属を含むため人間判断が必要です。
- release notes、公開される差分、名義、合成ID置換を人間が確認し、現在会話で明示承認してから作成します。
