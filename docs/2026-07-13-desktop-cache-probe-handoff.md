---
title: Discord Desktop cache probe handoff
status: implemented-local
date: 2026-07-13
owner: discord-context-bridge
privacy: public-safe-metadata-only
---

# Discord Desktop cache probe handoff

## 結論

Discord Desktopが実際に使う `user-data-dir` を検出し、Chromium Simple Cache v5をread-onlyで解析する `desktop-cache-probe` を実装した。

Mac / Windows / Linuxの保存場所差分は取得adapterで吸収する。raw cache自体はhost間同期せず、probe結果だけを `discord_desktop_cache_probe.v1` schemaへ正規化する。

## 実装範囲

- 解決順序: `--user-data-dir` 明示指定、実行中Discordプロセス、OS標準候補
- macOS: 実行中Electronの `--user-data-dir` を優先
- Windows: 実行中Discordのcommand lineをPowerShell経由で確認し、fallbackは `%APPDATA%\discord`
- Linux: 実行中プロセスを確認し、fallbackは `~/.config/discord`
- Chromium Simple Cache v5のheader / key / stream / EOFを検証
- plain JSON / gzip JSONを解析
- 対象channelと完全一致するobjectから `type` / `name` / `available_tags[].name` だけを抽出
- 既定出力はmetadata-only。`--include-labels` 指定時だけchannel/tag labelをprivate consoleへ返す

## 安全境界

- Discordへのsend / reaction / edit / deleteなし
- cache file、Discordプロセス、Local Storage、Session Storageを変更しない
- raw本文、HTTP header、完全URL、Discord ID、参加者名、token、cookie、local absolute pathを出力しない
- cache 0件を「Discord上に存在しない」と解釈しない
- 実データをfixtureやtracked fileへ保存しない

## 実測結果

Mac実機の現行Discordプロセスから保存場所を自動解決できた。対象URLについてcache参照は検出したが、channel設定objectと掲示板tagはcacheに残っていなかったため、結果は `reference_only` となった。

この結果により、次を分離できる。

- cache保存場所の誤認: 解消
- cache構造の未対応: 解消
- 対象参照の有無: 判定可能
- channel設定・tagの取得: cacheに残っている場合だけ可能

Windowsは人工fixtureで `%APPDATA%` fallbackと同一schemaを検証済み。Windows実機での検証は未実施。

## 検証

- `python3 -m pytest -q`: 339 passed
- `python3 scripts/ops_check.py --profile fast --skip-http`: 全check成功
- `python3 scripts/boundary_logic_check.py --json`: overall ok
- `python3 scripts/lint_ingest_route_policy.py --json`: overall ok
- `python3 scripts/verify_ssot_projection.py --json`: overall ok
- Mac実cache smoke: `reference_only`、raw本文・ID・path出力なし

HTTP MCP smokeはoptional dependencyが未導入の環境では実行していない。cache probe本体のCLI、test、fast smokeとは分離する。

## 使い方

```bash
PYTHONPATH=src python3 -m discord_context_bridge.cli \
  desktop-cache-probe \
  --url 'https://discord.com/channels/<guild>/<channel>' \
  --include-labels \
  --json
```

## 次の判断

現在のcacheに設定objectがない場合、別channelのtagを流用したり推測で補完したりしない。掲示板tagを確定する必要がある案件は、Discord UI上の人間確認または許可済みread-only取得へ戻す。
