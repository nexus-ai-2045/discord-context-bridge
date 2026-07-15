# DCB Site Adapter Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `discord.v1` を可視入力のruntime選択・drift判定・private atomic snapshot保存へ接続し、本文を公開出力しないCLIで運用証跡を生成する。
**Architecture:** 既存PR #20のapplication orchestrator設計を正本とし、browser操作を増やさない純粋なadapter coreとprivate writerを追加する。既存の可視テキスト取得経路はproducerとして再利用し、adapterは観測・保存だけを担当する。
**Tech Stack:** Python 3.11+、stdlib、pytest、JSON Schema文書。

---

### Task 1: adapter契約とdrift判定

**Files:**
- Create: `src/discord_context_bridge/site_adapter_runtime.py`
- Create: `tests/test_site_adapter_runtime.py`
- Modify: `schemas/dcb_raw_capture.v1.schema.json`
- Modify: `schemas/dcb_capture_manifest.v1.schema.json`

- [ ] RED: URL選択、structured入力、body fallback、zero-hit drift、欠損field、添付未帰属、absolute path拒否を1挙動ずつtestする。
- [ ] `python -m pytest tests/test_site_adapter_runtime.py -q`で未実装理由の失敗を確認する。
- [ ] `select_adapter`、`build_capture`、`build_manifest`を純関数として最小実装する。
- [ ] schemaの必須fieldをadapter契約と一致させ、metadata-only manifestへcoverage/freshness/driftを追加する。
- [ ] focused testをGREENにする。

### Task 2: private atomic persistence

**Files:**
- Create: `src/discord_context_bridge/site_adapter_store.py`
- Create: `tests/test_site_adapter_store.py`

- [ ] RED: temp→replace、同一captureの冪等性、失敗時partial/blocked、symlink/reparse/path traversal拒否、stdoutへの本文/絶対path非露出をtestする。
- [ ] `python -m pytest tests/test_site_adapter_store.py -q`でREDを確認する。
- [ ] `.local`配下へraw JSONとmanifestをatomic保存し、公開結果はcapture_idとsafe metadataだけ返す。
- [ ] focused testをGREENにする。

### Task 3: CLI統合とsynthetic E2E

**Files:**
- Modify: `src/discord_context_bridge/cli.py`
- Modify: `src/discord_context_bridge/__init__.py`
- Create: `tests/fixtures/discord_site_adapter_structured.json`
- Create: `tests/test_site_adapter_cli.py`

- [ ] RED: `capture-visible-snapshot --source-url --structured-input|--input --output-root --json`の正常、fallback、drift、URL不一致をtestする。
- [ ] 既存input/source-commandをproducerとして再利用し、adapter選択→build→storeへ接続する。
- [ ] CLI出力が本文・参加者名・source URL・絶対pathを含まず、`outbound_actions=disabled`を返すことを固定する。
- [ ] focused、全pytest、compile、`ops_check.py`、`repo_goal_status.py --run-smoke --json`を実行する。
- [ ] spec、品質、security diff reviewを通し、運用証拠と制約を日本語docsへ反映する。

### Task 4: PR readiness

- [ ] `git diff --check`、secret/personal-path scan、PR scope/language/account gateを実行する。
- [ ] 変更を1目的のcommitにまとめ、draft PRを作成する。
- [ ] CIと人間目視レビュー待ちで停止し、mergeは行わない。
