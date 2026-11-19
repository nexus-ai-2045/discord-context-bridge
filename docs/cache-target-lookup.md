# 対象別キャッシュ照会と全体棚卸し

通常のURL照会では `cache_inventory.build_target_cache_lookup` を使う。
このAPIは、指定した正規観測台帳だけを読み、対象の件数、鮮度、更新経路、任意の非公開タイトルを返す。
`lookup_scope` で正規台帳のみの照会であることを明示する。
外部キャッシュのNDJSON発見、Markdownの探索、保存先全体の容量集計は行わない。
したがって、未取り込みの外部キャッシュだけに本文がある場合は `snapshot_missing` を返す。
これは全保存先に本文が無いという判定ではない。

```python
from pathlib import Path
from discord_context_bridge.cache_inventory import build_target_cache_lookup

report = build_target_cache_lookup(
    url=target_url,
    snapshot_store=Path(".local/discord-context-bridge/text-snapshots.ndjson"),
)
```

全体の発見・容量棚卸しには引き続き `build_cache_inventory` を使う。
同APIの `inventory` は全体のfile数・容量と対象一致件数を含み、対象別APIへ暗黙に置き換えない。
Markdownのfrontmatter読取りは従来と同じ先頭8192 **文字** に制限する。
全fileを読んでsliceせず、text streamの部分読取りでその文字数だけ読む。UTF-8の日本語をbytes制限で切り、従来読めたメタデータを落とさない。その文字数内でfrontmatterが閉じない場合はメタデータを採用しない。

対象別APIも毎回 `matching_snapshot_records(..., canonical=True)` により台帳全体のhash chain、alias continuation、失効証拠を検証する。
壊れた台帳では検証エラーを呼出元へ返し、古い成功結果へ退避しない。
正規台帳内の対象照合規則は既存関数を再利用する。
正規台帳は全行を検証するので、全対象の本文I/Oを省く索引ではない。
既存target registryはURL・label・aliasの補完台帳であり、本文の位置や検証証拠を保持する索引ではないため、このAPIでは読まない。
永続projection・初回build・別の信頼境界は追加しない。
台帳の追記・置換・切詰めは次の呼出しで再読され、mtime/sizeだけを安全性の根拠にしない。
有効な過去prefixへの切詰め自体の検出は別のcheckpoint契約が必要であり、このAPIは切詰め後の実在行を返す。

通常出力には本文・生URL・実パス・参加者名を含めない。
`include_private_title=True` の時だけタイトルを含める。
`ready` は照会可能な観測の存在を示し、返信文脈gate、全文capture、実機表示、配布の成功を証明しない。

回帰確認は `tests/test_cache_inventory.py`。
合成の対象外Markdown・NDJSONを置き、全体棚卸しと対象別照会の件数を照合しながら読取り回数・bytesを比較する。
同サイズ・同mtimeの改変、追記、置換、切詰め、対象切替、無効URLも確認する。
この読取り削減は外部キャッシュ探索を明示的に除いた効果であり、ブラウザ・LLM・保存処理の待ち時間を測ったものではない。
