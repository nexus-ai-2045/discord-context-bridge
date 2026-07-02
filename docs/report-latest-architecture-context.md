# report-latest architecture context

obsidian_check: checked
scope_route: public-discord-context-bridge / report-latest / architecture-context / no-private-data / no-discord-action

この文書は、`report-latest` を今後拡張するときに残すべき前提と、仕組み化した方がよい境界をまとめる公開用メモです。
特定の private corpus や個別 project の現状はここへ持ち込まず、public package の設計判断だけを残します。

## 残すべき context

`report-latest` は「最新の Discord を読む」コマンドではありません。
保存済み snapshot store を読み、最後に保存された可視 message の metadata と coverage を返す report コマンドです。

実行時に Chrome へ接続しません。DOM を再取得しません。新しい capture を作りません。
`source_kind=Chrome DOM` が出る場合、それは snapshot 保存時の取得元を表す metadata であり、report 実行時の live access ではありません。
report 実行そのものの方法は `report_acquisition_context.mode=existing_saved_snapshot` で別枠に保持します。

public package は private adapter を内包しません。
private adapter は stdout contract や保存済み snapshot を作る側で、public core は schema、metadata-only report、coverage 判定を担当します。

出力は既定で raw 本文、発言者名、実 URL、local absolute path を含めません。
`raw_text_returned=false` を維持し、本文 preview が必要な developer verification は明示 opt-in として扱います。

## 実装済みの仕組み化

`report-latest` の metadata-only 境界は `scripts/report_latest_smoke.py` と
`scripts/ops_check.py` で検証します。
合成 snapshot store だけを使い、Chrome や Discord 実機なしで
`raw_text_returned=false`、`new_capture=false`、`live_browser_access=false`
を確認します。

この smoke は「保存済み snapshot を読む report」と「snapshot を作る adapter」を分けるための
最小運用保証です。実 data の再取得、Chrome 接続、DOM 再取得、入力欄観測は行いません。

`report-latest` の公開 contract は `schemas/` と `scripts/report_latest_schema_check.py`
で検証します。外部 dependency を増やさず、標準ライブラリの subset validator で
latest snapshot report、acquisition context、report acquisition context、body evidence の
必須 key と metadata-only 不変条件を確認します。

## 仕組み化すべきもの

source / acquisition taxonomy を registry 化します。
例: `saved_snapshot`, `source_command`, `chrome_dom_snapshot`, `ocr_snapshot`, `clipboard_snapshot`, `unknown_legacy_snapshot`。
adapter ごとに自由記述で source 名を増やすのではなく、public report が意味を説明できる分類へ寄せます。

private producer contract を明文化します。
producer は snapshot 作成時に acquisition context を保存し、public core はそれを読むだけにします。
public core が browser profile、cookie、token、実 Discord DOM へ触れない境界を contract と test の両方で固定します。

古い snapshot の backfill policy を決めます。
`acquisition_context` がない legacy record は fallback 表示してよいですが、将来は migration か normalization で `unknown_legacy_snapshot` へ揃えます。

## 次に残すべき context

`report-latest` の設計では、保存済み record の provenance と report 実行時の provenance を別の layer として扱います。
保存済み record は「いつ、どの adapter が、どの境界で snapshot を作ったか」を持ち、
report 実行時は「既存 store を読んだだけか」を持ちます。
この 2 layer を混ぜると、`Chrome DOM` が live access に見えたり、
cache 由来の record が再取得済みに見えたりするため、今後の schema でも分離を維持します。

public package の責務は、Discord 取得の成功率を競うことではありません。
安全に読める metadata contract を固定し、private adapter が渡した保存済み snapshot を
public-safe な report / passport / gate に変換することです。
adapter 比較、browser automation、OCR 改善は public core に戻さず、private producer 側の品質管理として扱います。

ops check は「実機が使えるか」ではなく「public core の保証が壊れていないか」を見る場所です。
そのため、今後追加する smoke も合成 fixture と public-safe metadata を既定にします。
実 Discord / Chrome / local profile / token / cookie を必要とする検証は、別の private adapter verification に隔離します。

## 次に仕組み化する候補

1. `source_kind` の taxonomy を単一 registry に寄せ、自由記述の source label と公開 report 用の分類を分ける。
2. legacy snapshot の normalization を追加し、`acquisition_context` 欠落時は `unknown_legacy_snapshot` として明示する。
3. private producer contract の fixture を追加し、producer が保存してよい metadata と保存してはいけない本文・path・credential を分ける。
4. `--include-preview` を developer verification として維持するか、public CLI から隠すかを人間レビューで決める。

## 人間レビュー観点

公開文書や PR 本文では、`Chrome DOM` を live browser access と誤読させない表現になっているかを確認します。

private corpus、個別 project 名、実ユーザー名、実 Discord ID、local path、raw Discord text が混ざっていないかを確認します。

`--include-preview` の扱いを確認します。
developer verification として残す場合でも、既定出力・CI・PR 添付・公開ログでは使わない前提を維持します。

`report-latest` を `scripts/ops_check.py` に入れる場合は、実 Discord 依存ではなく合成 fixture だけで閉じていることを確認します。

今後の仕組み化では、schema / taxonomy / producer contract の順に進めるのが安全です。
schema は公開 API の形を固定し、taxonomy は意味の揺れを減らし、producer contract は private adapter との責務境界を固定します。
`--include-preview` は便利さと漏えいリスクが衝突するため、最後に人間レビューで扱います。

## 完了条件

この領域の変更は、少なくとも次を満たしたときだけ完了とします。

- `python3 -m pytest tests -q` が成功する。
- `python3 scripts/ops_check.py` が成功する。
- public repo の diff に private data、raw Discord text、local absolute path がない。
- report の実行方法と snapshot の取得元が別 field として説明されている。
