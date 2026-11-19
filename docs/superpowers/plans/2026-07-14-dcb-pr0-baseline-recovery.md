# DCB PR0 Baseline Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Windows上のsubprocess文字コード、無期限待機、parallel compile競合を修正し、clean `origin/main` worktreeでfast/full gateを再現可能なgreenへ戻す。

**Architecture:** subprocess実行契約をrepo-local helperへ集約し、UTF-8 decode、bounded timeout、process-tree cleanup、最小限の進捗eventを共通化する。compile checkは専用pycache rootへ隔離し、他checkやpytestのimportと`.pyc`置換競合を起こさない。GitHub診断はread-onlyを維持し、account mismatch時に自動switchしない。

**Tech Stack:** Python 3.11+、pytest、subprocess、concurrent.futures、Windows taskkill、GitHub CLI。

---

## 固定する契約

- `subprocess.run(..., text=True)`には`encoding="utf-8"`と`errors="replace"`を指定する。
- Python childには`PYTHONIOENCODING=utf-8`を渡す。
- check timeoutは成功扱いせず、`failure_stage=timeout`、exit code `124`として返す。
- timeout後はchild process treeを終了し、orphanを残さない。
- secret、stderr本文、local pathを新しいpublic outputへ追加しない。
- GitHub account mismatchはblockedとして返し、read-only dashboardから`gh auth switch`しない。
- compile checkのpycacheを一時directoryへ隔離する。
- primary worktreeの未コミット差分は参照だけにし、コピーやcheckoutで上書きしない。

### Task 1: subprocess UTF-8契約をテストで固定する

**Files:**
- Create: `tests/test_process_runner.py`
- Modify: `tests/test_live_mvp_status_scripts.py`
- Modify: `tests/test_core.py`

- [ ] Step 1: `scripts/ops_check.py`、`scripts/discord_inventory_dashboard.py`、`scripts/gh_pr_read.py`、`scripts/repo_goal_status.py`、CLI source commandが`encoding="utf-8"`、`errors="replace"`、`PYTHONIOENCODING=utf-8`を使用することをmockで検証するtestを書く。
- [ ] Step 2: `python -m pytest tests/test_process_runner.py tests/test_live_mvp_status_scripts.py -q`を実行し、UTF-8引数未指定を理由にREDになることを確認する。
- [ ] Step 3: cp932ではdecode不能なUTF-8日本語をchild stdoutへ出すfixture testを追加し、例外ではなく置換可能な文字列として取得できることを契約化する。
- [ ] Step 4: focused testを再実行し、production変更前のREDが期待した箇所だけであることを確認する。

### Task 2: 共通process runnerを実装する

**Files:**
- Create: `src/discord_context_bridge/process_runner.py`
- Modify: `src/discord_context_bridge/__init__.py`
- Modify: `scripts/ops_check.py`
- Modify: `scripts/discord_inventory_dashboard.py`
- Modify: `scripts/gh_pr_read.py`
- Modify: `scripts/repo_goal_status.py`
- Modify: `src/discord_context_bridge/cli.py`
- Test: `tests/test_process_runner.py`

- [ ] Step 1: immutable `ProcessResult`を追加し、`ok`、`returncode`、`stdout`、`stderr`、`elapsed_seconds`、`failure_stage`を保持する。
- [ ] Step 2: `run_process(command, *, cwd, env, timeout_seconds)`をargv限定、`shell=False`、UTF-8 decode、`errors="replace"`で実装する。
- [ ] Step 3: Windows timeoutでは`taskkill /F /T /PID`、POSIXではprocess groupへSIGKILLを送り、その後processをreapする。
- [ ] Step 4:各wrapperを共通runnerへ移行する。既存のexit code、JSON schema、人間向け文言を変更しない。
- [ ] Step 5: CLI source commandは既存argv parserとenv-prefix機能を維持し、共通decode契約だけ利用する。
- [ ] Step 6: `python -m pytest tests/test_process_runner.py tests/test_live_mvp_status_scripts.py tests/test_core.py -q`を実行しGREENを確認する。

### Task 3: timeout・orphan cleanup・出力上限をTDDする

**Files:**
- Modify: `src/discord_context_bridge/process_runner.py`
- Modify: `scripts/ops_check.py`
- Modify: `tests/test_process_runner.py`
- Modify: `tests/test_live_mvp_status_scripts.py`

- [ ] Step 1: sleepするchildがtimeout後に`failure_stage="timeout"`、returncode `124`になるtestを書く。
- [ ] Step 2: childがさらにgrandchildを生成するfixtureを作り、timeout後にgrandchildが残らないtestを書く。
- [ ] Step 3: stdout/stderrの保存上限を各64KiBに固定し、超過時に文字数と`output_truncated=true`だけを返すtestを書く。
- [ ] Step 4: ops checkへdefault timeoutを追加する。fast checkは20秒、full/releaseのpytestは120秒、それ以外は60秒とする。
- [ ] Step 5: timeoutしたcheckだけを失敗として集約し、他checkの完了結果を保持する。
- [ ] Step 6: focused testsを実行しGREENを確認する。

### Task 4: compile checkをpycache競合から隔離する

**Files:**
- Modify: `scripts/ops_check.py`
- Modify: `tests/test_live_mvp_status_scripts.py`

- [ ] Step 1: compile checkだけが一意なtemporary `PYTHONPYCACHEPREFIX`を使用するtestを書く。
- [ ] Step 2: compile check終了後にtemporary directoryが削除されるtestを書く。
- [ ] Step 3: `compileall -q src tests scripts`を隔離envで実行する最小実装を追加する。
- [ ] Step 4: compile checkとpytestを同時に10回実行する回帰testを追加し、PermissionErrorが再現しないことを確認する。
- [ ] Step 5: `python -m pytest tests/test_live_mvp_status_scripts.py -q`を実行しGREENを確認する。

### Task 5: GitHub診断のread-only境界を固定する

**Files:**
- Modify: `scripts/discord_inventory_dashboard.py`
- Modify: `scripts/gh_guard.py`
- Modify: `tests/test_live_mvp_status_scripts.py`

- [ ] Step 1: account mismatch時もdashboardが`--switch`を渡さず、PR lookupへ進まないtestを書く。
- [ ] Step 2: `gh_guard --switch`は明示CLI利用時だけ動作し、dashboard、repo status、read-only monitorから呼ばれない静的testを書く。
- [ ] Step 3: mismatchを`gh_account_boundary_failed`としてmetadata-onlyに返す実装を維持する。
- [ ] Step 4: GitHub account名やtokenがfailure outputへ含まれないtestを実行する。
- [ ] Step 5: focused testsを実行しGREENを確認する。

### Task 6: baselineを再検証してcommitする

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/plans/2026-07-14-dcb-pr0-baseline-recovery.md`

- [ ] Step 1: `python -m pytest tests/test_process_runner.py tests/test_live_mvp_status_scripts.py tests/test_core.py -q`を実行する。
- [ ] Step 2: `python scripts/ops_check.py --profile fast`を3回実行し、全8 checkが毎回greenでbounded time内に終わることを確認する。
- [ ] Step 3: `python scripts/ops_check.py --profile full`を実行し、全checkがgreenであることを確認する。
- [ ] Step 4: `python -m pytest tests -q`、`python -m compileall -q src tests scripts`、`git diff --check`を実行する。
- [ ] Step 5: secret scan、personal path scan、public-safe output testを実行する。
- [ ] Step 6: plan checkboxとCHANGELOGを実測結果で更新する。
- [ ] Step 7: spec compliance review、code quality review、security diff reviewを順番に実行し、CRITICAL/HIGHを解消する。
- [ ] Step 8: `fix: Windows運用ゲートを安定化する`としてcommitする。

## PR0完了条件

- clean worktreeでfast/full/pytest/compileがgreen。
- cp932 decode exceptionが出ない。
- compileとpytestの並列実行でpycache PermissionErrorが出ない。
- 全checkにbounded timeoutがある。
- timeout後にchild/grandchildが残らない。
- dashboardはGitHub認証状態を変更しない。
- raw/private/secret情報を新たに出力しない。
- draft PRのCIがgreenで、人間目視レビュー待ち状態になる。
