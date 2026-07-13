# DCB Autonomous Orchestrator Design

**Status:** 人間レビュー待ち  
**Date:** 2026-07-13  
**Repository:** `nexus-ai-2045/discord-context-bridge`  
**Branch:** `codex/dcb-autonomous-orchestrator`

## 1. 目的

Discord Context Bridge（DCB）を、agentが低レベルcommandを手でつなぐ運用から、次を一貫して自走できるapplication orchestratorへ移行する。

1. 利用可能な取得経路を安全に診断する。
2. visible evidenceを失わずappend-onlyで保存する。
3. 入力品質を判定し、DOM/UIノイズを返信文脈へ流さない。
4. partial captureを既知証拠として維持し、full captureへ差分reconcileする。
5. 返信前最低文脈gateを通過した場合だけreply reviewへ進む。
6. local test、CI、PR、人間レビュー、mergeを別状態として証明する。

Discord send、reaction、edit、delete、repository visibility変更、外部公開は自動化しない。

## 2. 採用方針

application-level orchestratorとappend-only evidence state machineを採用する。

`core.py`へ機能を追加し続ける案は短期的には速いが、既存の巨大moduleとCLI/MCPの重複を悪化させるため採用しない。常駐queue/worker方式は将来選択肢として残すが、現時点では運用複雑性が大きいため実装しない。

## 3. 自走範囲

### 自動で行う

- local snapshot、REST、gateway、bot inbox、private adapter、既存Chrome接続の能力診断
- secret-command providerの利用。ただし秘密値は返さない
- visible evidenceのprivate local保存
- quality判定、coverage判定、partial-to-full reconcile
- reply context plan、metadata-only state、次の安全な行動の提示
- branch/worktree上のtest、smoke、E2E、security gate
- 明示された対象branchのpushとdraft PR作成
- CI、review、mergeabilityのread-only監視

### 人間gateを要求する

- bot登録、権限追加、secret設定変更
- Chrome拡張/native host設定変更
- Discord send、reaction、edit、delete
- PRの人間目視承認
- merge
- repository visibility、release、告知、外部共有

ユーザーが最終依頼でPRとmergeを求めていても、人間目視レビューが実際に完了するまではmergeしない。

## 4. component構造

```text
src/discord_context_bridge/
  capture/
    health.py        # routeごとの能力・状態診断
    quality.py       # visible text品質判定
    reconcile.py     # append-only evidenceからcoverageを投影
    orchestrator.py  # read-current / full-capture状態機械
  application/
    dcb.py           # CLI/MCP共通use case
  security/
    capability.py    # capabilityと人間gate
    private_store.py # private artifactの安全な保存
  ops/
    evidence.py      # test/CI/PR/review/mergeの証拠状態
    checks.py        # timeout付きcheck runner
  commands/
    dcb.py           # human-facing CLI adapter
```

既存の`core.py`、package root export、CLI command、MCP toolは互換facadeとして維持する。

## 5. route health

各routeを独立して判定する。1 routeの成功で他routeをgreenにしない。

```text
configured
unconfigured
available
auth_required
permission_denied
rate_limited_retryable
extension_unavailable
drift_detected
unverified
```

対象route:

- existing snapshot
- gateway live event
- REST backfill
- bot inbox
- private adapter
- Chrome connection
- Chrome tab inventory
- Chrome claim
- Chrome DOM snapshot
- attachment evidence

secret-command providerと既存Chrome接続は自動診断・利用する。設定変更や権限追加は行わない。

## 6. capture state machine

```text
preflight
  -> route_probed
  -> observation_saved
  -> quality_checked
  -> reconciled
  -> context_checked
  -> local_verified
  -> human_review_required
```

capture state:

- `blocked`: 安全な観測がなく、利用可能routeもない
- `partial`: visible evidence等は保存できたが全文保証条件を満たさない
- `full`: スレッド起点、履歴終端、必要message range、添付の帰属または不存在を検証済み

`complete`という曖昧な状態名は使わない。capture、local verification、PR、review、mergeを別fieldで表す。

## 7. command UX

正式package名は維持し、human-facing aliasとして`dcb`を追加する。

```text
dcb health
dcb read-current
dcb full-capture
dcb reply-review
dcb status
```

共通packet:

```json
{
  "route_selected": "read-current",
  "capture_state": "partial",
  "quality_state": "clean_message_text",
  "context_state": "expand_required",
  "can_claim": [],
  "cannot_claim": ["full_capture"],
  "next_safe_action": "rest_backfill_or_visible_expand",
  "raw_text_returned": false,
  "outbound_actions": "disabled"
}
```

## 8. quality gate

visible inputを次に分類する。

- `clean_message_text`
- `mixed_visible_dom`
- `ui_noise`
- `empty`

判定材料:

- HTML/DOM tag density
- `node_id`等のUI metadata比率
- message boundary confidence
- author/timestamp/body抽出率
- Discord UI chrome比率

`ui_noise`と`empty`はcontext passportやreply candidate生成へ進ませない。

## 9. evidence reconcile

- observation ledgerは重複観測もappendする。
- latest reportはprojectionとして生成する。
- event identity、hash、取得range、history end、attachment ledgerを別々に評価する。
- 既存partial snapshotを削除・上書きせず、新しい観測とreconcileする。
- full判定は本文件数だけでなく、履歴終端と添付整合を要求する。

## 10. security設計

### processとsecret

- `shell=True`を廃止し、argv + `shell=False`へ統一する。
- child processへ親環境を全量継承しない。
- secret providerへ渡す環境変数をallowlist化する。
- stdoutは1行・size上限・token形式を検証する。
- stderr、token、cookie、Authorization、webhookを永続化しない。
- timeoutとprocess-tree cleanupを共通化する。

### private artifact

- `.local`配下のみを既定保存先にする。
- path traversal、symlink、Windows reparse pointを拒否する。
- atomic writeを使用する。
- Windowsではcurrent user限定ACLを検証する。
- retention、容量上限、disk-full、同時writerをtestする。

### Chrome

- capabilityは`snapshot`と`fill`だけに限定する。
- Enter、send click、reaction、edit、deleteを拒否する。
- tab、URL、latest snapshot、UI候補一意性を直前に再確認する。
- callable APIがないsessionは`extension_unavailable`で停止する。

### GitHub

- read-only診断で`gh auth switch`を実行しない。
- capture、draft fill、PR create、merge、publishを別capabilityにする。
- PR createはrepo/branch/commitをpinする。
- mergeは人間レビュー済み状態を確認する。
- visibility変更はこのworkflowの能力に含めない。

## 11. 自走verification pipeline

### fast

- compile
- diff check
- secret scan
- reply context contract
- boundary contract
- quality/reconcile targeted tests
- URL/capture smoke

### full

- fast全件
- pytest全件
- fixture E2E
- schema/projection/runtime skill sync
- private store security tests
- process timeout/encoding/orphan tests

### release

- full全件
- GitHub remote/account/identity確認
- personal path/private artifact scan
- PR scope/Japanese review gate
- CI required checks

各checkはtimeout、開始/heartbeat/終了event、最大worker数、JSONL evidenceを持つ。infrastructure timeoutだけ1回再試行し、終了後にorphan processを監査する。

## 12. baseline修復（PR0）

2026-07-13に`origin/main`から作成したclean worktreeのfull gateは3件失敗した。

- dashboard subprocessのcp932 decode failure
- 同原因によるpytest 1件失敗
- parallel compile時の`__pycache__`置換PermissionError

現在のprimary worktreeにはUTF-8処理修正が未コミットで存在する。実装開始時は、この既知修正を独立PR0としてTDD・review・mergeし、clean origin/mainでfull gateをgreenにしてから機能PRへ進む。compileは隔離outputまたは並列競合を起こさない方式へ変更する。

## 13. PR分割

1. PR0: Windows encoding、timeout、compile isolationでbaselineを修復
2. PR1: route health / quality / reconcile / evidence schemaとfixture
3. PR2: pure domain quality・reconcile実装
4. PR3: route probeとread-current/full-capture orchestrator
5. PR4: CLI/MCP共通application serviceと`dcb` alias
6. PR5: private store・process・capability hardening
7. PR6: ops evidence、runtime projection、release gate
8. PR7: core/testの機械的分割（挙動変更なし）

各PRは1目的、原則12ファイル以内とし、前PRのmerge後に次PRを最新mainへrebaseする。

## 14. TDDとレビュー

- behavior変更は必ずRED→GREEN→REFACTORで進める。
- 実装taskごとにfresh implementerを使う。
- reviewはspec compliance→code quality→security diffの順に行う。
- CRITICAL/HIGHはPR作成前に解消する。
- mediumは修正または明示的なdefer理由を残す。
- PR作成後、人間目視レビューを待つ。
- 承認後だけmergeし、mainのfull/release gateを再実行する。
- merge後はremote branch、worktree、local branchを安全に後片付けする。

## 15. 完了条件

- `dcb health/read-current/full-capture/reply-review/status`が共通stateを返す。
- partialをfullと誤表示しない。
- UIノイズからreply candidateを生成しない。
- secret/private/raw情報がpublic outputへ出ない。
- process hangがbounded timeoutで終了し、orphanを残さない。
- CLI/MCPが同じapplication serviceを利用する。
- fast/full/release evidenceが機械可読で保存される。
- 全PRがCI green、security review済み、人間目視承認済みでmergeされる。
- mainのworktreeと一時worktree/branchが整理される。

## 16. 対象外

- Discordへの自動送信
- bot登録・Discord権限の自動変更
- Chrome設定の自動変更
- 常駐queue/worker
- FDE全体へのsurface adapter展開
- repository public化、release、告知
