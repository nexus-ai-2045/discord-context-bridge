# Discord 全文取得 LOOP 運用カード

## Legacy parallel run closeout

並列取得の完了正本は、個別の `done` marker ではありません。既存の
`discord_full_capture_completion_gate.v1`、immutable strict receipt、
`discord_parent_completeness_certificate.v1` を順に通った証拠だけを full とします。

過去の `dcb.parallel-run.v1` directory は次の metadata-only adapter で判定します。

```bash
python -m discord_context_bridge.cli closeout-parallel-run \
  --run-dir <private-run-dir> \
  --completeness-db <canonical-completeness.sqlite3> \
  --parent-target-key <private-parent-target-key> \
  --json
```

各workerとimporter自身がcreate-only `dcb.parallel-producer-drain-receipt.v1` を発行し、
event routerが全drainを確認した後、run定義hashと
artifact snapshot hashへ結合したcreate-only `dcb.parallel-run-stop-receipt.v1` を
発行済みで、証拠不足のlegacy runを閉じる場合だけ `--finalize` を付けます。
停止receiptがない、または発行後にartifactが1 byteでも変化したrunは閉じません。
この場合は `blocked_closed` となり、`full_capture_confirmed` は false のままです。
`workers.done` や `importer.done` を置いても判定には影響しません。将来の取得は
legacy layoutを新設せず、通常の capture loop と parent completeness auditを使います。

親監査JSONの持ち込みは受理しません。adapter自身がcanonical completeness DBを監査し、
`run-metadata.json` の `parent_target_key_sha256` と対象を結合します。terminal receiptは
create-onlyの正本、`run-metadata.json` はそこを指す再生成可能なprojectionです。
停止receiptもoperatorの手書きmarkerではなく、`producer.quiesced` eventのconsumerである
event routerだけが発行します。

ここでのproducer ownershipは同一local OS user内の協調契約であり、悪意あるlocal processに
対する認証境界ではありません。CLIはworker/routerの実terminal handlerからのみ呼ぶ運用面で、
重複・欠落・宣言外worker・発行後変更をcontent bindingとcreate-only publicationで検知します。

producerのterminal handlerは、自分自身の終了時に次を一度だけ呼びます。同じproducerの
2回目のeventは拒否されます。

```bash
python -m discord_context_bridge.cli record-parallel-producer-drain \
  --run-dir <private-run-dir> --producer <worker-N-or-importer> \
  --event-id <opaque-terminal-event-id> --json
```

event routerは全producer receiptを受け取った時だけ集約します。

```bash
python -m discord_context_bridge.cli record-parallel-run-stop \
  --run-dir <private-run-dir> --event-id <opaque-router-event-id> \
  --stopped-reason <completed-or-failure-reason> --json
```

closeout receiptとrun metadataは運用projectionです。fullの権威は毎回再評価されるcanonical
parent completeness DBとcontent-bound evidenceであり、receipt単体を再検証なしで読んで
fullを主張しません。

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

## Source selection and scope

- Resolve the canonical target through the existing registry. Its `url_hash_16` key is canonical; a native browser URL remains a source alias. Do not replace the key with a source fingerprint or target alias.
- Reuse the original tab and identify it only with the official browser API's ephemeral tab/window IDs. Do not persist those IDs as canonical identity or read cookies or tokens.
- Before capture, confirm that the visible header and URL both match the requested target. Bind message IDs and scope to the message's own role article, its `aria-labelledby`, and the native `#chat-messages` container. A reply quote ID belongs to quoted context, not the current message body.
- Treat author, time, reply, inline-image, and `body_html` values as raw context data. Keep them within the existing private source boundary and never execute HTML.
- Expand every blocked group in each virtualized window. Remounts can collapse groups again, so reopen them for every window. Record the exact UI header shown outside an article for an originally deleted item; if the source reports zero or unavailable, do not invent its text or ID.
- If a legacy input lacks `native_container_id` scope evidence, do not infer that the current view is full; obtain an actual new source observation.

Use existing capture and review mechanisms as scope grows:

1. Leaf: messages and their attachments through the existing PDCA.
2. Thread: connected window overlap and two stable complete passes.
3. Forum: two inventory passes plus every relevant child scope.
4. Server: the registered current and historical source set.
5. Runtime: source readback and tests for the requested scope.

Registering a source does not prove full coverage; the relevant runtime and readback evidence must still satisfy the existing full gate.

## Do

```powershell
discord-context-bridge capture-loop start `
  --target-key "<canonical registry url_hash_16 key>" `
  --route chrome_extension `
  --upper-watermark "<latest message id>" `
  --scope thread_only `
  --refresh-check `
  --json
```

返された `capture_id` を使い、状態イベントを一意な `event-id` と期待 sequence 付きで進める。

再取得を新しい独立runとして開始する場合だけ `start` に `--attempt-id` を指定する。
同じtarget・route・watermarkと同じattempt IDなら既存checkpointへ再開する。異なるattempt IDは
別の `capture_id` と保存領域を使い、以前のpartial coverage、ledger、添付seal、receiptを引き継がない。
省略時は従来のcapture IDと出力形を維持する。attempt IDは1〜128文字の英数字、`.`、`_`、`-`に限る。
capture IDはattempt IDとbase capture IDから再計算して検査するunkeyed hashであり、署名や改ざん防止鍵ではない。
試行付きrunは既存storeの `attempt-identities/<capture_id>.json` に開始時identityとsource digestを固定する。
直接capture IDで再開する場合もcheckpointと固定情報を照合し、片側の削除・差替えを拒否する。
固定情報を先に保存するため、初回保存の中断時は同じIDを自動復元せずfail-closedとなる。
この変更以前の実験版attempt checkpointは固定情報がないため、自動移行せず別attemptとして開始する。
checkpoint、固定情報、ledger、receiptを同時に書き換えられる場合まで検出できるとは扱わない。
checkpoint、ledger、receiptの保存先bindingとsource digestを合わせて検査するが、ローカル保存物への
書換権限を持つ攻撃者に対する真正性証明としては扱わない。

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

### Ledger再構築とfull receipt保存

利用者が境界、安定再走査、添付完了、`full=true`を手書きする入口はない。
`reconcile`はdurableなvirtual-scroll coverageとmessage ledgerからprojectionを再構築し、
既存の正規full gateで判定する。添付IDがあるのにdurableな保存証拠がない場合はfail-closedとなる。
`full_capture_confirmed=true`の時だけ`context_acquisition`向けstrict receiptを永続化する。
partialはreceiptを作らず正常終了する。

```powershell
discord-context-bridge capture-loop reconcile `
  --capture-id "<opaque capture id>" `
  --json
```

stdoutは判定状態、blocker、receipt保存有無だけを返し、Discord URL、本文、local pathを返さない。
`status=full`かつ`receipt_persisted=true`をreceipt保存完了の組として確認する。

添付が発見されたrunでは、各source objectを同一file handleでhash/size検証し、capture固有の
`attachment-objects`配下へatomic copyしてからmanaged refを保存台帳へ記録し、
全IDが揃った時点でinventoryをsealする。sealはmessage ledger sequence/tip hash、coverage digest、
window countへ結合されるため、その後の`observe`で失効し、既存full receiptも破棄される。

### Windows の保証境界

Windowsでは、添付がないcaptureの`start` / `advance` / `observe` / `status` /
`reconcile`をサポートする。checkpoint、message event ledger、virtual-scroll coverage、
full gate、receiptの通常LOOPが対象であり、CIの`windows-latest` smokeで継続確認する。
stdoutへDiscord URL、target key、本文、source object path、store rootなどのraw pathを返さない
契約も同じである。

一方、managed attachmentの保存・読取は、親ディレクトリをnative Windows handleで固定し、
そのhandle相対でreparse pointを拒否して操作するbackendが入るまでWindowsではfail-closedとする。
`attachment-save` / `attachment-seal`をWindowsの一般対応範囲に含めず、path検査の繰り返しや
ACLだけでsafe storageを主張しない。添付が発見されたrunはこの境界を迂回してfullにならない。

```powershell
discord-context-bridge capture-loop attachment-save `
  --capture-id "<opaque capture id>" `
  --attachment-id "<discovered attachment id>" `
  --object-file "<private local object>" `
  --private-ref "attachments/<relative private ref>" `
  --expected-attachment-sequence 0 `
  --json

discord-context-bridge capture-loop attachment-seal `
  --capture-id "<opaque capture id>" `
  --expected-attachment-sequence 1 `
  --json
```

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

- `overall=ok` は CLI start/observe/status/reconcile + ledger rebuild + 正規full gate + receipt保存が通った証拠。
- `live_discord=false` と `not_claimed` を必ず確認する。live 全文完了の代替にしない。
- Windows CIのno-attachment smokeも同じくfixtureだけを検証し、managed attachment対応や
  live Discord全文取得を主張する根拠にはしない。


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
