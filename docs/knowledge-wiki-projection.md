# 人物・話題Wiki projection

Discord Context Bridge（DCB）の追記型snapshot台帳から、Obsidianで読むための人物タイムライン、話題Wiki、Knowledge TOPを生成する。

## 責務

- DCB snapshot台帳: 取得した観測の正本
- `export-knowledge-wiki`: 人物・話題・TOPの再生成
- Templater: 人間が新しいメモを作る場合の入力補助。projection本体ではない
- 人間: 人物同一性、話題の意味、重要導線を編集

## 実行

```powershell
python -m discord_context_bridge.cli export-knowledge-wiki `
  --snapshot-store <DCB_SNAPSHOT_STORE> `
  --output-root <PRIVATE_KNOWLEDGE_WIKI_ROOT> `
  --person-registry <PRIVATE_PERSON_REGISTRY_JSON> `
  --topic-registry <PRIVATE_TOPIC_REGISTRY_JSON> `
  --dry-run `
  --json
```

最初に`--dry-run`で生成予定を確認し、問題がなければ同オプションを外して生成する。出力先はprivate local領域に限定する。コマンドの標準出力は件数などのmetadataだけで、本文とローカルパスを返さない。

## 生成構造

```text
Knowledge Wiki/
├─ Knowledge TOP.md
├─ Knowledge TOP.generated.md
├─ Review Queue.md
├─ Review Queue.generated.md
├─ Templates/
│  ├─ Person Notes.md
│  ├─ Topic Notes.md
│  └─ Review Decision.md
├─ People/
│  ├─ person-<id>.generated.md
│  └─ person-<id>.notes.md
└─ Topics/
   ├─ topic-<id>.generated.md
   └─ topic-<id>.notes.md
```

`*.generated.md`は再生成対象。`*.notes.md`と`Knowledge TOP.md`は人間編集領域であり、既存内容を上書きしない。
`Review Queue.md`と`Templates/`も人間編集領域として初回だけ作成し、再実行では上書きしない。Templaterはこれらの入力補助に使い、イベント台帳やprojectionの実行主体にはしない。

第一スライスでは、発言者ラベルを人物候補として扱う。話題は本文中の明示的な`#hashtag`と`[[Wiki link]]`だけを採用し、AIによる推測分類は行わない。

人物同一性の統合、話題名の統合・改名、題の分割・粒度・横断リンク、推論の事実昇格は人間レビュー境界とする。

### 題粒度の契約

題はprivate localの話題台帳で安定した`topic_id`を持つ。`label`の変更は改名であり、同じ`topic_id`を保つ。`aliases`はhashtagまたはWiki linkの表記揺れを同じ題へ統合する。1観測を複数の`topic_ids`へassignする操作を題の分割として扱う。

`broader_topic_ids`は「この題より一段広い題」、`related_topic_ids`は階層ではない横断導線を表す。投影は上位・下位・関連を相互に辿れるリンクとして生成するが、子のイベントを親へ暗黙集約しない。粒度や意味を機械推論で決めず、台帳にない本文は未分類のままReview Queueへ残す。

### Sparkによる候補生成

Sparkは分類判断の正本ではなく、候補生成器としてだけ使う。正本はレビュー済み`dcb.topic_assignment_registry.v1`、入力正本はDCB snapshot台帳、未レビュー候補の履歴はappend-only `dcb.topic_classification_proposal.v1`である。

`build-topic-classification-packet`は未分類だけをprivate fileへ最大件数付きで抽出する。packetには原文が入るため、外部送信は別の人間レビュー境界である。Sparkの結果は`gpt-5.3-codex-spark`、packet ID、全観測への応答、既存topic ID、confidenceを検証してから`import-topic-classification-result`で候補台帳へ追記する。再実行は同じproposal IDを重複追記しない。`build-topic-human-review-packet`で採用・修正・却下の判断材料を作れるが、どのコマンドもレビュー済み台帳やWikiを変更しない。

## 日次運用

`scripts/run_knowledge_wiki_projection.py` は既存projectionを呼ぶ薄い運用runnerである。同時起動をlockで停止し、本文とローカルパスを含まない最新実行receiptをatomicに保存する。`--dry-run`はWikiとreceiptを変更せず、`--verify`は成功receiptのtimestampを含めて読み取り専用で確認する。既定では36時間より古いreceiptを失敗とし、必要な場合だけ`--max-receipt-age-hours`で変更する。

Windows Task Schedulerには`scripts/setup_knowledge_wiki_projection_task.ps1`を使う。既定はdry-runで、`-Apply`を明示した場合だけ毎日実行タスクを登録する。`ExpectedCommit`には人間レビュー済みの統合commitを40桁SHAで指定し、安定checkoutの現在HEADがそのcommitを履歴に含む場合だけ適用可能とする。設定変更前にdry-runのJSONを人間レビューし、登録後は`-Verify`で実タスクのaction、working directory、有効状態、日次trigger、時刻、同時起動、15分上限、hidden設定を照合する。既定時刻は日本時間04:00で、必要なら`-At HH:mm`で変更する。actionはPython runnerを直接起動し、source欠損などの終了コード`2`をTask Schedulerへ伝播する。console hostを中継すると子プロセス失敗がTask成功に見えるため使わない。

```powershell
.\scripts\setup_knowledge_wiki_projection_task.ps1 `
  -PythonPath <PYTHON_EXE> `
  -RepoRoot <STABLE_DCB_CHECKOUT> `
  -SnapshotStore <DCB_SNAPSHOT_STORE> `
  -OutputRoot <PRIVATE_KNOWLEDGE_WIKI_ROOT> `
  -ReceiptPath <PRIVATE_RUN_RECEIPT_JSON> `
  -LockPath <PRIVATE_RUN_LOCK> `
  -ExpectedCommit <REVIEWED_MERGE_COMMIT_SHA> `
  -Json
```

この確認結果が`action: dry_run`かつ`changed: false`であることを確認してから、同じ引数へ`-Apply`を追加する。Task Schedulerからworktreeを参照させず、merge後の安定したcheckoutを`RepoRoot`に指定する。

自動更新の保証は「必ず成功する」ことではなく、入力正本が永続private領域に存在すること、runner失敗を失敗として記録すること、atomic receiptを`--verify`で36時間以内に監視すること、同じsnapshotとregistryから同じ投影を再生成できることの組合せで行う。snapshotやregistryをrepo checkout配下だけに置かない。receiptが古い、`ok=false`、またはsourceがない場合は自動更新未保証としてfail closedにする。

### 復旧保証マトリクス

| 根因 | detector | repair | evidence |
|---|---|---|---|
| private root欠損 | setup dry-runの`snapshot_store_present`と`data_paths_outside_repo` | operating contract既定のshared snapshot root配下へledger・registry・receipt・lockを配置 | `ready_to_apply=true`とrunner receipt |
| runtime参照破損 | `repo_root_present`、`stable_checkout`、`runner_present`、Task `working_directory`照合 | worktreeではない安定checkoutを指定 | setup `-Verify`の`task_matches=true` |
| 対象commit未包含 | `git_present`、`expected_commit_format`、`expected_commit_present`、`expected_commit_in_head_history` | Gitを利用可能にし、review済み統合commitを含むまで安定checkoutをfast-forward | dry-runと`-Verify`の`ready_to_apply=true` |
| console hostによる偽成功 | `direct_exit_propagation`とTask action照合 | PythonをTask actionとして直接実行 | source欠損時のrunner exit code `2`と失敗receipt |
| 題分類契約不足 | packet/result/proposal schema、topic relation検証、review status | Spark出力をappend-only候補へ限定し、人間がreviewed registryへ昇格 | 60件fixtureの再実行一致と重複追記0 |

setupのdry-runは設定を書き換えず、detectorを返す。`ready_to_apply=false`でも調査用dry-run自体は成功するが、`-Apply`は登録前にexit code `2`で停止する。

定期実行が自動化するのは再投影だけである。人物同一性、話題の意味、Review Queueの判断は引き続き人間が行う。

## 人間レビュー台帳

人物名寄せと話題付与は、private localのJSON台帳を明示指定した場合だけ適用する。台帳を省略した場合は従来どおり人物をtarget単位で分離し、明示タグのないイベントを未分類に残す。

人物台帳は`dcb.person_registry.v1`、話題台帳は`dcb.topic_assignment_registry.v1`を使用する。対応するJSON Schemaは`schemas/`にある。どちらも`private_local_only: true`、各判断に`reviewed_at`と`reviewed_by`が必要である。人物台帳の`aliases`にはReview Queueに表示された候補`person_id`を並べ、統合後の`person_id`と表示名を人間が決める。

`Review Queue.generated.md`には、人物候補IDと話題未分類イベントの`observation_id`が出る。人間はこのIDを判断材料にして台帳を更新し、再度`--dry-run`で件数変化を確認する。人物名やDiscord本文を含み得るため、台帳と投影結果は公開リポジトリへcommitしない。
