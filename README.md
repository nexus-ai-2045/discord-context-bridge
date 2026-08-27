# Discord Context Bridge

Discordで読める会話をローカルに保存し、AIが安全に理解・確認できる文脈へ変換します。

![Discord Context Bridgeの流れ](docs/assets/readme-flow.svg)

| 見つける | 保存する | 理解する | 判断する |
|---|---|---|---|
| Discordで対象を選ぶ | privateな追記型台帳へ残す | 目的・前提・決定事項を整理する | 人間が返信や次の行動を決める |

> [!IMPORTANT]
> Discord Context Bridge（DCB）のpublic coreは、Discordへ投稿しません。token、cookie、webhook、実ID、参加者名、会話本文を公開出力へ含めません。

## できること

- 会話の目的、前提、決定事項を整理する
- 返信案を送る前にレビューする
- 保存済み会話の範囲と鮮度を確認する
- CLI、runtime skill/plugin、MCPから同じ安全境界で使う

## 最短で使う

| 手順 | 操作 | 結果 |
|---:|---|---|
| 1 | Python 3.11以上でインストールする | CLIが利用可能になる |
| 2 | Discordの可視テキストをprivateなファイルへ用意する | tokenやcookieを使わず入力できる |
| 3 | `bridge-intake`へURLとファイルを渡す | 保存・取得範囲確認・文脈整理が一度に進む |

インストール: `python -m pip install .`

詳しい引数とWindows例は[詳細リファレンス](docs/full-reference.md)の「最短の使い方」を参照してください。

## やりたいことから選ぶ

| やりたいこと | 入口 |
|---|---|
| 取り込みから文脈整理まで一括実行する | `bridge-intake` |
| 可視テキストだけ取り込む | `import-visible-text` |
| 会話の目的・前提・流れを確認する | `context-passport` |
| 返信に必要な文脈を判定する | `reply-context-plan` |
| 返信案をレビューする | `review-draft` / `guide-reply` |
| 保存範囲と鮮度を確認する | `coverage-report` / `report-latest` |
| ローカルcacheを確認する | `cache-inventory` |

## 保存モデル

| データ | 役割 |
|---|---|
| 追記型ledger | 取得した観測履歴の正本 |
| projection | ledgerから再構築する最新状態 |
| digest | 文脈パスポートや返信ガイドなどの閲覧物 |

同じ本文を再取得しても観測記録を追記し、`content_hash`や`changed`で差分を表します。保存先とcross-device運用は[運用契約](docs/operating-contract.md)を参照してください。

## 安全境界

- Discordへの送信、reaction、edit、deleteを実行しない
- raw本文、認証情報、実ID、参加者名、ローカル絶対パスを公開しない
- `raw Discord text`と`local path`はprivate領域に閉じ、`outbound_actions`は既定で無効にする
- `send_message()`は無効化し、`pr_scope_guard.py`でpublic／private差分を検査する
- Chrome profileからuser token、cookie、localStorageを抽出しない
- OCR、screenshot、headless browserを本文取得の既定経路にしない
- 公開、外部共有、repository visibility変更には人間レビューと明示承認を求める

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
| `discord-context-bridge` | CLIから取り込み・確認する |
| runtime skill/plugin | Codexなどのエージェントから呼び出す |
| `discord-context-bridge-mcp` | MCPクライアントへstdio接続する |
| `discord-context-bridge-mcp-http` | 認証付きHTTPで接続する |

すべてread-only／metadata-only境界に従います。MCPにもDiscord送信toolはありません。HTTP接続はBearer認証が既定で必須です。

## 開発と運用

基本言語は日本語です。利用者向け文書とPR本文も日本語を既定にします。runtime skillは`capability/manifest.yaml`と`docs/operating-contract.md`から生成し、生成済み`SKILL.md`は直接編集しません。

| 資料 | 内容 |
|---|---|
| [詳細リファレンス](docs/full-reference.md) | CLIと運用手順 |
| [運用契約](docs/operating-contract.md) | runtime共通の安全境界 |
| [取得経路](docs/routes.md) | 取得経路とfallback |
| [Codex ingress](docs/codex-discord-ingress.md) | Codexから読む入口 |
| [将来アーキテクチャ案](docs/future-proof-architecture.md) | 採用前の耐久性設計案 |
| [ロードマップ](ROADMAP.md) | 今後の計画 |
| [Issue一覧](ISSUE_LIST.md) | active TODO |

ライセンスは[MIT License](LICENSE)です。
