# Discord Context Bridge process boundary

obsidian_check: checked
scope_route: public-discord-context-bridge / process-boundary / no-private-data / no-discord-action

この文書は、Discord Context Bridge の開発が遠回りしないように、目的、主経路、やらないこと、PR 境界を固定するための公開用メモです。

スレッド観測から返信候補までの初期13工程は
`references/initial-thread-ruleset.md` を正本にします。

## 目的

Discord Context Bridge の目的は、Discord を自動操作することではありません。

目的は、人間のユーザーが公開チャンネルやスレッドに入る前に、見えている文脈、温度感、ルール、暗黙前提を確認し、送信前の下書きを自分で調整できるようにすることです。

## 主ユーザー行動

1. ユーザーが Discord の見えている文脈を用意する。
2. bridge が文脈パスポートや返信前レビューを作る。
3. ユーザーが自分で読む。
4. ユーザーが必要なら下書きを直す。
5. 実際の送信は Discord 側でユーザーが行う。

## 主経路

public package の主経路は `--source-command` で local observer の stdout を読むことです。

public package は Discord login、cookie、token、webhook、browser profile を持ちません。Accessibility、OCR、browser automation などの private adapter は public package の外に置き、stdout contract だけを渡します。

CLI、MCP、plugin、ChatGPT connector、実機 capture は任意 adapter / developer
verification です。13工程MVPの成立条件にはしません。MVP 判断は
`references/initial-thread-ruleset.md` に戻します。

## Notification metadata probe gate

Discord Desktop の通知実測は、before / after の metadata-only probe として扱う。
public core に本文取得 route を戻さない。

読んでよいもの:

- `bundle_id`
- safe label
- file list
- mtime / size
- count
- `changed_paths`
- `delta_bytes`
- `first_changed_at` / `last_changed_at`
- `confidence`

読まないもの:

- notification title / subtitle / body
- Unified Log `eventMessage`
- SQLite BLOB payload
- raw Discord text
- 実 guild / channel / user / message ID

有効な終端状態:

- `discord_notification_delta.v1`
- `no_notification_observed`
- `insufficient_metadata`

この probe は human が実際に通知を発生させた時だけ測る。bridge / agent は送信、
返信、reaction、delete、外部投稿を行わない。

## 保存単位

保存してよいもの:

- 合成 fixture
- event store の schema
- 文脈パスポートの public-safe な構造
- private adapter から受け取る stdout contract
- blocker の分類名
- 永続化前に pseudonymize された参加者 alias と `text_snippet="omitted"`
- snapshot の source kind / acquisition context / report acquisition context
- `raw_text_returned=false` を満たす metadata-only latest report

保存しないもの:

- 実 guild ID、channel ID、user ID
- private message 本文
- real handle
- raw import text
- local absolute path
- token、cookie、webhook、browser profile
- 実会話のスクリーンショット

`source_kind=Chrome DOM` は、保存済み snapshot の取得元を表す metadata です。
`report-latest` の実行時に Chrome 接続、DOM 再取得、新規 capture をしたことは意味しません。
その区別は `report_acquisition_context.mode=existing_saved_snapshot` として出力します。

## 最小E2E

最小E2Eは、実 Discord 本文ではなく合成 fixture で確認します。
これは実装検証用であり、ユーザーが CLI、MCP、plugin、ChatGPT connector、
実機 capture を使うことを MVP の前提にはしません。

検証レイヤは次の3つに分けます。

| レイヤ | 目的 | MVP での扱い |
|---|---|---|
| smoke | 個別工程が壊れていないかを合成 fixture で見る | 必須 |
| preflight | raw 本文、secret、local path、outbound action が混ざらないかを見る | 必須 |
| fixture E2E | 13工程が入口 metadata から handoff まで通るかを見る | 必須 |

live capture、browser automation、OCR、bot route は任意 adapter の検証であり、
13工程E2Eが green になるまでは MVP 経路に入れません。

```bash
python3 scripts/ops_check.py
python3 -m pytest tests -q
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  context-passport \
  --input tests/fixtures/thread_context_passport.txt
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  review-draft \
  --draft "前提を確認してから返信します。"
```

送信テスト運用では `stage-discord-send` を使い、Chrome 拡張が実行してよい範囲を
`fill-only` packet として固定します。packet は socket preflight、対象 URL 照合、
reply UI / message box への下書き入力、pre-send ping、`stop_before_send_button`
までを許可します。`Enter` 送信、送信ボタン click、reaction、edit、delete は
常に禁止です。実際に Discord 通知を発生させる最後の操作は human の責任範囲です。

## やらないこと

- Discord へ送信しない。
- reaction / delete / edit を実装しない。
- user token / selfbot を扱わない。
- public repo に private adapter の credential や local profile を置かない。
- 取得経路比較を目的化しない。

## fallback

| 状態 | fallback |
|---|---|
| Accessibility が空読み | OCR private adapter へ切る |
| OCR が不安定 | copy / paste fixture または手動 visible text へ戻す |
| browser automation が必要 | private adapter に隔離し、public package には stdout contract だけ渡す |
| source command が失敗 | `adapter_failed` として人間語で原因を返す |

## route drift stop 条件

次が出たら、実装を止めてこの文書へ戻ります。

- 文脈パスポートより adapter 比較が中心になる。
- 送信や自動操作の話が主目的に戻っている。
- public repo に private data を入れそうになる。
- PR に FDE / Obsidian / 記事 draft が混ざる。
- 最小E2Eなしで周辺機能が増える。

## PR 境界

この public repo の PR は、Discord Context Bridge の public-safe なコード、docs、tests だけを含めます。

FDE 原則は `nexus-ai-2045/fractal-decision-ecosystem`、Nexus AI の私的運用や Obsidian/SSOT は `nexus-ai-2045/nexus_ai`、記事 draft は private draft 側に分けます。

PR を作る前に `python3 scripts/pr_readiness_preflight.py --fetch --json` を実行し、
PR 元ブランチが現在の `origin/main` を含むこと、対象 repo が
`nexus-ai-2045/discord-context-bridge` であること、GitHub account が一致することを確認します。

merge 後は `python3 scripts/post_merge_closeout_report.py --pr <PR番号> --fetch --json`
で、PR が merged、merge commit が存在、local `main` と `origin/main` が同期、working tree が clean
であることを確認します。
