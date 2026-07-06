# Discord Context Bridge roadmap

Updated: 2026-07-07

## North Star

Discord 側では対象を見つけるだけにする。
DCB 側では、見つけた対象から local snapshot / append-only observation ledger / metadata-only report / context passport / reply guide / send closeout へ進み、人間が安全に判断できる状態を 1-3 秒台で返す。

この repo は送信ロボットではない。
Discord send、reaction、edit、delete、外部投稿、repository visibility 変更は、現在会話での明示承認と人間レビューまで止める。

## Product Premise Review

結論: Yellow

方向性は合っているが、「残務ゼロ」と「ロードマップ上の未完了」を混ぜると判断前提が汚れる。

| Premise | Status | Evidence gap |
|---|---|---|
| public-safe core は local verification で安定している | accepted | `repo_goal_status.py --run-smoke`、pytest、ops check は green |
| ユーザー価値は message found -> bridge intake -> reply support | accepted | README / chat-context / current user request が一致 |
| 現状は遅い | accepted | `ops_check.py --skip-http` が約16-17秒、旧目標10秒未達 |
| live Discord rehearsal まで完了している | rejected | 実チャンネルでの human send log は未実施 |
| すべての将来roadmapが完了している | rejected | M1/M2/M3/P3 が active |

## Current State

| Area | Status | Note |
|---|---|---|
| M0 public-safe core | done | residual dashboard / tests / safety boundary green |
| M1 message found -> bridge intake | active | 個別コマンドはあるが最短導線がまだ弱い |
| M2 fast upstream loop | active | full ops check が16-17秒。fast gateが必要 |
| M3 parser quality | active | fixture品質指標と実サンプル評価が不足 |
| M4 maintainability | active | `core.py` と `test_core.py` が大きい |
| M5 send rehearsal E2E | blocked | human/live channel approval and log needed |
| M6 operator UX | active | README / ROADMAP / ISSUE_LIST の役割を整理中 |
| M7 production send runbook | later | M5後 |
| M8 optional private adapters | later | public-safe boundaryを維持して別lane |
| M9 release/package | later | public visibility / release approval は別gate |

## Roadmap

### M0: Stable Public-Safe Core

Status: done, but keep guarded.

Done condition:

- `python scripts/repo_goal_status.py --run-smoke --json` が `state=done`。
- `python -m pytest tests -q` が green。
- `python scripts/ops_check.py --skip-http` が green。
- raw Discord text、参加者名、secret、local path、outbound action が visible output に出ない。

### M1: Message Found -> Bridge Intake

Goal: Discordで対象を見つけた後、URL / visible text / safe label を渡すだけで次へ進める。

Deliverables:

- `snapshot-discord-url-text`、`coverage-report`、`report-latest`、`context-passport`、`guide-reply` を一本の短い導線にする。
- stale / missing / duplicate / changed を人間語で返す。
- append-only observation ledger を正本にし、latest report は projection として扱う。

Done condition:

- fixtureで、対象URLから snapshot保存、coverage、context passport、reply guide まで通る。
- stdout は metadata-only。
- 失敗時は `read_more`, `snapshot_missing`, `raw_cache_missing`, `stale` などの reason を返す。

### M2: Fast Upstream Loop

Goal: 通常開発と実運用の待ち時間を短くする。

Targets:

| Gate | Target | Current evidence |
|---|---:|---|
| intake/report fast path | <= 1s | smoke は個別2秒前後 |
| context/reply from saved snapshot | <= 3s | 要測定 |
| send gate check | <= 1s | 要測定 |
| `ops_check --profile fast` | <= 5s | 未実装 |
| `ops_check --profile full` | <= 20s | 現状約16-17s |

Deliverables:

- `ops_check.py` を `fast` / `full` / `release` profile に分ける。
- slow tests の top 20 を継続表示する。
- parse時間、入力サイズ、抽出件数、cache reuse、blocked reason を metadata-only で出す。

### M3: Conversation Parser Quality

Goal: 周辺会話から、目的、前提、参加者ロール、温度感、ルール、返信前の一点確認を安定して返す。

Done condition:

- fixtureで必須field充足率を測る。
- 実サンプルは raw本文をpublic出力せず private snapshot / manifest に閉じる。
- `read-more` blocker が人間語で説明できる。

### M4: Maintainability Refactor

Goal: 速度改善と変更容易性のために責務を分ける。

Targets:

- `src/discord_context_bridge/core.py` を URL intake、snapshot ledger、context/reply、send closeout、audit/redaction に分ける。
- `tests/test_core.py` を feature別に分割する。
- generated runtime skills は `scripts/export_runtime_skills.py` 由来に保つ。

Done condition:

- public API compatibility を保つ。
- tests / ops check green。
- PR差分は原則12ファイル以内。

### M5: Send Rehearsal E2E

Goal: 自動送信ではなく、人間送信の前後を実ログで閉じる。

Done condition:

- test channel safe label が決まる。
- `stage-discord-send`、fill dry-run、human send、`closeout-discord-send`、`send-operation-status` が1本につながる。
- 失敗時回復は自動delete/editではなく、人間判断の停止、追記、修正投稿、再送として記録される。

### M6-M9: Operator UX / Production / Optional Adapter / Release

M5後に進める。

- READMEを最短手順に保つ。
- production runbook を human approval 用に固定する。
- private adapter は public package と切り離す。
- release / public visibility / announcement は publication gate で止める。

## Active TODO Source

Active TODO は `ISSUE_LIST.md` を正本にする。
古い closeout docs は履歴証跡であり、現在の roadmap 完了判定には使わない。

## Stopline

- Discord send、auto reply、reaction、edit、delete は実装しない。
- GitHub push、PR、public visibility、release、外部投稿は人間レビューと明示承認まで止める。
- raw Discord text、参加者名、実URL、snowflake、token、cookie、webhook、local absolute path は public-safe output に出さない。
