# 初期スレッド ruleset

このファイルは、Discord Context Bridge が Discord 返信支援を始める前に
最初に決めることの SSOT です。

目的は Discord を自動操作することではありません。人間が特定の Discord
入口を選び、bridge が必要な文脈を集め、送信しない状態の返信候補を
レビュー可能にすることです。

サイドパネル向けの可視化は `references/initial-thread-ruleset.html` に置きます。

## スコープ

この ruleset は Discord Context Bridge 専用です。Note 記事制作、Note QA
evidence、note-publishing-suite verifier 修復、外部投稿、公開 gate は
このスコープに含めません。

public core に保存してよいのは、safe metadata、schema、label、件数、
local registry pointer、risk state、review artifact です。Chrome 観測、
download、文字起こし、OneFile 化は private / local adapter 側の責任であり、
public package に混ぜません。

## 主工程

1. **Chrome 観測入口**

   まず Chrome / 拡張で Discord ページを観測できるか確認します。
   可能なら Chrome で Discord を開き、人間が対象 channel / thread へ移動します。
   bridge が見るのは、その時点の入口だけです。URL、title、channel/thread label、
   source readiness、missing context を扱い、Discord 全体の巡回、送信、reaction、
   edit、delete は行いません。

2. **既存 SSOT registry 確認**

   入口が決まったら、返信判断の前に local registry SSOT を確認します。
   thread URL、channel label、server alias、safe thread key から、既存 context
   record、rule note、過去観測、local evidence path、OneFile / transcript artifact
   を探します。なければ新規 context 候補、古ければ `stale_context` とします。

3. **観測・資料取得範囲**

   返信支援に必要な範囲を決めます。優先して見るのは server rule、
   channel/thread rule、pin、過去トーク、直近トーク、添付、画像、リンク資料、
   link summary です。private / local 境界で許される場合は、資料を local 保存し、
   音声 / 動画を文字起こしし、Web 資料を OneFile などの local evidence artifact
   に落とします。取得できないものは `missing_context` として明示します。

   返信支援では、スレッド起点、返信対象、返信対象までの直前10件を最低取得量とします。
   全履歴が10件未満なら履歴終端を確認して全件を使います。指示語、引用、添付、
   過去回答などの未解決参照が残る場合は10件ずつ追加取得し、解決しないまま
   provisional draftへ進みません。

4. **文脈理解チェック**

   文脈理解を 3-9 個の要点にまとめ、1ページ程度で確認できるサイズにします。
   server rule、thread rule、直近やり取りは他より解像度を上げます。
   確定、推定、未確認を分けます。これは bridge が空気を読めているかを
   人間が素早く確認するための checkpoint です。

5. **理解確認 gate**

   ここで人間が `understanding-ok`、`read-more`、`wrong-thread`、`missing-rules`、
   `stop` のいずれかを選びます。`understanding-ok` になるまで、bridge は
   provisional draft、final reply candidate、`copy_block`、推奨投稿文を作りません。

6. **仮下書きプローブ**

   理解確認後にだけ provisional draft を作ります。これはまだ投稿候補ではありません。
   未確認前提、危ない言い回し、トーンのズレ、スレッド理解の誤りを見つけるための
   probe として使います。

7. **下書きリスクレビュー**

   provisional draft を、文脈適合、server rule 適合、thread rule 適合、tone、
   misread risk、missing premise、返信タイミング、参加者への負荷で確認します。
   返す状態は `go_candidate`、`revise`、`ask_context`、`wait`、`do_not_post`
   などの短い state にします。bridge は判断材料を出すだけで、投稿判断はしません。

8. **Markdown レビュー artifact**

   文脈理解、理解確認 gate、provisional draft、risk review、unknown、
   次の選択肢を Markdown 作業ファイルに保存します。サイドパネルや preview
   で読める形にします。
   人間はこの Markdown を直接編集してよく、編集後は bridge が再読込して
   focused diff / risk review を行えます。

9. **最終返信候補**

   review 後に final reply candidate を作ります。AI 生成でも、人間が Markdown
   内で直接編集したものでもよいです。人間が編集した場合は、その編集済み本文を
   source of truth とします。ここでもまだ投稿済みではありません。

10. **人間判断 gate**

   人間が次を選びます。copy に進む、修正する、追加文脈を読む、待って観測する、
   返信しない、のいずれかです。bridge は Discord の send button を押しません。

11. **コピー可能な出力**

   Discord に普通の返信として貼れる短さの `copy_block` を作ります。
   長すぎる場合はまず短縮します。それでも必要な場合だけ `split_copy_blocks`
   を作ります。3分割以上が必要な場合は、Discord 返信として投稿せず、
   別形式を提案します。clipboard insertion は任意で、人間の明示操作が必要です。

12. **送信後観測 / Context SSOT 更新**

   人間が送信した後、依頼があれば follow-up context を観測します。
   自動追撃返信、reaction、delete、edit は行いません。新しい返信支援は同じ
   context / review path から始めます。
   safe entry metadata、registry key、observed scope、rule summary、
   understanding points、final candidate、decision result、unresolved items を
   local context SSOT に更新します。実 ID、private raw message body、token、
   cookie、public-package-unsafe evidence は保存しません。

13. **終了・handoff**

   context が肥大化した時、作業を止める時、次チャットへ渡す時は handoff packet
   を作ります。含めるのは current state、read scope、confirmed rules、
   unresolved questions、final candidate status、stopline、next action です。
   handoff は local Markdown / metadata artifact であり、public release artifact
   ではありません。

## 任意割り込み

これらは主工程のどこにでも挟めます。必須工程ではありません。

- **壁打ち割り込み**

  人間がいつでも解釈を相談、反論、再構成できます。bridge は採用した変更、
  捨てた案、残った unknown を Markdown review artifact に反映します。

- **追加文脈取得割り込み**

  bridge または人間が追加文脈が必要だと判断した場合、過去ログ、rule、link、
  attachment、image、transcript 取得に戻ります。その後、文脈理解と risk check
  を再実行します。

- **直接編集割り込み**

  人間は Markdown review artifact や final reply candidate を直接編集できます。
  編集済み本文を次の review の source of truth とします。copy output の前に、
  length、tone、rule、missing premise を再確認します。

## Stopline

- Discord への送信、reaction、edit、delete、account action 自動化はしない。
- 全 server / channel を巡回しない。
- Discord token、cookie、browser profile、webhook URL を読まない、保存しない。
- raw private Discord text、private attachment、local username、local absolute path、
  private evidence artifact を public package に入れない。
- Note 記事作業、Note QA evidence、note-publishing-suite verifier 修復を
  Discord bridge scope に混ぜない。
