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

棚卸し証拠JSONは次の形です。

```json
{
  "parent_target_key": "private-parent-key",
  "scan_id": "scan-001",
  "observed_at": "2026-07-28T10:00:00+09:00",
  "thread_ids": ["private-thread-id"],
  "scopes": {
    "active": true,
    "archived_public": true,
    "archived_private": true
  },
  "pagination_exhausted": true
}
```

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

1. `pagination_exhaustion`: active、archived public、archived private の列挙を終端まで進める。
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
