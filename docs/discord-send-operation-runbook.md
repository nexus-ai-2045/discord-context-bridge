# Discord送信テスト運転表

このrunbookは、Discord Context Bridgeを「送信直前まで支える」ための手順です。
既定は人間送信です。この public core 自体はDiscordへ送信しません。自動送信を使う場合は private adapter 境界で `auto-send-preflight` を通し、ready packet が出た一回だけ実行します。

## すぐ使う手順

```mermaid
flowchart TD
  start["対象チャンネル/投稿先をsafe labelで決める"] --> review["review-draftで本文レビュー"]
  review --> stage["stage-discord-sendでfill-only packet作成"]
  stage --> dry["verify-chrome-fill-dry-runでdry-run確認"]
  dry --> fill["Chrome拡張または人間操作で下書き欄へ入力"]
  fill --> stop["送信ボタン手前で停止"]
  stop --> human["人間がテスト用チャンネルで送信判断"]
  human --> closeout["closeout-discord-sendで送信後metadata確認"]
  closeout --> status["send-operation-statusで6点チェック吸い上げ"]
  status --> prod["本番送信手順へ進むか判断"]

  dry --> auto_preflight["任意: auto-send-preflight"]
  auto_preflight --> adapter["private adapter が一回送信"]
  adapter --> closeout

  stage -. "public coreでは禁止" .-> auto_send["自動送信 / Enter送信 / 送信ボタンclick"]
  dry -. "禁止" .-> reaction["reaction / edit / delete"]
  closeout -. "出力しない" .-> raw["raw本文 / URL / snowflake / 参加者名"]
```

## 送信前 gate

送信補助を行う場合でも、このリポは Discord への実送信をしません。
送信前 gate は「どこへ、何を、どの経路で置こうとしているか」を止まって確認するためのものです。

### 送信経路照合

- webhook / bot / browser の投稿先が一致しない場合は送信しない。
- 通知用 webhook、別 guild の bot token、別用途の自動通知チャンネルを、目的チャンネルの代替経路として使わない。
- ブラウザで進める場合は、ブラウザタイトルや画面上のサーバー名・チャンネル名を title evidence として確認する。
- title evidence は `target_verified_by_browser_title` のような metadata に残し、raw URL / snowflake / handle を visible output に出さない。

### 自動送信 preflight

- 自動送信は `auto-send-preflight` が `ready_for_auto_send_adapter` を返した時だけ private adapter が実行できる。
- 必須条件は、明示承認、対象 route 一致、ready な `stage-discord-send` packet、ready な `verify-chrome-fill-dry-run` report、transport 設定、operator label、idempotency key、rollback plan、metadata-only audit log。
- `auto-send-preflight` が `blocked` の場合は送信しない。
- public core の `send_message()` は無効のままにする。private adapter は idempotency key を使って一回だけ送信し、送信後は `closeout-discord-send` を必ず残す。

### 添付と停止の扱い

- ファイル添付に失敗した場合は、送信せず blocked として止める。
- 添付前に、対象ファイルの存在、サイズ 0 でないこと、意図したファイル名であることを local evidence として確認する。
- Unicode 分解文字などでブラウザ upload が不安定な場合、repo外の一時 ASCII 名コピーまたは hard link は使ってよい。ただし元ファイルと同一内容であることを local evidence に残す。
- ユーザーが停止した場合は not_sent として閉じる。入力欄準備、添付試行、送信先確認は送信済みではない。

### closeout 出力

- closeout は DCB の metadata-only 状態として残す。
- 外部 action 状態は `not_sent` / `staged` / `human_sent` / `blocked` / `unknown` のどれかに分ける。
- evidence がない場合は `not_sent` / `blocked` / `unknown` のどれかで閉じる。

1. 対象チャンネル/投稿先を決める
   - 実URLやsnowflakeを公開ログに出さず、`test-channel` のようなsafe labelで扱います。

2. 送信本文をレビューする

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  --store .local/discord-context-bridge/events.ndjson \
  review-draft \
  --understanding-confirmed \
  --draft "まず前提を確認してから返事します。" \
  --json
```

3. 下書き入力用packetを作る

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  --store .local/discord-context-bridge/events.ndjson \
  stage-discord-send \
  --mode reply \
  --target-url "https://discord.com/channels/<guild>/<channel>/<message>" \
  --understanding-confirmed \
  --draft "まず前提を確認してから返事します。" \
  --json > .local/discord-context-bridge/staging-packet.json
```

4. dry-run / previewを通す

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  verify-chrome-fill-dry-run \
  --staging-packet .local/discord-context-bridge/staging-packet.json \
  --socket-preflight \
  --target-url-verified \
  --socket-after-navigation \
  --latest-target-snapshot-confirmed \
  --reply-ui-candidates 1 \
  --draft-matches-copy-block \
  --socket-pre-send \
  --json > .local/discord-context-bridge/fill-dry-run.json
```

5. テスト用チャンネルで人間が送信する
   - このリポは送信しません。
   - Chrome拡張や人間操作で下書き欄に入れたあと、最後の送信だけ人間が判断します。

5-a. 任意: private adapter の自動送信 preflight を通す

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  auto-send-preflight \
  --staging-packet .local/discord-context-bridge/staging-packet.json \
  --dry-run-report .local/discord-context-bridge/fill-dry-run.json \
  --explicit-auto-send-approval \
  --target-route-verified \
  --transport-label private-test-adapter \
  --transport-configured \
  --operator-label human-approved-operator \
  --idempotency-key "<send-once-key>" \
  --rollback-plan-reviewed \
  --audit-log-path .local/discord-context-bridge/auto-send-audit.jsonl \
  --json > .local/discord-context-bridge/auto-send-preflight.json
```

6. 送信後closeoutを取る

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  closeout-discord-send \
  --staging-packet .local/discord-context-bridge/staging-packet.json \
  --dry-run-report .local/discord-context-bridge/fill-dry-run.json \
  --human-sent-observed \
  --human-reviewed \
  --observed-text-status human-edited-and-reviewed \
  --unread-check-status none-unread \
  --observed-message-id "<message-id>" \
  --json > .local/discord-context-bridge/send-closeout.json
```

7. 既存ログから運転表を吸い上げる

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  send-operation-status \
  --staging-packet .local/discord-context-bridge/staging-packet.json \
  --dry-run-report .local/discord-context-bridge/fill-dry-run.json \
  --closeout-report .local/discord-context-bridge/send-closeout.json \
  --target-label test-channel \
  --rollback-plan-reviewed \
  --production-runbook-fixed \
  --json
```

## 送信済みログの事後closeout

既に人間送信が終わっているログを後から確認する場合は、事前の
`stage-discord-send`、`verify-chrome-fill-dry-run`、テスト送信gateを
通ったことにはしません。代わりに、送信後metadataだけを閉じる
事後closeoutとして扱います。

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  send-operation-status \
  --closeout-report .local/discord-context-bridge/posted-records/<closeout>.json \
  --target-label posted-reply \
  --target-environment production \
  --rollback-plan-reviewed \
  --production-runbook-fixed \
  --post-send-retrospective \
  --json
```

このモードが `post_send_retrospective_closed` になっても、次回以降の
正式送信フローでは通常どおり `stage-discord-send`、dry-run、テスト送信、
closeout を順に通します。

## 6点チェック

| チェック | OK条件 | 足りない時 |
|---|---|---|
| 対象チャンネル/投稿先の明示 | `target-label` と staging packet がある | safe labelを決める |
| 送信本文のレビュー | `stage-discord-send` が `ready_to_fill` | review-draft / understanding gateを通す |
| dry-run / preview | `verify-chrome-fill-dry-run` が `ready_to_fill` | URL、UI候補数、snapshot、copy block一致を直す |
| テスト用チャンネルで実送信 | 人間送信後のcloseoutが `closed` | テストチャンネルで人間が送信し、観測する |
| 送信ログ/失敗時回復確認 | closeout、未読0、回復手順レビュー済み | 修正投稿、停止、人間確認の手順を確認する |
| 本番送信手順の固定化 | runbookをレビュー済みにする | 本番前チェックリストを更新する |

## 現状ロジック

```mermaid
stateDiagram-v2
  [*] --> TargetMissing
  TargetMissing --> DraftReview: target-label + visible context
  DraftReview --> StageBlocked: understanding未確認 / copy block未ready
  DraftReview --> StageReady: stage-discord-send ready_to_fill
  StageReady --> DryRunBlocked: URL/UI/snapshot/copy不一致
  StageReady --> DryRunReady: fill-only dry-run ready_to_fill
  DryRunReady --> HumanSend: 下書き入力後に人間が送信判断
  HumanSend --> CloseoutBlocked: 未観測 / 未レビュー / 未読あり
  HumanSend --> CloseoutClosed: closeout closed
  CloseoutClosed --> OperationReady: rollback-plan + production-runbook確認済み
  OperationReady --> [*]
```

## 失敗時の扱い

- 誤送信の自動削除やeditはしません。
- 失敗時は、人間が対象チャンネルを見て、停止、修正投稿、再送、追記説明のどれにするか決めます。
- このリポが残すのはmetadata-onlyの状態です。本文、URL、snowflake、参加者名は出力しません。

## 本番前の最低条件

本番チャンネルで送信する前に、次をすべて満たします。

- `send-operation-status` が `ok: true`
- テストチャンネルでcloseout済み
- 本文は人間レビュー済み
- 送信先safe labelが明示されている
- 失敗時の回復方針が確認済み
- Discord送信は人間が最後に実行する
