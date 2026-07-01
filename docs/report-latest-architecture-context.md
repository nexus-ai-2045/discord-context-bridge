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

## 仕組み化すべきもの

`discord_bridge_latest_snapshot_report.v1` と acquisition context の schema contract を fixture 化します。
unit test だけでなく、公開してよい key と出してはいけない key を固定する regression guard にします。

source / acquisition taxonomy を registry 化します。
例: `saved_snapshot`, `source_command`, `chrome_dom_snapshot`, `ocr_snapshot`, `clipboard_snapshot`, `unknown_legacy_snapshot`。
adapter ごとに自由記述で source 名を増やすのではなく、public report が意味を説明できる分類へ寄せます。

`report-latest` を ops check の小さな smoke に入れます。
合成 snapshot store を使い、Chrome や Discord 実機なしで `raw_text_returned=false`、`new_capture=false`、`live_browser_access=false` を確認します。

private producer contract を明文化します。
producer は snapshot 作成時に acquisition context を保存し、public core はそれを読むだけにします。
public core が browser profile、cookie、token、実 Discord DOM へ触れない境界を contract と test の両方で固定します。

古い snapshot の backfill policy を決めます。
`acquisition_context` がない legacy record は fallback 表示してよいですが、将来は migration か normalization で `unknown_legacy_snapshot` へ揃えます。

## 人間レビュー観点

公開文書や PR 本文では、`Chrome DOM` を live browser access と誤読させない表現になっているかを確認します。

private corpus、個別 project 名、実ユーザー名、実 Discord ID、local path、raw Discord text が混ざっていないかを確認します。

`--include-preview` の扱いを確認します。
developer verification として残す場合でも、既定出力・CI・PR 添付・公開ログでは使わない前提を維持します。

`report-latest` を `scripts/ops_check.py` に入れる場合は、実 Discord 依存ではなく合成 fixture だけで閉じていることを確認します。

## 完了条件

この領域の変更は、少なくとも次を満たしたときだけ完了とします。

- `python3 -m pytest tests -q` が成功する。
- `python3 scripts/ops_check.py` が成功する。
- public repo の diff に private data、raw Discord text、local absolute path がない。
- report の実行方法と snapshot の取得元が別 field として説明されている。
