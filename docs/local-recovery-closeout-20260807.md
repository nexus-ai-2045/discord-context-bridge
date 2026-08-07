# Local recovery closeout (2026-08-07)

recorded_by: grok  
schema_version: fact-provenance/v1  
japanese_pr_ok: yes

## 目的

`discord-context-bridge` のローカル話題・worktree・stale remote branch を、main 基準で回収し、重複実装を増やさずに運用状態を閉じる。

## 次元分離（FDE）

| 次元 | この PR で扱う | 扱わない |
|---|---|---|
| 履歴/索引 | 回収結果の metadata-only 記録 | raw Discord 本文 |
| 実装 salvage | 既存 main への吸収済み判定のみ | capture / send / auth の再実装 |
| 公開境界 | public-safe 文書のみ | secret 候補除去・path rewrite（PREFLIGHT 別 PR） |
| runtime skill sync | 未実施（別承認） | local skill 書換え |

車輪の再発明はしない。capture-loop / acquisition-gate / desktop-cache / receipts / residual salvage は PR #45/#48/#49 等で main に到達済み。

## 事実（2026-08-07 実測）

- remote: `nexus-ai-2045/discord-context-bridge`（PUBLIC）
- `origin/main`: recovery 時点の最新（PR #48 residual salvage / #49 receipts / #50 preflight baseline 済み）
- open PR: 0
- 主 checkout dirty: ローカル consolidation notes を private archive へ退避後 clean
- worktree: マージ済み PR #48/#49 用 2 本を削除対象

## ローカル consolidation notes

PREFLIGHT 方針どおり、untracked consolidation notes は public に載せない。

- private archive label: `local-private/archive/2026-08-06-local-consolidation`
- 内容: 2026-08-06 時点の統合管制・live status（stale。現行 worktree 数は 2 本台）
- active SSOT は引き続き `ISSUE_LIST.md` / `ROADMAP.md` / `PREFLIGHT.md`

## Remote branch 回収

実装ファイルは main に存在することを確認済み:

- `acquisition_gate.py`, `desktop_cache.py`, `capture/*`, `full_capture.py`, `scripts/repo_goal_status.py`, `scripts/pdca_e2e_inventory.py`

| branch | 扱い | 証跡 |
|---|---|---|
| `codex/dcb-residual-salvage-20260806` | delete | ahead 0; PR #47 closed, #48 re-landed |
| `codex/dcb-acquisition-completion-gate` | delete after archive tag | PR #38 closed; main に gate あり |
| `codex/dcb-capture-loop-pr-20260728` | delete after archive tag | PR #37 closed; main に capture loop あり |
| `codex/dcb-autonomous-full-capture-20260718` | delete after archive tag | PR #27 closed; main に full_capture/orchestrator あり |
| `codex/dcb-operational-closeout-20260716` | delete after archive tag | NO_PR だが tip を `archive/` tag で保全 |
| `codex/discord-live-cache-adapter-20260713` | delete after archive tag | NO_PR だが tip を `archive/` tag で保全 |

Archive tag 命名: `archive/<branch-slug>`（branch tip SHA を指す annotated tag）

## Worktree / local branch

| 対象 | 扱い |
|---|---|
| `.worktrees/dcb-receipts-20260807` | remove（PR #49 merged, remote gone） |
| `.worktrees/dcb-residual-salvage-20260806` | remove（PR #48 merged, remote gone） |
| local `archive/local-main-bec46cd-20260806` | keep local only（旧 main tip 保全） |
| local `tmp-restore-old-main` | delete local（同一 tip の重複） |
| local merged PR heads | delete after worktree remove |

## 検証コマンド（既存）

```bash
python -m pytest -q
python scripts/ops_check.py --profile fast
python scripts/verify_ssot_projection.py --json
python scripts/repo_goal_status.py --json
```

## 完了条件

1. private archive に 2026-08-06 consolidation を退避し、public tree から除去
2. stale remote branch を archive tag 後に削除し remote は main + tags
3. マージ済み worktree を除去
4. ops_check fast / pytest / SSOT projection が green
5. 本 closeout が main に載る（本 PR）

## 残る別次元（意図的に未着手）

- PREFLIGHT: secret 候補 / personal path / identity policy（別 PR）
- ISSUE_LIST active: P1-2..P1-5, P2-1, P2-2, M3–M5
- runtime skill の host 反映（設定変更・別承認）
- Discord write / live send rehearsal

## 未実行

Discord 送信、repository visibility 変更、history rewrite、secret ignore、新規 capture/send 実装。
