# 公開準備状況

`discord-context-bridge` の公開前レビュー用記録です。この文書だけで公開可とは
判定せず、`PUBLIC_RELEASE_CHECKLIST.md` と現在の GitHub 状態を併せて確認します。

- `schema_version`: `fact-provenance/v1`
- `recorded_at`: `2026-08-07T22:59:03+09:00`
- `recorded_by`: `grok`
- `tip_sha`: `4b6502943e21bbc600cd59d7cf0d40b8d26ee8e1`
- `tip_short`: `4b65029` (`docs: ops release-gate closeout を記録する (#55)`)

## ローカル確認

| 主張 | source | actor | event_time | observed_at | scope |
|---|---|---|---|---|---|
| 必須公開文書が存在する | Git tree `4b65029` | git | commit時刻 | 2026-08-07T22:59:03+09:00 | `README.md`、`SECURITY.md`、`LICENSE`、`PUBLIC_RELEASE_CHECKLIST.md`、`PREFLIGHT.md` |
| package version / CHANGELOG / tag 検査が整合 | `python scripts/bump_version.py --check` | grok | 2026-08-07T22:58:00+09:00 | 2026-08-07T22:58:00+09:00 | `pyproject.toml` version=`0.11.0`（GitHub Release / `v0.11.0` tag は未作成） |
| 全テストが成功した | `python -m pytest tests -q` | grok | 2026-08-07T23:05:00+09:00 | 2026-08-07T23:05:00+09:00 | `760 passed` / tip worktree |
| gh_guard が公開先と一致 | `python scripts/gh_guard.py --json --history-ref HEAD` | grok | 2026-08-07T22:58:00+09:00 | 2026-08-07T22:58:00+09:00 | owner=`nexus-ai-2045`、active account 一致、forbidden identity 0 |
| SSOT projection が ok | `python scripts/verify_ssot_projection.py` | grok | 2026-08-07T23:00:00+09:00 | 2026-08-07T23:00:00+09:00 | overall=ok / generated skills 4 |
| residual dashboard が done | `python scripts/repo_goal_status.py` | grok | 2026-08-07T23:00:00+09:00 | 2026-08-07T23:00:00+09:00 | `state=done` / `residual_count=0` |
| ops release-gate closeout 済み | `PREFLIGHT.md` + PR #55 | prior session | 2026-08-07 | 2026-08-07T22:59:03+09:00 | host skill sync / release profile 証跡は PREFLIGHT 参照 |

## GitHub側確認

| 主張 | source | actor | event_time | observed_at | scope |
|---|---|---|---|---|---|
| `main` tip の CI が成功した | GitHub Actions API | GitHub Actions | 2026-08-07T13:46:39Z | 2026-08-07T22:58:00+09:00 | run `31184266910` / head `4b65029` / CI success |
| `main` tip の CodeQL が成功した | GitHub Actions API | GitHub Actions | 2026-08-07T13:46:39Z | 2026-08-07T22:58:00+09:00 | run `31184266926` / head `4b65029` / CodeQL success |
| Secret scanning open alertは0件 | GitHub REST API | GitHub | unknown | 2026-08-07T22:58:00+09:00 | `nexus-ai-2045/discord-context-bridge` |
| Code scanning open alertは0件 | GitHub REST API | GitHub | unknown | 2026-08-07T22:58:00+09:00 | `nexus-ai-2045/discord-context-bridge` |
| GitHub Release は未作成 | `gh release list` | grok | 2026-08-07T22:58:00+09:00 | 2026-08-07T22:58:00+09:00 | remote tags は `v0.2.0`〜`v0.4.0` のみ（`v0.11.0` なし） |

## 運用ゲートとの関係

- 運用残務（ops residual）: PR #51–#55 で closeout。`repo_goal_status` residual 0 は**実装の安全状態**。
- 製品残（ISSUE_LIST P1/P2）: 運用ゼロとは**別次元**。Release ブロッカーではない。
- production live monitoring / Discord send rehearsal: 別判断・別承認。

## 未完了と人間判断

- release version は package 上 `0.11.0`。`v0.11.0` tag と GitHub Release は**未作成**。
- tag / GitHub Release 作成には、現在会話での明示承認（バージョン番号 + Release 作成）が必要。
- `LICENSE` の `Copyright (c) 2026 yas` を維持するか、組織名義へ変えるかは法的帰属を含むため人間判断が必要。
- Discord への自動送信・reaction・visibility 変更は、この release prep では実行しない。
- repo-preflight の dep/ci/human 機械 unknown は設計上の仕様。証跡は本ファイルと `PREFLIGHT.md` に置く。

## この更新での追加修正（同一 PR）

- Windows 負荷下で `test_process_runner_stops_unbounded_output_during_execution` が
  wall-clock `< 5s` に依存して flaky になる問題を、bounded drain + 契約を timeout 未満に
  寄せて安定化（failure_stage / returncode / 64KiB truncate は維持）。
