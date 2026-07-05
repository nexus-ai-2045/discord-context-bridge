---
title: Site Adapter 方式
status: adopted
updated: '2026-07-06'
---

# Site Adapter 方式

DCB は、サイト別の軽量 adapter を採用する。目的は Discord のような SPA で毎回 DOM を探索し直すのではなく、既知の構造、fallback、drift 判定、保存 schema を固定し、読めた文脈を即座にローカル snapshot として失わないこと。

この adapter はブラウザ操作フレームワークではない。Discord 送信、reaction、edit、delete、外部投稿、GitHub 公開操作は担当しない。

## 採用形

- `site_adapters/index.json`: site adapter の一覧。
- `site_adapters/discord.v1.json`: Discord 用の抽出仕様。
- `schemas/dcb_raw_capture.v1.schema.json`: raw capture の保存 schema。
- `schemas/dcb_capture_manifest.v1.schema.json`: metadata-only manifest の保存 schema。

既存 OSS を丸ごと取り込むのではなく、Page Object Model 的に「Discord ページから何を抽出するか」を adapter 定義に閉じ込める。

## FDE への持ち出し方

この考え方は DCB 固有ではなく、FDE の観測面にも持っておく価値がある。POM を UI テストの実装パターンとしてではなく、外界の変わりにくい構造を読むための `surface adapter` として扱う。

FDE 側で持つべき単位:

- `surface`: Discord、GitHub、note、Chrome visible page、local file、automation log などの観測面。
- `adapter`: その surface から何を読むか、どの順で fallback するか、何を drift とするか。
- `raw_snapshot`: 読めた一次情報。private local を既定にする。
- `manifest`: 本文なしで、取得方法、範囲、drift、未帰属 cache、レビュー要否を示す。
- `decision_card`: raw ではなく manifest と sanitized summary を使って判断するカード。

この分離により、FDE は「毎回その場で読む」のではなく、「adapter で観測し、snapshot で固定し、manifest で drift を判断し、decision card で扱う」流れを標準化できる。Discord adapter はその最初の具体例として扱う。

FDE へ持ち出す時も、adapter は公開や外部送信の権限を持たない。adapter は観測と保存だけを担当し、公開、投稿、送信、PR、repository visibility 変更は別 gate の責務に残す。

## Discord v1 の抽出項目

必須項目:

- `source_url`
- `captured_at`
- `site`
- `adapter_id`
- `adapter_version`
- `capture_method`
- `read_scope`
- `messages[]`
- `messages[].ordinal`
- `messages[].visible_timestamp`
- `messages[].author_label`
- `messages[].body_text`
- `messages[].attachments[]`
- `messages[].extraction_confidence`

任意項目:

- `channel_or_thread_title`
- `viewport_state`
- `selector_hits`
- `fallback_used`
- `redaction_status`
- `unattributed_cache_candidates[]`
- `warnings[]`

raw JSON は private local only として扱う。通常の report や manifest では raw Discord 本文、参加者名、実 ID、token、cookie、webhook、local absolute path を出さない。

## Fallback 順

1. `structured_dom`: message container から message 単位で抽出する。
2. `body_inner_text`: `domSnapshot` が落ちた場合に `document.body.innerText` 相当を保存する。
3. `message_container_text`: body 全体が空または過剰な時に message container 候補の `innerText` を保存する。
4. screenshot / OCR は最後の補助であり、標準 route ではない。

読めた内容は、抽出品質が低くても raw snapshot として保存する。完璧な構造化を待って失うことを避ける。

## Drift 検出

drift は失敗ではなく、adapter 更新が必要な観測状態として manifest に残す。

- Discord URL なのに adapter が適用できない。
- message container selector の hit 数が 0。
- `document.body.innerText` には本文らしき文字があるが structured DOM が空。
- timestamp、author、body の欠落が連続する。
- 添付 URL はあるが message ordinal に紐づかない。
- 前回有効だった selector hit 率が大きく落ちる。
- viewport 末尾確認ができず、最新文脈と言えない。
- 主要 selector が複数同時に失敗する。

## Cache 候補

添付 URL や既存 cache は、本文または DOM 近傍から message に紐づくまで `unattributed_cache_candidates` に置く。近傍本文、timestamp、message ordinal、URL の一致が確認できた時だけ `messages[].attachments[]` へ昇格する。

## DCB への最小接続手順

1. 可視テキスト取得 route が `adapter_id` と `capture_method` を出す。
2. `domSnapshot` 失敗時の標準 fallback を `body_inner_text` と `message_container_text` に固定する。
3. 取得成功直後に raw JSON を `.local/discord-context-bridge/inbox/raw/` へ保存する。
4. manifest は本文なしで `.local/discord-context-bridge/inbox/manifests/` に保存する。
5. drift 判定を manifest に出し、ユーザー報告では `本文全文` と `添付紐づけ` を別々に表示する。
6. fixture HTML、innerText fixture、drift fixture で回帰テストする。

## 採用しないもの

- Playwright やスクレイピング OSS の大型依存を DCB の中核にはしない。
- adapter から Discord write API、送信ボタン、Enter 送信、reaction、edit、delete を触らない。
- raw Discord 本文や参加者名を公開向け artifact に出さない。
