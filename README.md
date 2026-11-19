# Discord Context Bridge

Discordで読める会話を手元の非公開領域へ保存し、AIが安全に理解・確認できる文脈へ変換します。

[![Discord Context Bridgeの流れ](https://raw.githubusercontent.com/nexus-ai-2045/discord-context-bridge/main/docs/assets/readme-flow.svg)](docs/operating-contract.md)

| 見つける | 保存する | 理解する | 判断する |
|---|---|---|---|
| Discordで対象を選ぶ | 非公開の追記型台帳へ残す | 目的・前提・決定事項を整理する | 人間が返信や次の行動を決める |

> [!IMPORTANT]
> Discord Context Bridge（DCB）の公開部分は、Discordへ投稿しません。トークン、cookie、webhook、実ID、参加者名、会話本文を公開出力へ含めません。

## 目的

分散したDiscord会話を読み直す負担を減らし、返信や次の行動を決める時に、保存範囲・鮮度・参照の根拠を確かめられるようにします。

## できること・しないこと

| できること | しないこと |
|---|---|
| 会話の目的、前提、決定事項を整理する | Discordへの送信、リアクション、編集、削除 |
| 返信案を送る前にレビューする | ブラウザのプロファイルからトークンやcookieを抜き出すこと |
| 保存済み会話の範囲と鮮度を確認する | OCR、スクリーンショット、ヘッドレスブラウザを本文取得の既定にすること |
| 「完全」「最新」と言ってよいかを保存記録で判定する | 保存記録なしに「全部取得した」と表明すること |
| コマンドライン、エージェント用スキル、MCPから同じ安全境界で使う | 公開範囲の変更や外部共有を人間の承認なしに行うこと |

## 0.12.0の変更

| 変更 | 効果 |
|---|---|
| ブラウザ取得の観測URL照合 | 取得直前に見ていたURLが要求先と違う時、ログイン画面へ移った時、本文が空の時は保存しません |
| 表明ゲート | 「完全」「最新」「理解済み」と言う前に、正本の取得記録と鮮度を再監査します |
| ボット経路のアクセス確認 | 資格情報があるだけでは取得可能と判定せず、対象ごとのアクセス確認記録を求めます |
| 送信後の照合 | 人間が送った後の記録を、正本台帳の対象・時刻・ハッシュと照合します |
| 取得試行の識別 | 同じ対象を取り直しても、試行ごとの証拠が混ざりません |
| 不正な観測行の失効 | 壊れた観測行を書き換えずに、失効記録を追記して切り離します |
| 出力先と読み込みの安全化 | REST backfillの出力先では資格情報領域とシンボリックリンク経由の書き込みを、デスクトップキャッシュの読み込みではシンボリックリンクの項目を拒否します。`bridge-intake --input` など他の入力ファイルには適用されないため、非公開のファイルを渡してください |

変更の全体は[変更履歴](CHANGELOG.md)、公開履歴は[GitHub Releases](https://github.com/nexus-ai-2045/discord-context-bridge/releases)を参照してください。

## クイックスタート

AIエージェントへ[このリポジトリ](https://github.com/nexus-ai-2045/discord-context-bridge)を渡し、次のように依頼してください。

「先に危険レビューしてください。削除、GitHub書込み（write）、公開範囲（visibility）、認証情報（secret）、未確認（unknown）を安全だと決めつけず、READMEと運用契約を読んで保存先と取得経路を確認してください。外部送信・設定変更・公開の前に人間レビューを通してください」

## 手動導入

| 手順 | 操作 | 結果 |
|---:|---|---|
| 1 | Python 3.11以上で `python -m pip install .` を実行する | コマンド `discord-context-bridge` が使えるようになる |
| 2 | 下の取得経路の順に、読める本文を非公開のファイルへ用意する | トークンやcookieを抜き出さずに入力できる |
| 3 | `bridge-intake` にURLとファイルを渡す | 保存、取得範囲の確認、文脈整理が一度に進む |

ブラウザで読んだ本文を渡す時は、取得経路（`--capture-route in_app_browser` または `chrome_extension`）と取得直前に観測したURL（`--observed-url`）を付けます。引数の詳細とWindowsでの例は[詳細リファレンス](docs/full-reference.md)の「最短の使い方」を参照してください。

MCPサーバーを使う場合は、MCP用の追加依存関係を含めて `python -m pip install ".[mcp]"` でインストールします。

### 保存先を選ぶ

共有スナップショットの既定の保存先は、ユーザーホーム配下の `Projects/Documents/discord/raw-snapshots` です。このフォルダー構成や端末間の同期が、すべての導入先にあるとは限りません。自分の非公開の保存領域を選び、`configure-local-cache --shared-snapshot-root`、環境変数 `DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT`、または手元の設定の `shared_snapshot_root` で指定してください。`configure-local-cache` は既定では検査だけで、`--apply` を付けた時だけ設定を保存します。保存先を指定したことと、同期・アクセス権・全履歴の取得を確認できたことは別です。

## 取得経路の順序

上から順に試し、使えない時だけ次へ進みます。

| 順 | 経路 | 使う条件 |
|---:|---|---|
| 1 | ボットの読み取り専用経路（ライブ受信、REST） | ボットが対象に参加し、アクセス確認記録がある |
| 2 | ボットの受信箱、非公開アダプター | 事前に設定済み |
| 3 | AIエージェントの内部ブラウザ | 1と2が使えない時の可視取得の既定 |
| 4 | Chrome拡張 | 内部ブラウザが使えない時だけ。人間の承認が必要 |
| 5 | 手動で用意した本文ファイル | 人間が貼り付けた本文を取り込む |

クリップボードは、利用者が明示した時だけ読みます。詳しくは[取得経路](docs/routes.md)と[運用契約](docs/operating-contract.md)を参照してください。

## やりたいことから選ぶ

| やりたいこと | 入口 |
|---|---|
| 取り込みから文脈整理まで一括実行する | `bridge-intake` |
| 可視テキストだけ取り込む | `import-visible-text` |
| 会話の目的・前提・流れを確認する | `context-passport` |
| 返信に必要な文脈を判定する | `reply-context-plan` |
| 返信案をレビューする | `review-draft` / `guide-reply` |
| 保存範囲と鮮度を確認する | `coverage-report` / `report-latest` |
| 手元に保存済みの記録を確認する | `cache-inventory` |
| スレッド全文の取得計画と判定 | `thread-capture-plan` / `full-capture-gate` |
| 「完全」「最新」と言ってよいか判定する | `scripts/context_claim_gate.py` |
| ボットが対象を読めるか確認する | `scripts/discord_bot_live_verify.py`（[手順](docs/bot-live-verification.md)） |

## 保存モデル

| データ | 役割 |
|---|---|
| 追記型の台帳（ledger） | 取得した観測履歴の正本 |
| 投影（projection） | 台帳から作り直す最新状態 |
| 閲覧物（digest） | 文脈パスポートや返信ガイドなど |

同じ本文を取り直しても観測記録を追記し、`content_hash` や `changed` で差分を表します。既存の行は書き換えず、訂正や失効も新しい記録として追記します。保存先と複数端末での運用は[運用契約](docs/operating-contract.md)を参照してください。

一度の可視取得を保存できたことと、スレッド全文を取得できたことは別です。全文取得は、範囲の端、安定した再走査、添付、保存物の整合を `full-capture-gate` で確認して判定します。

保存先の履歴やハッシュの連鎖に破損が見つかった場合は、その行を無視して追記を続けず、停止してください。元データを保全し、運用契約に沿って検証・復旧します。

## 安全境界

- Discordへの送信、リアクション、編集、削除を実行しない
- 生の本文、認証情報、実ID、参加者名、手元の絶対パスを公開しない
- 生の本文（`raw Discord text`）と手元のパス（`local path`）は非公開領域に閉じ、外部操作（`outbound_actions`）は既定で無効にする
- `send_message()` は無効にし、`pr_scope_guard.py` で公開と非公開の差分を検査する
- Chromeのプロファイルからユーザートークン、cookie、localStorageを抜き出さない
- OCR、スクリーンショット、ヘッドレスブラウザを本文取得の既定経路にしない
- Windowsの保存先はファイルアクセス権（ACL）を別に確認する。POSIXの権限数値だけで非公開保存を保証しない
- 公開、外部共有、リポジトリの公開範囲の変更には人間のレビューと明示の承認を求める

### Discord Desktop 通知 metadata probe

通知probeは本文を読まず、通知の有無だけを確認する補助経路です。

- schema: `discord_notification_delta.v1`
- Trigger condition: 人間がDiscord通知を1件発生させる
- Fallback order: Notification Center → Unified Log → Cache.db
- blocked reason: `no_notification_observed` / `insufficient_metadata`
- safety: `text_output="omitted"`、`raw_payload_read=false`、`outbound_actions="disabled"`

## MCP

| 入口 | 用途 |
|---|---|
| `discord-context-bridge` | コマンドラインから取り込み・確認する |
| エージェント用スキル（runtime skill / plugin） | CodexやClaude Codeなどから呼び出す |
| `discord-context-bridge-mcp` | MCPクライアントへ標準入出力で接続する |
| `discord-context-bridge-mcp-http` | 認証付きHTTPで接続する |

すべて読み取り専用・メタデータのみの境界に従います。MCPにもDiscord送信の機能はありません。HTTP接続はBearer認証が既定で必須です。保存後に受領記録を検証しても、実際のDiscordへのアクセスや投稿が本物であることの証明にはなりません。

## 開発と運用

基本言語は日本語です。利用者向け文書とPR本文も日本語を既定にします。エージェント用スキルは `capability/manifest.yaml` と `docs/operating-contract.md` から生成し、生成済みの `SKILL.md` は直接編集しません。

変更後は次の検査を通します。

| 検査 | コマンド |
|---|---|
| 正本と生成スキルの一致 | `python3 scripts/verify_ssot_projection.py --json` |
| 取得経路の方針 | `python3 scripts/lint_ingest_route_policy.py --skip-local-claude-skill --json`（手元にClaude Code用スキルを配布済みなら、省略オプションを外すと配布物も検査します） |
| 表明ゲートの配線 | `python3 scripts/lint_context_claim_gate_wiring.py --json` |
| バージョンと変更履歴の整合 | `python3 scripts/bump_version.py --check` |
| テスト一式 | `python3 -m pytest tests -q` |

| 資料 | 内容 |
|---|---|
| [詳細リファレンス](docs/full-reference.md) | コマンドと運用手順 |
| [運用契約](docs/operating-contract.md) | 実行環境共通の安全境界 |
| [投稿の付随情報と対象を絞った検索](docs/message-metadata.md) | 投稿者・添付・編集・削除の保存と非公開検索API |
| [取得経路](docs/routes.md) | 取得経路と代替手段 |
| [Codex ingress](docs/codex-discord-ingress.md) | Codexから読む入口 |
| [将来アーキテクチャ案](docs/future-proof-architecture.md) | 採用前の耐久性設計案 |
| [公開チェックリスト](PUBLIC_RELEASE_CHECKLIST.md) | 公開前に確認すること |
| [ロードマップ](ROADMAP.md) | 今後の計画 |
| [Issue一覧](ISSUE_LIST.md) | 進行中のTODO |

ライセンスは[MIT License](LICENSE)です（著作権者 `nexus-ai-2045`）。
