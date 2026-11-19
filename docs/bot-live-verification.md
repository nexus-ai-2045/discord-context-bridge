# ボットの対象別アクセス確認

資格情報の設定済み状態と、指定した対象を取得できる状態を分けます。
対象を指定しない一般の状態確認では、本文取得の経路を利用可能にしません。

```bash
python3 scripts/discord_bot_live_verify.py --expected-url "<Discord対象URL>" --json
python3 scripts/discord_bot_route_preflight.py --expected-url "<Discord対象URL>"
python3 scripts/discord_plugin_route_status.py --expected-url "<Discord対象URL>" --json
```

最初のコマンドだけが外部サービスへアクセスします。ボット 本人、対象サーバーへの所属、
対象チャンネルへのアクセスを、読み取り用の `GET` 要求で確認します。
本文の取得、権限変更、送信は行いません。後続の状態確認は保存された記録を検証します。

すべての確認が成功すると、設定ディレクトリの `live-verification.json` に受領記録を保存します。
保存先の権限は `0600` とし、一時ファイルからの置き換えで書き込みます。
生の識別子、`URL`、資格情報、ダイジェスト、保存先を公開出力へ含めません。

記録の検証では、現在の資格情報、正規化した対象、チャンネル種別、有効期限、署名を照合します。
未指定の対象、別の対象、資格情報の変更、改変、期限切れ、シンボリックリンク、広すぎる権限の記録では停止します。
記録は通常1時間で期限切れとなり、有効期間は最大24時間に制限されます。
履歴取得では資格情報を一度だけ読み込み、同じ値で記録を検証して `API` 要求に使います。
記録は同じローカル資格情報への結び付けであり、その資格情報を持つ主体から独立した証明ではありません。

アクセス確認が対応するチャンネル種別の正本は `SUPPORTED_TARGET_CHANNEL_TYPES` です。
テキスト、お知らせ、各スレッド、フォーラム、メディアに限定します。
本文履歴の対応種別は `MESSAGE_HISTORY_CHANNEL_TYPES` とし、テキスト、お知らせ、各スレッドに限定します。
フォーラムとメディアの親チャンネルは本文履歴を直接要求せず、既存のスレッド一覧取得へ案内します。

本文の取り込みや検証用コマンドにも `--expected-url` を渡します。

```bash
python3 scripts/discord_main_route_smoke.py --input /private/path/input.txt --expected-url "<Discord対象URL>" --json
```

設定ディレクトリの解決は既存の資格情報取得処理と共通です。`--channel-dir` の明示指定を優先し、
未指定時は `DISCORD_CONTEXT_BRIDGE_CHANNEL_DIR` と既存の既定値を使います。
内部ブラウザを可視読取の既定にする順序は変更しません。
