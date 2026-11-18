# ADR 0001: Draft Before Understanding Gate を禁止する

## Status

Accepted

## Context

Discord Context Bridge は、Discord の可視文脈を読んで返信前支援を行う。
実地テストでは、スレッド内容や役割を十分に理解する前に返信下書きへ進めると、
「内容も分からずに参加表明している」ような不自然な提案が出ることが分かった。

特にコミュニティスレッドでは、次を確認する前に draft を出してはいけない。

- 元スレッドが何の場か
- 誰が何を求めているか
- 直近の未解決点は何か
- 既存SSOTがあるか
- サーバールール、チャンネルルール、スレッドルール、ピン留めがあるか
- 返信するなら、どの役割で入るのか

## Decision

`understanding_confirmed` が true になるまで、返信 draft、final candidate、copy block を作らない。

理解確認前に出してよいものは、以下に限定する。

- 観測範囲
- 既存SSOT / rules / pin の確認結果
- 3〜8個の理解サマリ
- 未確認点
- 次に読むべき範囲
- human gate の選択肢

理解確認前の human gate は次の選択肢を使う。

- `understanding-ok`
- `read-more`
- `wrong-thread`
- `missing-rules`
- `stop`

`understanding-ok` の後に初めて reply intent と draft を扱う。

## Consequences

良い影響:

- 内容理解なしの軽い参加表明を避けられる。
- 返信案より先に、ユーザーがスレッド理解を検査できる。
- 既存SSOTやルール確認の抜けを明示しやすい。
- Discordへの不用意な投稿・引用・役割引き受けを防ぎやすい。

トレードオフ:

- 返信案に到達するまで1ステップ増える。
- 勢いで短く返したい場面でも、最低限の理解確認が必要になる。

## Implementation Notes

今後の実装では次を反映する。

- 13工程の「仮下書き」を、理解確認後の工程へ移す。
- `fixture_13_step_e2e.py` は understanding gate を通った後に draft へ進む。
- `review-draft` は `--understanding-confirmed` なしでは警告または blocked にする。
- Markdown review artifact は先頭に understanding summary / rules check / missing context を置く。
- `copy_block` は human gate が `copy` を推奨できる状態でのみ ready 扱いにする。

## Test Evidence

2026-06-22 の実地テストでは、`シンギュラボ掲示板 / 科学する仏教` スレッドを読み、
内容理解前に「告知文づくりなら手伝えそう」という draft を出すのは早すぎると判定した。

正しい順序は、まずスレッド内容とルール照合を理解サマリとして提示し、
人間が理解を承認してから reply intent / draft に進むこと。
