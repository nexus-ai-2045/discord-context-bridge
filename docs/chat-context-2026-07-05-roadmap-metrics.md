# Chat Context 2026-07-05: roadmap, metrics, and next development

このメモは、2026-07-05 のチャットで決めた `discord-context-bridge` の開発文脈を保存するための引き継ぎです。
raw Discord text、実URL、snowflake、参加者名、token、local absolute path は含めません。

## 今回の判断

- リポ内の public-safe core、運用チェック、PR/closeout は完了扱い。
- 理想体験は、Discord側では対象メッセージ / スレッド / チャンネルを見つけるだけにし、その後の文脈取得、パース、返信前判断、下書き、送信前後の確認を bridge 側で扱うこと。
- 次の開発主軸は、送信そのものではなく `message found -> bridge intake -> conversation parser quality`。
- 送信テストは、Discord自動送信ではなく、人間送信の前後を安全に閉じる rehearsal として扱う。
- README は短い入口と図に寄せ、詳細は runbook / full reference / roadmap へ分離する。

## マイルストーン

| Milestone | 内容 | 状態 |
|---|---|---|
| M0 | public-safe core closeout | 完了 |
| M1 | Discordで見つけた対象をbridgeへ渡す入口 | 次 |
| M2 | 軽量な上流ループ、速度/精度の計測 | 次 |
| M3 | conversation parser quality | 次 |
| M4 | 保守性のためのリファクタリング | M2-M3と並行 |
| M5 | テスト用チャンネルで送信 rehearsal E2E | M1-M4後 |
| M6 | operator UX | 進行中 |
| M7 | production send runbook | M5後 |
| M8 | optional capture adapters | 後段 |
| M9 | release/package | 後段 |

### 2026-07-13 更新

- M1: `bridge-intake` を含む返信生成入口へ最低文脈gateを適用。
- M2: `返信文脈契約` をfast profileへ追加し、GitHubレビュー前のローカル検出を短縮。
- M3: 時系列不明、履歴終端後の未解決参照、取得窓の後退を回帰テスト化。
- M4: replyとmentionの要件分離、CLI/MCP/bridge-intakeの共通不変条件を文書化。

## 指針となる数字

| 領域 | 指針 |
|---|---:|
| URL / snapshot intake | 1秒以内 |
| 保存済みsnapshotからの文脈整理 | 3秒以内 |
| 送信前 gate 判定 | 1秒以内 |
| full smoke | 10秒以内 |
| 入力サイズ | 直近50-100発言相当を標準 |
| cache再利用率 | 同一targetで80%以上 |
| parser必須field充足率 | fixtureで95%以上 |
| read-more誤停止率 | 実サンプルで10%未満目標 |
| raw/private漏えい | 0件 |
| outbound誤許可 | 0件 |
| PR差分サイズ | 原則12ファイル以内 |
| 回帰テスト | 変更ごとに100% green |

## 残っている開発

1. Discordで見つけた対象から、周辺会話をbridgeへ渡す入口を実運用で固める。
2. `snapshot-discord-url-text` から `context-passport` / `guide-reply` / `review-draft` へ進む導線を短縮する。
3. 上流処理を軽量化し、snapshot差分、safe metadata、cache、早期blockerで無駄な再parseを減らす。
4. parse時間、入力行数、抽出件数、read-more理由をmetadata-onlyで測る。
5. 実サンプルでparser品質を見て、足りない時の `read-more` blocker を人間語で出す。
6. gate判定、redaction、安全境界、CLI表示を共通化してリファクタリングする。
7. テスト用チャンネルで人間送信し、`closeout-discord-send` と `send-operation-status` を実ログで閉じる。
8. 失敗時回復方針を固定する。誤送信の自動削除やeditではなく、停止、修正投稿、追記、再送を人間判断にする。
9. 本番送信前チェックリストを `docs/discord-send-operation-runbook.md` に寄せる。
10. release採番、CHANGELOG、tag、公開レビューを実運用と同期する。

## Stopline

- Discord send、auto reply、reaction、edit、deleteは自動化しない。
- repo visibility変更、外部投稿、リリース告知は現在の明示承認なしに実行しない。
- MCPでも送信toolは公開しない。bridge は送信前後の安全確認係であり、送信ロボットではない。
