# Discord Desktopキャッシュprobe 設計

## 目的

実行中のDiscord Desktopが使用する現行プロファイルを実測し、Chromiumのディスクキャッシュから対象Discord URLに関係するAPI応答をread-onlyで検査する。取得できた場合だけチャンネル種別・掲示板タグなどの設定を返し、キャッシュ欠落を本文欠落や取得完了と誤認しない。

## 採用方針

既存の `cache-first-intake` は `Documents/discord/raw-snapshots` を対象とするため、Discord本体のElectronキャッシュ検査は独立した `desktop-cache-probe` コマンドとして追加する。既存の追記台帳やsnapshot取得フローは変更しない。

コマンドの既定出力はmetadata-onlyとする。raw本文、HTTP header、完全URL、Discord ID、参加者名、token、cookie、local absolute pathは返さない。チャンネル名とタグ名は `--include-labels` が明示された場合だけprivate consoleへ返す。

## 入力と検出

- 必須入力: Discord channel URL
- 任意入力: `--user-data-dir`
- 解決順序: 明示指定、実行中Discord/Electronプロセスの `--user-data-dir`、OS標準候補
- macOS標準候補: `~/Library/Application Support/discord`
- Windows標準候補: `%APPDATA%\discord`
- Linux標準候補: `~/.config/discord`
- OS標準候補は実在確認できた場合だけ採用し、存在しない候補を推測で確定扱いしない
- 対象cache: `<user-data-dir>/Cache/Cache_Data`

標準出力には絶対pathを返さず、`explicit` / `running_process` / `platform_default` の検出元だけを返す。Mac / Windows間でraw cacheを同期対象にはせず、各hostでprobeしたmetadata-only結果だけを同じschemaへ正規化する。

## Chromium Simple Cache解析

Chromium公式のSimple Cache形式に従う。

1. `SimpleFileHeader` のmagic、version、key lengthを検証する。
2. 末尾の `SimpleFileEOF` からstream 0の長さとkey SHA-256有無を読む。
3. stream 1をHTTP body、stream 0をHTTP response metadataとして境界分離する。
4. bodyはplain JSONまたはgzip JSONだけをv1で扱う。未知のencodingは `unsupported_encoding` として数える。
5. JSONを再帰走査し、対象channel IDと完全一致するobjectだけを候補にする。
6. `type`、`name`、`available_tags[].name` だけを許可フィールドとして抽出する。

v1ではHTTP header本文を解釈・出力せず、認証情報探索もしない。cache keyは対象一致判定にだけ使い、表示しない。

## 結果状態

- `metadata_found`: 対象objectと設定を取得
- `reference_only`: 対象参照はあるが設定objectがない
- `cache_missing`: 現行cacheに対象参照がない
- `unsupported_cache_format`: magic/versionが未対応
- `unsupported_encoding`: 対象候補のbody encodingが未対応
- `user_data_dir_unresolved`: 現行プロファイルを確定できない

出力には候補file数、parse成功数、失敗理由別件数、対象object数、タグ数を含める。`--include-labels` なしでは名前を件数へ落とす。

## 安全境界

- read-only。Discordへのsend、reaction、edit、deleteを行わない。
- cache fileやDiscordプロセスを変更・停止しない。
- token、cookie、Local Storage本文、Session Storage本文を読まない。
- キャッシュは不完全・削除可能なため、0件を「Discordに存在しない」と解釈しない。
- public repoのfixtureは人工データだけを使い、実サーバー名・ID・本文・pathを含めない。

## 検証

- Simple Cache v5の人工fixtureでplain JSONとgzip JSONを検証する。
- magic/version不一致、破損EOF、未知encoding、対象不一致を検証する。
- `--include-labels` の有無で出力境界を検証する。
- 実キャッシュではmetadata-only smokeを実行し、stdoutにURL、Discord ID、absolute path、本文断片が出ないことを確認する。
- 既存test suiteとboundary checkを実行する。

## 完了条件

`desktop-cache-probe` が現行プロファイルを実測し、対象について `metadata_found` または欠落理由を返すこと。実キャッシュにタグ設定がなければ `reference_only` を正常結果とし、タグを捏造・別チャンネルから流用しない。
