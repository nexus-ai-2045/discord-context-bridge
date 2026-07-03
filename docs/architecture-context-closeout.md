# アーキテクチャ文脈 closeout

obsidian_check: checked
scope_route: public-discord-context-bridge / architecture-context-closeout / no-private-data / no-discord-action

この文書は、システムアーキテクチャ視点で残すべき context と、仕組み化すべき運用保証をまとめる公開用メモです。
個別の private corpus、実 Discord URL、snowflake、実ユーザー名、local absolute path、raw Discord text はここへ持ち込みません。

## 残すべき context

public package の責務は、Discord を操作することではなく、private adapter から渡された可視文脈を public-safe な passport、gate、report、closeout へ変換することです。

残すべき context は次の layer に分けます。

| 層 | 残すもの | 残さないもの |
|---|---|---|
| source provenance（取得由来） | `source_kind`、capture method、保存済み snapshot 由来かどうか | token、cookie、browser profile |
| content boundary（内容境界） | 件数、coverage、safe label、`raw_text_returned=false` | raw Discord text、実 handle、実 ID |
| human gate（人間停止線） | `human_reviewed`、`stop_before_send_button`、`fill-only` verdict | 送信 click、Enter 送信、reaction、edit、delete |
| post-send closeout（送信後確認） | `human_sent_observed`、`observed_text_status`、metadata-only status | observed URL、message snowflake、本文 |
| operation proof（運用証跡） | pytest、ops check、GitHub account guard、PR readiness 結果 | private adapter の credential、実会話ログ |
| context operating mode（文脈運用モード） | `triage`、`catchup`、`join-thread`、`boundary` の route、checklist、next_actions、`send_capability=disabled` | Discord 参加操作、送信、reaction、edit、delete |

`source_kind=Chrome DOM` のような値は、保存済み snapshot の取得元 metadata です。
report 実行時の live browser access とは分け、`report_acquisition_context.mode=existing_saved_snapshot` のように別 field で保持します。

## 仕組み化すべきもの

仕組み化は、便利な取得経路を増やすことより先に、責務境界を壊さない検査を増やす順で進めます。

1. architecture closeout document を public repo に置き、残す context と残さない context を固定する。
2. 文書不変条件を test に入れ、metadata-only、fill-only、no outbound action、no raw text が抜けたら失敗させる。
3. `scripts/ops_check.py` を実装後の最短保証にし、合成 fixture だけで public core の保証を確認する。
4. `triage-mode`、`catchup-mode`、`join-thread-mode`、`boundary-mode` は `文脈運用モード smoke` として ops check に含め、schema、停止線、raw 非返却を毎回確認する。
5. GitHub account / remote owner / git author は `scripts/gh_guard.py` と PR readiness で確認し、名義事故を Git 操作前に止める。
6. private adapter producer contract は別 layer で管理し、public core には stdout contract と metadata schema だけを渡す。

## 人間レビュー観点

人間レビューでは、次を確認してから commit / push します。

- public diff に raw Discord text、実 URL、snowflake、実 handle、local absolute path がない。
- `fill-only` が Discord 送信許可に読める表現になっていない。
- closeout が Codex の送信実行ではなく、人間送信後の metadata-only verification として説明されている。
- test / ops check / GitHub guard の結果が残っている。
- 4 つの context operating mode が判断 packet であり、送信・参加・reaction・edit・delete の実行 surface に昇格していない。
- PR title や文書 title が読みにくい固定 prefix に寄っていない。

## 完了条件

この領域の変更は、少なくとも次を満たしたときだけ完了とします。

- `python3 -m pytest tests -q` が成功する。
- `python3 scripts/ops_check.py` が成功する。
- `python3 scripts/gh_guard.py --account-only` が成功する。
- branch が `origin/main` から分岐し、作業木が clean になる。
- push 済み branch と未 merge / human-owned queue を分けて報告する。
