# Discord 全文取得 LOOP 運用カード

## 目的

Discord Context Bridge（DCB）の既存 Orchestrator、全文取得 gate、observed-full 検証を、再開可能な checkpoint と追記専用 event ledger で接続する。本文や Discord URL は status に返さず、外部送信は行わない。

## Plan

- 対象範囲は `dm`、`server_threads`、`thread_only` のいずれかで宣言する。
- 経路は既存の preflight 結果から選ぶ。
- `scan-pass-budget` と `retry-budget` を先に固定する。
- `refresh-check` は既存保存物との差分確認が必要な run に付ける。

## Do

```powershell
discord-context-bridge capture-loop start `
  --target-key "<private Discord URL または opaque key>" `
  --route chrome_extension `
  --upper-watermark "<latest message id>" `
  --scope thread_only `
  --refresh-check `
  --json
```

返された `capture_id` を使い、状態イベントを一意な `event-id` と期待 sequence 付きで進める。

```powershell
discord-context-bridge capture-loop advance `
  --capture-id "<opaque capture id>" `
  --event visible_snapshot_saved `
  --event-id visible-snapshot-1 `
  --expected-sequence 0 `
  --json
```

## Check

```powershell
discord-context-bridge capture-loop status `
  --capture-id "<opaque capture id>" `
  --json
```

- checkpoint は atomic replace、event ledger は append-only。
- sequence 不一致、破損、予算超過は fail-closed。
- `gate_partial` は指定 pass 数を超えて無限再走査しない。
- observed-full receipt は `api_full.verified=false` の境界を越えない。

## Act

- `FDE` は `continue_capture`、`resolve_blocker`、`context_understanding`、`human_review` の判断投影を受け取る。
- `LCS` は全文 gate が閉じた後も `passport_pending` まで。DCB 自身は `passport_ready` event を発火しない。
- blocker 解消後は新しい一意な `event-id` と、現在 checkpoint の sequence で再開する。
- Discord 送信、投稿、公開、削除、設定変更はこの LOOP の範囲外。

## 安全な operational tags

タグは入力文字列をそのまま採用せず、allowlist から生成する。

- 範囲: `direct-message` / `server-threads-all` / `thread-only`
- 経路: `in-app-browser` / `chrome-visible` / `rest-backfill` / `saved-artifacts` / `desktop-accessibility`
- 補助: `refresh-check` / `observed-full`
