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

人物同一性の統合、話題名の統合・改名、推論の事実昇格は人間レビュー境界とする。

## 日次運用

`scripts/run_knowledge_wiki_projection.py` は既存projectionを呼ぶ薄い運用runnerである。同時起動をlockで停止し、本文とローカルパスを含まない最新実行receiptをatomicに保存する。`--dry-run`はWikiとreceiptを変更せず、`--verify`は成功receiptのtimestampを含めて読み取り専用で確認する。既定では36時間より古いreceiptを失敗とし、必要な場合だけ`--max-receipt-age-hours`で変更する。

Windows Task Schedulerには`scripts/setup_knowledge_wiki_projection_task.ps1`を使う。既定はdry-runで、`-Apply`を明示した場合だけ毎日実行タスクを登録する。設定変更前にdry-runのJSONを人間レビューし、登録後は`-Verify`で実タスクのaction、working directory、有効状態、日次trigger、時刻、同時起動、15分上限、hidden設定を照合する。既定時刻は日本時間04:00で、必要なら`-At HH:mm`で変更する。

```powershell
.\scripts\setup_knowledge_wiki_projection_task.ps1 `
  -PythonPath <PYTHON_EXE> `
  -RepoRoot <STABLE_DCB_CHECKOUT> `
  -SnapshotStore <DCB_SNAPSHOT_STORE> `
  -OutputRoot <PRIVATE_KNOWLEDGE_WIKI_ROOT> `
  -ReceiptPath <PRIVATE_RUN_RECEIPT_JSON> `
  -LockPath <PRIVATE_RUN_LOCK> `
  -Json
```

この確認結果が`action: dry_run`かつ`changed: false`であることを確認してから、同じ引数へ`-Apply`を追加する。Task Schedulerからworktreeを参照させず、merge後の安定したcheckoutを`RepoRoot`に指定する。

定期実行が自動化するのは再投影だけである。人物同一性、話題の意味、Review Queueの判断は引き続き人間が行う。

## 人間レビュー台帳

人物名寄せと話題付与は、private localのJSON台帳を明示指定した場合だけ適用する。台帳を省略した場合は従来どおり人物をtarget単位で分離し、明示タグのないイベントを未分類に残す。

人物台帳は`dcb.person_registry.v1`、話題台帳は`dcb.topic_assignment_registry.v1`を使用する。対応するJSON Schemaは`schemas/`にある。どちらも`private_local_only: true`、各判断に`reviewed_at`と`reviewed_by`が必要である。人物台帳の`aliases`にはReview Queueに表示された候補`person_id`を並べ、統合後の`person_id`と表示名を人間が決める。

`Review Queue.generated.md`には、人物候補IDと話題未分類イベントの`observation_id`が出る。人間はこのIDを判断材料にして台帳を更新し、再度`--dry-run`で件数変化を確認する。人物名やDiscord本文を含み得るため、台帳と投影結果は公開リポジトリへcommitしない。
