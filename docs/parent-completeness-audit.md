# 親チャンネル配下の取得完全性監査

## 目的

単一スレッドの `strict_full_capture_v1` に加えて、フォーラム等の親チャンネル配下を
「全部取得した」と判定するためのローカル監査契約を定めます。Discordへの送信、投稿、
削除、設定変更は行いません。

## URL分類

`classify-discord-url` は、URL文字列から分かる `structural_shape` と、REST API、
可視DOM、保存済みメタデータ等から確認した `resolved_kind` を分離します。

- 2-ID URL: `guild_channel_target`。通常チャンネルかフォーラム親かは未解決。
- 3-ID URL: `nested_target`。親・スレッド関係は未解決。
- 4-ID URL: `nested_message_target`。証拠があれば `message` に解決可能。

URLだけから種別を断定しません。解決する場合は `evidence_source` を必須にします。
診断出力にはURLとsnowflakeを返しません。

```powershell
python -m discord_context_bridge.cli classify-discord-url `
  --url "<Discord URL>" `
  --resolved-kind forum_parent `
  --evidence-source discord_rest_api `
  --evidence-observed-at "2026-07-28T10:00:00+09:00" `
  --json
```

## 正規化SQLite

`init-completeness-db` は次を別テーブルで保持します。

- 親対象
- 親配下の棚卸し走査
- 各走査で観測したスレッド集合
- 各子スレッドの `discord_full_capture_completion_gate.v1` 証明書

Discord IDはprivate local DB内の集合突合にだけ使い、監査出力には返しません。

```powershell
python -m discord_context_bridge.cli init-completeness-db `
  --db ".local/discord-completeness.sqlite3" --json
```

## 正式RESTによる棚卸し

`discord_archived_thread_inventory.py` は既存のbot token providerを再利用し、Discord正式APIを
GETだけで読みます。`--parent-kind` を指定すると、親完全性監査が直接受け取れるscope receiptを
private JSONへ保存します。標準出力は件数と終端状態だけで、token、Discord ID、URL、保存path、
スレッド名を表示しません。

```powershell
python scripts/discord_archived_thread_inventory.py `
  --url "<Discord親チャンネルURL>" `
  --parent-kind forum `
  --output ".local/discord-context-bridge/parent-scope-receipts.json" `
  --json
```

announcement/forum/mediaでは `active_filtered` と `archived_public`、textではさらに
`archived_private` をcursor終端まで取得します。active routeはguild全体の応答から対象の
`parent_id` だけを残します。private routeの成功応答は `manage_threads` 権限の確認証拠です。
実行前に `GET /channels/{channel_id}` のtrustedな `id`、`guild_id`、`type` をURL由来の対象へ
結び付けて親種別を分類し、対象または `--parent-kind` と一致しなければ停止します。
fixtureでも同じchannel metadataを必須にします。
`joined_private` は参加済みthreadの補助調査に限り、全private取得の証拠にはしません。
429は指定回数内だけ再試行し、401/403では別credentialやuser tokenへ迂回せず停止します。

棚卸し証拠JSONの正本は `parent_kind` と scope別 receipt です。`locked` は独立scopeではなく、
各scopeの `locked_count` 属性として数えます。announcement/forum/media は `active_filtered` と
`archived_public`、text はそれらに `archived_private` を加えたものだけを必須scopeとします。

```json
{
  "parent_target_key": "private-parent-key",
  "scan_id": "scan-001",
  "observed_at": "2026-07-28T10:00:00+09:00",
  "parent_kind": "forum",
  "scope_receipts": {
    "active_filtered": {
      "route": "GET /guilds/{guild_id}/threads/active",
      "parent_target_key": "private-parent-key",
      "active_parent_filter_applied": true,
      "page_count": 1,
      "terminal_reached": true,
      "terminal_cursor": null,
      "thread_ids": ["private-active-thread-id"],
      "locked_count": 0
    },
    "archived_public": {
      "route": "GET /channels/{channel_id}/threads/archived/public",
      "parent_target_key": "private-parent-key",
      "active_parent_filter_applied": false,
      "page_count": 2,
      "terminal_reached": true,
      "terminal_cursor": "private-terminal-cursor",
      "thread_ids": ["private-archived-thread-id"],
      "locked_count": 1
    }
  }
}
```

text親の `archived_private` receipt は routeを
`GET /channels/{channel_id}/threads/archived/private` とし、さらに
`"authorization":{"capability":"manage_threads","confirmed":true}` を必須にします。
これは参加済みprivate threadだけを返す別routeを、全private列挙の証拠と誤認しないためです。
保存時にscopeごとの集合digestを計算し、route・親binding・filter・page/cursor終端・件数と
一体のreceiptとしてDBに保存します。ID、cursor、URLは監査出力へ返しません。

同じ対象を時間を分けて2回以上走査し、少なくとも最新2回を保存します。

```powershell
python -m discord_context_bridge.cli record-parent-inventory `
  --db ".local/discord-completeness.sqlite3" `
  --evidence ".local/inventory-scan.json" --json
```

各子スレッドは `full-capture-gate` のmetadata-only出力を保存します。

```powershell
python -m discord_context_bridge.cli record-child-certificate `
  --db ".local/discord-completeness.sqlite3" `
  --parent-target-key "private-parent-key" `
  --thread-id "private-thread-id" `
  --certificate ".local/child-full-capture.json" --json
```

## 完全性アルゴリズム

`audit-parent-completeness` は次のアルゴリズムを順に適用します。

1. `pagination_exhaustion`: 親種別ごとの必須scopeを、それぞれ固有routeで終端まで進める。
2. `stable_rescan`: 最新2走査のスレッド集合と件数が一致する。
3. `set_reconciliation`: 最新棚卸し集合と子証明書集合が一致する。
4. `strict_child_full_capture`: 全子が `strict_full_capture_v1` を通過する。
5. `attachment_manifest_reconciliation`: 各子の発見・保存・manifest添付件数が一致する。
6. `pending_work_zero`: gap、retry、未証明子が0件である。

```powershell
python -m discord_context_bridge.cli audit-parent-completeness `
  --db ".local/discord-completeness.sqlite3" `
  --parent-target-key "private-parent-key" --json
```

`parent_full_capture_confirmed=true` は全条件が同時に成立した時だけ返します。一つでも
欠ける場合は `partial` または `blocked` とし、推測で補完しません。Discord上で走査を
実行する取得アダプター自体は別責務であり、この監査は保存済み証拠だけを判定します。

旧schemaの `scopes` と単一 `pagination_exhausted` は読み取り互換のため残しますが、
scope receiptが存在しない旧scanは `inventory_scope_receipts_missing` となり、再走査なしで
`full` へ昇格しません。migrationは既存行を書き換えない加算型です。
