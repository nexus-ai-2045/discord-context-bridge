# 変更耐性アーキテクチャと保守方針

Status: proposed

この文書は、Discord Context Bridge（DCB）の責務分割を進める際の設計指針です。実装済みかどうかは [ISSUE_LIST.md](../ISSUE_LIST.md)、実行順は [ROADMAP.md](../ROADMAP.md)、安全境界は [operating-contract.md](operating-contract.md) を正本とします。

## 目的

Discord API、権限、DOM、保存形式、CLI、MCP の変更を局所化し、次の既存契約を保ったまま段階的に保守性を上げます。

- Discordへの書き込みを行わない。
- raw本文、credential、実ID、local pathを通常出力や公開artifactへ出さない。
- 取得事実はlocal-privateなappend-only ledgerへ保存し、人間向け文書はprojectionとして再生成できるようにする。
- `full`、`partial`、`blocked`を証拠に基づいて区別し、完全保存前に完了を主張しない。
- package export、CLI/MCPの利用形、metadata-only出力を意図せず壊さない。

## 目標とする責務境界

```text
Ingress adapters
    -> policy / capability gate
    -> capture application service
    -> immutable evidence store
    -> normalizer / projection
    -> context and review services
    -> CLI / MCP / report adapters
```

境界ごとの責務は次のとおりです。

| 境界 | 責務 | 持ち込まないもの |
|---|---|---|
| Ingress adapter | REST、Gateway、visible text、clipboard、local file、private commandの差を吸収する | domain判断、公開文面 |
| Policy / capability gate | 利用可能な取得範囲、安全な縮退、停止理由を判定する | token値、raw本文 |
| Application service | capture、reconcile、passport、draft reviewなどのuse caseを組み立てる | CLI/MCP固有の表示処理 |
| Evidence store | 観測を追記し、hash、順序、取得条件を保持する | 人間向け要約を正本にすること |
| Projection | raw evidenceから安定したconversation、coverage、manifestを作る | source固有fieldへの無制限な依存 |
| Output adapter | CLI、MCP、report向けに安全な結果を整形する | use caseの重複実装 |

`core.py`とpackage rootは、移行中の互換facadeとして残します。新しい実装へ責務を移しても、旧importと新importのparityを検証できる間は一括削除しません。

## 変更に強くするための契約

### Source adapter

外部source固有のURL、HTTP、DOM selector、pagination、rate limit、API versionはadapter内に閉じ込めます。adapterは取得可能なcapabilityと失敗分類を返し、1経路の成功を別経路の成功として扱いません。

### Evidenceとprojection

同じ内容の再取得も観測としてledgerへ追記します。重複や変更なしは保存停止ではなくmetadataで表現します。既存eventの訂正は書き換えではなく、新しい補正eventを追加します。

raw evidenceは保存形式を直接migrationせず、version付きnormalizerまたはupcasterから現在のprojectionを再生成できる形を目指します。Discord固有の追加fieldは安定domainから隔離し、readerは未知fieldを許容します。

### 完全性とreadiness

取得件数だけで`full`にはしません。対象範囲、pagination終端、thread root、message、attachment、未解決参照、route failure、raw/projection/manifestの整合をreconcileして判定します。

運用結果は少なくとも次を分離します。

- local implementation
- local verification
- branch / commit
- push / PRなどの外部状態
- live Discordなど未検証の外部境界
- cleanupと無関係なdirty state

これにより、ローカルテスト成功を外部運用完了や公開準備完了と混同しません。

## 段階的な保守計画

1. package export、主要signature、CLI/MCP結果、安全境界、ledger表現をcharacterization testで固定する。
2. `test_core.py`をfeature別へ分け、挙動を変えずにfailure localizationを改善する。
3. model、redaction、storageを抽出し、`core.py`からre-exportする。
4. capture、context、review、sendの順に領域を分け、旧importとのparityを維持する。
5. CLIとMCPが共通application serviceを呼ぶようにし、表示とexit policyだけをadapterへ残す。
6. subprocess実行とmetadata-only output policyを共通化し、shell展開や秘密情報を含む失敗出力を避ける。
7. ops checkを副作用のない合成可能なcheckへ分け、local failureとexternal unverifiedを別状態にする。
8. 各段階でSSOT projection、性能、secret/path scan、互換性を確認してから次へ進む。

各段階は小さなrollback単位にします。file moveとbehavior change、複数領域の抽出、外部操作を同じ変更へ混ぜません。

## 検証契約

最低限、変更範囲に応じて次を確認します。

- 公開exportとsignature、旧importと新importのparity
- metadata-only出力、redaction、outbound disabled
- ledgerのappend-only性、既存hash・保存形式との互換性
- pagination終端、途中再開、重複観測、partial-to-full reconcile
- rate limit、権限不足、timeout、capability不足の分類
- CLI/MCP間のsafe field、blocker、next actionの一致
- `pytest`、該当するops profile、`git diff --check`

live API、認証、GitHub、Discordなどの外部確認がない場合は、その境界を未検証として残します。

## 非目標

- Discord APIやDOMの永久互換を保証すること
- 自動送信、reaction、edit、deleteを追加すること
- raw Discord dataを公開・外部共有すること
- PlaywrightをDiscord本文取得の既定経路にすること
- 全領域を一度に書き換えること
- push、PR、release、repository visibility変更を自動化すること

