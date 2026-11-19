# Discord Context Bridge チャット統合・引き継ぎ

recorded_at: 2026-07-25T15:12:31+09:00  
recorded_by: codex  
schema_version: fact-provenance/v1

## この文書の位置づけ

この文書は、`discord-context-bridge` に関する Codex タスク、保存済み会話要約、repo の現行 SSOT、Git/GitHub のライブ状態を、今後このタスクから再開できる形へ統合した引き継ぎである。

現在の active TODO は `ISSUE_LIST.md`、実装順序は `ROADMAP.md` を正本とする。過去の `docs/chat-context-*.md` と過去タスクは履歴証跡であり、現在値としては使わない。

## 取得したタスク

| thread id | タイトル | cwd | 取得状態 | 主な内容 |
|---|---|---|---|---|
| `019f4ea1-51e0-72e1-acfb-b7e6cacfaa83` | レビューと分析 | `<local-dcb-repo>` | partial | repo レビュー、CodeQL、PR #25、運用 automation。後半は `nexus_ai` の横断運用へ拡張しており、DCB固有事項だけ採用 |
| `019f95c6-1670-7bd0-9997-49183d382c0e` | discord-context-bridge レビュー | `<local-nexus-ai>` | partial | public-safe 契約、HTTP auth 認証、safe-store、PR #29、レビュー指摘と修正履歴 |
| `019f8d53-6edc-79d0-8cb0-d6da9ab9d8b7` | 8/1イベント | `<local-nexus-ai>` | partial | Discord 可視DOM取得、限定公開保存、添付保存、外部公開停止線。製品実装とは別の利用実績 |
| `019ed337-0b15-7721-b232-ef4e02731581` | observed-full closeout 実装 | `<local-nexus-ai>` | saved-summary | `observed_full_verified` と `api_full.verified=false` の分離、二重走査・anchor・添付manifest |
| `019f7164-5d61-7c01-b822-d7707d29da26` | full capture gates / Draft PR #27 | `<local-nexus-ai>` | saved-summary | strict/resumable full capture、private artifact 境界、Draft PR #27 |

`partial` は直近ターンとページ情報を取得できたが全ページを完全復元していない状態、`saved-summary` は Codex タスク本文ではなく保存済み rollout 要約から取得した状態を表す。

## 統合した結論

### 実装済み・履歴として閉じたもの

- 専用 repo の SSOT は `<local-dcb-repo>`。
- public-safe core、message found から `bridge-intake` への一本化、append-only observation ledger、fast/full/release gate は実装済み。
- raw 本文・実ユーザー名の visible output、MCP HTTP 認証、safe-store、shell invocation の主要問題は PR #29 の実装系列で扱われた。
- PR #29 は 2026-07-25 13:06 JST に merge 済み。merge commit は `a6d58835222fad064365d213f232d186371ac7f5`。
- `observed-full` は可視範囲の取得保証であり API 全履歴保証ではない。`observed_full_verified` でも `api_full.verified=false` を維持する。

### 現在の実装残務

`ISSUE_LIST.md` の active 項目を引き継ぐ。

1. P1: parser quality、slow test、cache reuse、read-more blocker の測定。
2. P2: `core.py` / `test_core.py` の責務分割、private cache backfill の別設計、docs 階層整理。
3. P4: PR #29 から繰り越した full-capture evidence 再計算、Obsidian 旧index掃除、capture orchestrator の承認/action紐付け・resume state・blocked close、非対応browser route拒否。
4. P3-1: human/live channel の送信 rehearsal。これは人間承認と実チャンネル準備まで blocked。

### ライブ Git / PR 状態

観測時刻: 2026-07-25 15:12 JST。

- `main` HEAD は `3459c31fec1ed345a0166b3c68bfa5940e6b0dee`。
- `main...origin/main` は ahead 0 / behind 0、主 checkout は clean。
- open PR は 0 件。
- PR #27 `Discord全文取得を自律実行・厳格判定する` は 2026-07-25 15:36 JST に未mergeで close 済み。branch/worktree の保全・再利用可否は別確認が必要。
- PR #28 Dependabot actions update は 2026-07-25 15:35 JST に merge 済み。merge commit は `3459c31fec1ed345a0166b3c68bfa5940e6b0dee`。
- PR #29 は merge 済み。必須 CI は成功していたが、merge 前の status rollup には別の `CodeQL` failure も残る。失敗理由と現在の main 解析結果は未確認。
- worktree はこの引き継ぎ用 worktreeを含め8本。PR #27 対応 worktreeのほか、P4 review、capture loop、architecture docs、operational closeout、security refactor、contracts task の各 lane が残る。削除可否は各 branch の未反映差分を確認するまで unknown。

## 重複排除

- 「新しい引き継ぎ台帳」は作らず、この文書を既存 `docs/` の履歴文書として置き、現在値は `ISSUE_LIST.md` / `ROADMAP.md` へ委ねる。
- PR #29 以前の CRITICAL/HIGH 指摘は、過去レビュー結果として保持するが、現行 main に残っているとは断定しない。
- `observed-full`、`full-capture`、`API full` を同義にしない。
- `repo_goal_status` の residual は運用ゲートの残差であり、ロードマップの active TODO 件数ではない。
- Discord取得の利用実績と、DCB製品実装の完了判定を混ぜない。

## 次の安全な実行順

1. close 済み PR #27 の branch/worktreeを current main と比較し、PR #29/P4 と重複する変更を除いて保全・再利用・cleanupを判断する。
2. P4 を小さな修正単位へ分け、bound evidence と orchestrator state machine を優先する。
3. P1/P2 の性能・分割は、安全契約の回帰テストを先に固定してから進める。
4. live Discord rehearsal、Discord送信、reaction、edit、delete、release、公開、visibility変更、外部告知は、対象と可視範囲を示したうえで現在会話の明示承認を待つ。

## unknown

- PR #29 rollup 内の非必須 `CodeQL` failure の正確な原因と、merge後 main の再解析結果。
- 残存5 worktreeそれぞれの dirty / ahead / upstream / PR対応と、安全な cleanup 可否。
- close 済み PR #27 の branchが PR #29 とどこまで重複し、どこが独自価値として残るか。
- human/live send rehearsal を行う対象チャンネル、確認者、サンプル本文。
