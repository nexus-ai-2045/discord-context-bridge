# Chat Context 2026-07-06: history rewrite, privacy scrub, and site adapters

このメモは、2026-07-05 から 2026-07-06 にかけての `discord-context-bridge` 作業文脈を保存するための引き継ぎです。
raw Discord text、実 Discord URL、snowflake、参加者名、token、cookie、webhook、local absolute path は含めません。

## このチャットの役割

`discord-context-bridge` の実装本部として、Discord URL を入口にした読解、返信下書き、運用 closeout、公開境界、ローカルパス露出事故の封じ込めを扱った。

このチャットは、Discord 送信や外部投稿の自動化ではなく、読み取り、保存、下書き、レビュー、可視性変更の安全境界を整理するためのものだった。

## 決定したこと

- DCB の SSOT はリポジトリ本体であり、ローカル runtime skill はその投影として扱う。
- Discord URL が渡された時は、ai-party や外部 MCP に逸れず、DCB route order を先に使う。
- 返信目的の Discord 読解では、対象スレッド名やチャット名を collection title / chat title / saved context に反映する。
- raw Discord 本文や実 URL は public artifact に出さない。
- ローカルユーザー名を含む絶対パスは、repo、skill、報告文のいずれにも出さない。
- repository visibility 変更は公開境界であり、public 化は repo-specific approval まで止める。

## 完了した封じ込め

- 最新 tree の personal local path 露出を scrub した。
- runtime skill 側にも、展開済み local username path を出さない rule を追加した。
- GitHub repository を一度 private にし、main と tags の履歴を書き換えた。
- GitHub PR refs に残る履歴を消すため、repository を削除して private で再作成した。
- クリーン済み main と tags だけを再投入した。
- fresh mirror clone の all refs scan で、対象 personal path pattern が 0 件であることを確認した。
- `ops_check.py --skip-http` が全成功した。

## 現在の外部境界

- repository は private のまま。
- public に戻す操作は未実行。
- public 化には、対象 repository、exact command、README / license / SECURITY.md / secret scan / personal path scan / PUBLIC_READY.md の確認結果、commit history と files が Web visible になる reminder を提示したうえで、現在会話での明示 yes が必要。

## 追加で入った設計文脈

site adapter 方式を採用する。

目的は、Discord のような SPA で毎回その場の DOM 探索に依存せず、既知の selector、fallback、drift 判定、raw snapshot schema、metadata-only manifest を固定して、読めた文脈をローカルに失わず保存すること。

adapter は観測と保存だけを担当する。Discord send、reaction、edit、delete、外部投稿、GitHub visibility 変更は担当しない。

## 次に触る時の再開条件

1. public に戻す場合は、publication gate の確認文から再開する。
2. Discord URL から本文取得へ戻る場合は、DCB route order と site adapter の raw capture / manifest 方針から再開する。
3. 返信下書きへ戻る場合は、最新 snapshot の有無、スレッド名、返信目的、read-more 要否を確認してから draft に入る。

## Stopline

- Discord send、auto reply、reaction、edit、delete は自動化しない。
- repo visibility 変更、外部投稿、リリース告知は現在の明示承認なしに実行しない。
- local absolute path、raw Discord text、参加者名、実 URL、snowflake は public-safe docs に出さない。
