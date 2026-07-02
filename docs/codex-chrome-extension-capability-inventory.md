# Codex Chrome 拡張 capability inventory

> status: draft
> scope: Discord context bridge / Chrome extension operation boundary
> outbound_actions: disabled by default

このメモは、Codex Chrome 拡張で Discord を扱う時の「できること」と
「やってよいこと」を分けるための棚卸しです。Chrome 拡張は広い操作能力を持つため、
Discord では `read -> stage -> fill -> stop -> human send -> closeout` を標準 envelope とします。

## 残すべきコンテキスト

システムアーキテクチャ上、残す価値があるのは会話本文ではなく境界情報です。
次の文脈は、今後の runner / Chrome 拡張 / MCP tool の実装判断で再利用します。

| context | 残す理由 | 保存してよい形 |
|---|---|---|
| Chrome 拡張 capability | 実行能力と許可操作を分けるため | capability map、allowed / forbidden action |
| Discord send envelope | 下書き入力と実送信を混ぜないため | `read -> stage -> fill -> stop -> human send -> closeout` |
| `stage-discord-send` blockers | 自動化を止める理由を機械判定するため | blocker 名、status、safe label |
| socket evidence | 誤タブ、誤入力欄、二重送信を検知するため | preflight / after navigation / pre-send の結果 |
| human final boundary | 最後の Discord 通知発生を人間責務に固定するため | `outbound_actions=disabled`、human gate |
| post-send closeout | 送信後の残務を本文なしで閉じるため | `human_sent_observed`、`human_reviewed`、text status |

残さないものは raw Discord 本文、実参加者名、実 ID、token、cookie、local profile、
local absolute path です。これらは public package の architecture context ではなく、
private adapter 側の一時入力として扱います。

## 仕組み化候補

このメモは実装済み機能と次の仕組み化候補を分けます。実装する時は、先に
合成 fixture / local smoke / PR gate を追加してから runner 側へ接続します。

| priority | mechanism | done shape |
|---|---|---|
| P0 | `stage-discord-send` packet schema を runner contract として固定 | schema test、README pointer、MCP tool |
| P0 | Chrome 拡張 runner の preflight / after-navigation / pre-send dry-run check | `verify-chrome-fill-dry-run` の JSON report、CLI、MCP tool |
| P0 | human final send 後の metadata-only closeout | `closeout-discord-send` の JSON report、CLI、MCP tool |
| P0 | reply UI と通常 message box の selector drift 検知 | 一意に取れない時は `blocked` |
| P2 | fill-only 実行の dry-run / smoke command | draft を入れずに対象判定だけ返す |
| P2 | PR gate に forbidden action scan を追加 | `send_message` / `click_send_button` が allowed に混ざると fail |

仕組み化の基準は、Chrome 拡張が「できる」ことを増やすことではなく、
できる操作のうち Discord で「やってよい」範囲だけを機械的に狭めることです。

## 根拠

- Chrome skill は、既存 Chrome profile、ログイン済みセッション、開いているタブ、拡張状態が必要な作業で Chrome を使う入口です。
- Chrome runtime は `agent.browsers.*` API 経由で、タブ一覧、tab claim、新規タブ、URL 移動、DOM snapshot、click、fill、type、keypress、clipboard、screenshot、console log 読み取りを扱えます。
- `discord-context-bridge` は Discord への send / reaction / edit / delete を public core の既定動作に入れません。
- `stage-discord-send` は Chrome 拡張 runner へ渡す `fill-only` packet であり、実送信 packet ではありません。

## Capability map

| layer | できること | Discord での既定扱い | 主な失敗 |
|---|---|---|---|
| socket | Chrome 拡張へ接続し、タブ一覧を読む | 必須 preflight | 拡張未接続、対象タブ不在 |
| observe | URL、title、DOM snapshot、可視テキスト、screenshot、console log を読む | read-only OK | ログイン画面、仮想リスト、本文欠落 |
| navigate | 既存タブ claim、新規タブ作成、URL 移動、reload | 条件付き OK | 誤タブ、reload で状態喪失 |
| fill | textbox に fill/type、clipboard 経由で貼り付け | `stage-discord-send` が ready の時だけ OK | 誤入力欄、UI 変化、メンション誤爆 |
| action | click、keypress、DOM CUA 操作 | read/fill に必要な範囲だけ OK | 意図しないメニュー、誤クリック |
| external effect | Enter 送信、送信ボタン click、reaction、edit、delete | 原則禁止。human final | 二重送信、公開面への誤送信 |

## Discord send envelope

```text
read:
  - socket_preflight
  - claim_existing_discord_tab_or_open_target
  - verify_visible_url_matches_target

stage:
  - review-draft または guide-reply
  - stage-discord-send
  - blockers が空であることを確認

fill:
  - verify-chrome-fill-dry-run
  - reply UI または message box を一意に確認
  - draft を入力
  - socket_pre_send_ping

stop:
  - Enter 送信しない
  - 送信ボタンを押さない
  - ユーザーが目視確認できる状態で止める

human:
  - 最後の送信操作は人間が行う

closeout:
  - closeout-discord-send
  - human_sent_observed と human_reviewed を確認
  - 本文、URL、snowflake は出力しない
```

## `stage-discord-send` gate

`stage-discord-send` は次を満たす時だけ `staging_status=ready_to_fill` を返します。

- `understanding_confirmed=true`
- `copy_block.status` が `ready` または `split`
- `mode=reply` の時は Discord message URL がある
- `mode=mention` の時は Discord URL と `@safe-label` がある

blocked reason は、少なくとも次のように分けます。

| blocker | 意味 |
|---|---|
| `understanding_not_confirmed` | 文脈理解を人間が確認していない |
| `copy_block_not_ready` | 下書きが未生成、長すぎる、または危険 |
| `reply_target_message_url_required` | 返信先 message URL がない |
| `mention_target_discord_url_required` | メンション投稿先 URL がない |
| `mention_label_required` | 人間確認済みの `@safe-label` がない |
| `unsupported_mode` | `reply` / `mention` 以外の mode |

## `verify-chrome-fill-dry-run` gate

`stage-discord-send` のあと、Chrome 拡張 runner は下書き入力前に
`verify-chrome-fill-dry-run` を通します。これは実入力ではなく、観測結果だけを
検査する gate です。

`dry_run_status=ready_to_fill` になる条件:

- staging packet が `discord_send_staging_packet.v1` で `ready_to_fill`
- `socket_preflight=true`
- `target_url_verified=true`
- `socket_after_navigation=true`
- `mode=reply` では `reply_ui_candidates=1`
- `mode=mention` では `message_box_candidates=1`
- `draft_matches_copy_block=true`
- `socket_pre_send=true`

blocked reason は次を含みます。

| blocker | 意味 |
|---|---|
| `staging_packet_not_ready` | stage 側の blocker が残っている |
| `target_url_not_verified` | 対象 Discord URL と表示中 URL が一致しない |
| `reply_ui_not_found` / `reply_ui_not_unique` | reply UI が 0 件または複数件 |
| `message_box_not_found` / `message_box_not_unique` | 通常入力欄が 0 件または複数件 |
| `draft_mismatch` | 入力予定 draft が copy block と一致しない |
| `socket_pre_send_missing` | 送信前停止地点の socket ping が未確認 |

## `closeout-discord-send` gate

人間が Discord 側で最後の送信操作をした後、runner / agent は
`closeout-discord-send` で残務を metadata-only に閉じます。この gate は
実送信、編集、reaction、削除をしません。

`closeout_status=closed` になる条件:

- 任意で渡した staging packet が `discord_send_staging_packet.v1` で `ready_to_fill`
- 任意で渡した dry-run report が `chrome_extension_fill_only_dry_run.v1` で `ready_to_fill`
- `human_sent_observed=true`
- `human_reviewed=true`
- `observed_text_status` が `matches-copy-block` または `human-edited-and-reviewed`

blocked reason は次を含みます。

| blocker | 意味 |
|---|---|
| `staging_packet_not_ready` | stage 側が ready ではない |
| `dry_run_not_ready` | Chrome 拡張 dry-run が ready ではない |
| `human_send_not_observed` | 人間送信後の visible message を確認していない |
| `human_review_not_confirmed` | 送信後の見え方を人間が確認していない |
| `observed_text_not_checked` | 送信後本文状態が未確認 |
| `invalid_observed_text_status` | text status が定義外 |

出力は `observed_message_id_output=omitted`、`observed_url_output=omitted`、
`raw_discord_text_output=omitted`、`text_returned=false` を維持します。

## Socket checks

Chrome 拡張を使う時は、最低 3 点を evidence として残します。

| timing | check | 目的 |
|---|---|---|
| preflight | Chrome 拡張接続、タブ一覧、対象 URL 候補 | 操作対象を誤らない |
| after navigation | 現在 URL、title、DOM が対象スレッドを示す | 遷移失敗やログイン面を検知 |
| pre-send stop | 入力欄に入った後も socket が生きている | 送信前停止と二重送信防止 |

## Stoplines

- Discord への send / reaction / edit / delete を自動実行しない。
- `press Enter` や送信ボタン click を automation の完了条件にしない。
- raw Discord 本文、実参加者名、token、cookie、local profile、local absolute path を外向き出力しない。
- reply UI が一意に取れない時は、通常 message box に流し込まない。
- mention は `@safe-label` を人間が確認済みでない限り入れない。

## Test operation checklist

```text
obsidian_check: done
scope_route: Discord Chrome extension fill-only / external_action none until human send

[ ] socket_preflight が通った
[ ] target URL / title が一致した
[ ] stage-discord-send が ready_to_fill
[ ] reply UI または message box が一意
[ ] draft が copy_block と一致
[ ] pre-send socket ping が通った
[ ] automation は送信ボタン手前で停止した
[ ] final send は human 操作
[ ] closeout-discord-send が closed
```
