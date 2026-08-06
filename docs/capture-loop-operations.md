# Discord 全文取得 LOOP 運用カード

## 目的

Discord Context Bridge（DCB）の既存 Orchestrator、全文取得 gate、observed-full 検証を、再開可能な checkpoint と追記専用 event ledger で接続する。本文や Discord URL は status に返さず、外部送信は行わない。

## Message data SSOT

本文取得データの唯一の正本は
`dcb-private-message-event-ledger.v1` の追記型event ledgerとする。
checkpointとvirtual-scroll coverageは運用状態・取得範囲の証拠であり、
本文正本ではない。

正規化済みthread state、raw projection、Markdown projection、
attachment manifest、full-capture evidenceは、message event ledgerから
同じdeterministic reducerで再生成する。projection自体はcapture storeへ
永続化せず、必要時に再構築する。

`full_candidate` は ledger projection の局所 pre-check であり、最終 full ではない。
最終 full は既存 `evaluate_full_capture`（schema `discord_full_capture_completion_gate.v1`）だけが確定する。
`rebuild` は ledger projection を reconcile 契約へ写し、その gate 結果を `full_capture_gate` として返す。
`full_candidate` 単独や derived evidence 直接評価で full を主張しない。

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

### Chromium仮想リストとbackground cacheの統合

取り込み順はcache-firstとする。保存済みcacheを同期的に先読みして最初の
文脈を即時構成し、その後のChrome/Chromium可視走査は補完処理として進める。
両経路はDiscord message IDを正本キーに統合し、到着順が逆でも永続化時には
cacheからlive DOMの順で評価する。

Discordのmessage listは仮想化されるため、現在DOMの`article`件数を総数として扱わない。
Chrome側は毎回fresh DOMからmessage ID、content hash、表示順だけをprivate JSONへ保存し、
`observe`でLOOPへ渡す。本文、実URL、参加者名はstatusへ返さない。

```powershell
discord-context-bridge capture-loop observe `
  --capture-id "<opaque capture id>" `
  --window-file "<private window observation.json>" `
  --expected-window-count 0 `
  --json
```

window observationの最小形:

```json
{
  "window_id": "pass-1-window-001",
  "source": "chrome_visible_dom",
  "direction": "toward_oldest",
  "scan_pass": 1,
  "oldest_reached": false,
  "latest_reached": false,
  "messages": [
    {"message_id": "<private id>", "content_hash": "<private hash>"}
  ]
}
```

background cache collectorはChrome操作と別processで動かしてよい。exact targetに結合できた
cacheだけを`source=background_cache`として同じ`observe`入口へ渡す。message IDが同じなら
DOM/cacheの重複で件数を増やさず、content hashが変わった時だけ編集versionを追加する。
message IDがないrecordを本文hashだけでcanonical dedupeしない。

仮想リストの完了条件:

- window間にmessage ID overlapがあり、coverage graphが連結している。
- 最古端と最新watermarkを両方観測している。
- 2回以上のcomplete scanでfirst/last IDと件数が一致する。
- final passの新規message IDが0件。
- stable message ID欠落、window overlap gap、未処理cacheが0件。
- このcoverage成立後も、添付inventoryとraw/Markdown/ledger照合は別gateとして必須。

## Check

```powershell
discord-context-bridge capture-loop status `
  --capture-id "<opaque capture id>" `
  --json
```

fixture だけの metadata-only 運用 smoke（live Discord ではない）:

```powershell
python scripts/capture_loop_metadata_smoke.py --json
```

- `overall=ok` は CLI start/observe/status + ledger rebuild + `evaluate_full_capture` 橋渡しが通った証拠。
- `live_discord=false` と `not_claimed` を必ず確認する。live 全文完了の代替にしない。


- checkpoint は atomic replace、event ledger は append-only。
- virtual scroll coverageもatomic checkpointで保存し、window countの楽観lockで並行writerを防ぐ。
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
