# Discord Context Bridge active TODO

Updated: 2026-07-09

このファイルを、リポジトリ内の active TODO 正本にする。
過去の `docs/chat-context-*.md` と `docs/2026-07-01-*` は履歴証跡であり、現在の残務判断ではこのファイルと `ROADMAP.md` を優先する。

## 最終目的

Discord 側では対象メッセージ、スレッド、チャンネルを見つけるだけにし、DCB 側で保存済み文脈、差分、返信前判断、下書きレビュー、人間送信後 closeout を高速に扱う。

既定境界は read-only / local-first / public-safe。Discord send、reaction、edit、delete、repo visibility 変更、外部投稿は、この repo の自動経路に含めない。

## 現在の実測

| 項目 | 状態 | 根拠 |
|---|---|---|
| repo sync | ok | `main...origin/main`, ahead/behind `0 0`（作業中 commit がある場合はその commit を正とする） |
| open PR | ok | open PR `[]`（2026-07-09 実測） |
| residual dashboard | ok | `repo_goal_status.py` が `state=done`, `residual_count=0` |
| full tests | ok | `python -m pytest tests -q` で green（P0-3 追加後は bridge-intake 含む） |
| ops check | ok | `ops_check.py --profile fast --skip-http` 約 1.05s green。通常開発は fast、PR/closeout は full/release |
| runtime skill sync | ok | `export_runtime_skills.py` 後、Claude / Codex / Grok へ配置。`lint_runtime_skill_sync.py` overall=ok、`ssot_commit` = HEAD |
| local WIP | parked | cache backfill 試作は stash に退避中。active roadmap にはまだ入れない |

注: `repo_goal_status` の residual zero は「現在実装の安全状態」であり、本ファイルの active TODO 完了ではない。

## P0: 表に出ている問題

| ID | 状態 | TODO | 完了条件 | 検証 |
|---|---|---|---|---|
| P0-1 | improved | README / ROADMAP / 旧closeout docs の残務表現を揃える | README の残TODO、ROADMAP、過去closeout文書が「現在のactive TODOは ISSUE_LIST」と読める | README は pointer 済み。旧 chat-context に historical banner を追加。細部は P2-4 継続 |
| P0-2 | done | full smoke を現実的に速くする | fast gate と full gate を分け、通常開発では fast gate が5秒以内、full gate が20秒以内 | `ops_check.py --profile fast|full` が green |
| P0-3 | done | message found -> bridge intake を一本化する | URL / visible text / safe label から、snapshot 保存、coverage、context passport、reply guide までの最短コマンドが1つに見える | `bridge-intake` CLI + `build_bridge_intake` + tests |
| P0-4 | done | append-only snapshot ledger を active docs に反映する | snapshot は重複でも観測eventとして追記し、latest report は projection だと ROADMAP / README から分かる | README「保存モデル」+ ROADMAP M1b + report-latest / operating-contract 参照 |
| P0-5 | improved | stale closeout の「残務0」を誤読させない | `docs/2026-07-01-*` / 旧 chat-context は historical と明示され、active TODO と混ざらない | 2026-07-01 系は banner 済み。2026-07-08 chat-context に historical banner 追加 |
| P0-6 | done | runtime skill を SSOT HEAD と同期する | dist 生成物と Claude/Codex/Grok local skill が同一 checksum / ssot_commit | `lint_runtime_skill_sync.py` overall=ok |

## P1: 高速化と品質

| ID | 状態 | TODO | 完了条件 | 検証 |
|---|---|---|---|---|
| P1-1 | done | `ops_check.py` に profile を入れる | `fast`: compile / diff / boundary / targeted tests / smoke、`full`: 全pytest / dashboard / projection を分離 | fast / full / release profile を実装 |
| P1-2 | active | slow tests を短縮する | `test_discord_inventory_dashboard_omits_sensitive_values` など上位slow testsの待ち時間をfixture化・分離 | `pytest --durations=20` の最上位が1秒台以下 |
| P1-3 | active | context parser quality を測る | `context-passport` / `guide-reply` の必須field充足率を fixture で測る | metadata-only quality report |
| P1-4 | active | cache再利用率を測る | target_key一致時に再parse回避率が出る | `cache_reuse_rate` metadata |
| P1-5 | active | read-more 誤停止を測る | blocker reason と人間判断結果を保存できる | sample ledger / fixture |

## P2: 保守性と設計負債

| ID | 状態 | TODO | 完了条件 | 検証 |
|---|---|---|---|---|
| P2-1 | active | `core.py` を責務ごとに分割する | URL intake、snapshot ledger、context/reply、send closeout が別moduleに分かれる | public API互換のまま tests green |
| P2-2 | active | `test_core.py` をfeature別に分割する | slow / failure-localization が改善する | `pytest --durations=20` とCIログ |
| P2-3 | active | local-private cache backfill を別設計にする | stash中の試作は実ID風fixtureとlocal path保存方針を見直してから採用判断 | separate PR、secret/path scan |
| P2-4 | active | docsの階層を整理する | README =入口、ROADMAP =順序、ISSUE_LIST =active TODO、chat-context =履歴、full-reference =詳細 | link checker / grep |

## P3: 送信 rehearsal と任意 adapter

| ID | 状態 | TODO | 完了条件 | 検証 |
|---|---|---|---|---|
| P3-1 | blocked by human/live env | テスト用チャンネルで人間送信 rehearsal を行う | `stage-discord-send`、dry-run、human send、closeout、send-operation-status が1本の実ログで閉じる | human-approved live log |
| P3-2 | later | production send runbook を実運用で固定する | safe label、失敗時回復、送信後確認者が決まる | runbook review |
| P3-3 | later | optional private adapters を採用判断する | public repoにcredential/raw本文を入れず、failure reason だけを出す | private adapter smoke |

## すぐ直す順序

1. P1-3: context parser quality を fixture で測る。
2. P2-1 / P2-2: `core.py` と `test_core.py` を分割し、速度と見通しを上げる。
3. P1-2 / P1-4: slow tests 短縮と cache reuse 測定。
4. P3-1: live rehearsal は人間承認と実チャンネル準備後に別実行する。

## Stopline

- active TODO を「残務0」と言わない。`repo_goal_status` の residual zero は現在実装の安全状態であり、将来roadmap完了ではない。
- Discord send、reaction、edit、delete は自動化しない。
- repository public 化、release、外部告知は別の明示承認まで実行しない。
- raw Discord text、参加者名、実URL、snowflake、local absolute path、secret-like 文字列は public-safe docs / stdout / PR本文に出さない。
