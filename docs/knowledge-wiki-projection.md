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
