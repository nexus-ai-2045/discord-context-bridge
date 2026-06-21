# Live Test Reinforcements

2026-06-22 の Codex/Chrome Discord 実地テストで見つかった補強リスト。

## 反映済みの判断

- Draft Before Understanding Gate を禁止する。
  - 正本: [ADR 0001](adr/0001-understanding-before-draft.md)
- 返信案より先に、スレッド理解サマリと未確認点を出す。
- `understanding-ok` なしで draft、final candidate、copy block を作らない。

## 見つかった弱点

### URLだけではスレッド判定できない

現象:

- Chrome URL の path は channel route に見えた。
- しかし Discord DOM では `スレッド: 科学する仏教` と main region が見えていた。

補強:

- URL / title だけでなく、DOMヘッダー、main region、textbox label を観測して surface を決める。
- `has_thread_like_route=false` でも、DOMに thread header があれば `surface=discord_thread` と扱う。

### local observation を一時削除だけにしない

現象:

- 可視本文を一時ファイルに保存し、処理後に削除した。
- 実運用では local DB / observation store として残したい場合がある。

補強:

- raw Discord本文は repo に保存しない。
- 保存する場合は git 管理外の local observation store に置く。
- visible output には raw本文、参加者名、local path を出さない。
- observation store には `external_action: none` を保持する。

### rules / pin / SSOT 照合を draft 前に必須化する

現象:

- 初回の返信案は、サーバールール、スレッドルール、ピン留め確認の前に出てしまった。

補強:

- 既存SSOT、サーバールール、チャンネルルール、スレッドルール、ピン留めを先に確認する。
- 見つからなかった場合も `rules_missing` / `pin_missing` として理解サマリに出す。
- ルール未確認のまま draft に進まない。

### 内容理解なしの参加表明を禁止する

現象:

- 「告知文づくりなら手伝えそう」という案は、科学する仏教の内容理解や役割確認の前には早すぎた。

補強:

- まず「何のスレッドか」「誰が何を求めているか」「何が未解決か」を確認する。
- 返信目的は `content-understanding`, `draft-help`, `role-confirmation`, `watch-only` などに限定する。
- 役割を引き受ける文は、reply intent 確認後にだけ作る。

## 後続PR候補

1. `codex_discord_ingress_smoke.py` に DOM-derived surface metadata を追加する。
2. `fixture_13_step_e2e.py` に understanding gate を実装する。
3. `review-draft` に `--understanding-confirmed` gate を追加する。
4. local observation store の保存先とschemaを追加する。
5. Markdown review artifact の先頭に understanding summary / rules check を置く。
