# Chat Context 2026-07-08: residual zero account boundary

> Historical note: このファイルは 2026-07-08 時点の引き継ぎ証跡です。
> 現在の active TODO 正本は repo root の `ISSUE_LIST.md`、順序正本は `ROADMAP.md` です。
> 下記の open PR 件数・residual 判定は当時の snapshot であり、現在の open PR / residual 状態としては使わないでください。

このメモは、2026-07-08 の `discord-context-bridge` 残務ゼロ確認と account boundary 修正の文脈を保存するための引き継ぎです。
raw Discord text、実 Discord URL、snowflake、参加者名、token、cookie、webhook、local absolute path は含めません。

## 今回の確認

ユーザーは、チャット残務ゼロ、実装完了、運用保証、context 保存、仕組み化候補、commit / push までの確認を依頼した。

最初の残務判定では、GitHub PR 状態取得が失敗した。原因は `gh` active account が repository owner とずれていたためで、実装や smoke の失敗ではなかった。

## 修正したこと

- `discord_inventory_dashboard.py` の open PR 取得前に `gh_guard.py --account-only --json` を通すようにした。
- これにより、active account drift がある時は dashboard が PR 状態取得を止め、明示修復を促せる。
- 回帰テストで、account guard が `gh pr list` より先に呼ばれることを固定した。

## 現在の残務レイヤー

ローカル実装と運用 smoke は完了扱いにできる。

repo-wide には open PR が 3 件あるため、`repo_goal_status.py --run-smoke --json` は residual zero にはならない。これは GitHub 認証エラーではなく、人間レビュー / merge / close 判断が必要な open PR 残務として扱う。

| PR | 状態 | 意味 |
|---:|---|---|
| #3 | Open | 完全保存ゲート。人間レビュー後に merge / revise / close 判断が必要 |
| #4 | Draft | REST backfill。#3 の後に扱う stacked PR |
| #5 | Draft | Chrome visible fallback guard。main 向けだが draft のままレビュー待ち |

## システムアーキテクチャ視点

残務ゼロ判定は 2 層に分ける。

1. current branch closeout: worktree clean、origin/main 同期、tests / ops / boundary checks green。
2. repo-wide lifecycle: open PR、draft PR、stacked PR、human review、merge / close 判断。

この 2 層を混ぜると、ローカル実装完了を repo 全体完了と誤認する。dashboard は account boundary を自動検知し、open PR は見える残務として出すのが正しい。

## 次に再開する時

1. `python3 scripts/repo_goal_status.py --run-smoke --json` を実行する。
2. `github.open_pr_count` が 0 でなければ、PR 番号、draft 状態、base/head、CI 状態を分けて見る。
3. merge は `PR Merge Permission Gate` を通し、人間レビューまたは明示 waiver なしに実行しない。
4. GitHub 操作前は `python3 scripts/gh_guard.py --account-only --json` で境界を確認し、ずれていた場合だけ `python3 scripts/gh_guard.py --switch --json` で明示修復する。
