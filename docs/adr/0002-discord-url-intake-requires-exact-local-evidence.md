# ADR 0002: Discord URL intake は exact local evidence がある時だけ成立する

## Status

Accepted

## Context

Discord Context Bridge は、Discord URL を受け取った時に、AI がその会話を読めたかどうかを判定する必要がある。
しかし URL だけでは、対象スレッドの本文を読めたことにはならない。

特に forum channel では、URL の形、親チャンネル、スレッド、保存済み snapshot の鮮度が混ざると、
単なる `snapshot_missing` だけでは原因を診断できない。

この ADR は、Discord URL intake が成立する条件と、成立しない時の理由分類を決める。

## Decision

Discord URL intake は、exact URL または exact target key に一致する local evidence がある時だけ成立する。

AI が読めた扱いにしてよい local evidence は、次の順で確認する。

1. raw cache / local snapshot に exact match がある
2. AI-facing log に exact match がある
3. 必要な場合だけ raw cache exact match を AI-facing log へ同期する

same-guild fuzzy match は許可しない。
同じ guild 内の似た channel / thread / message を代用してはいけない。

Discord forum URL の shape は intake gate で判定する。
forum parent channel が欠けている場合は、単なる snapshot 不足ではなく、
`forum_parent_channel_missing` として分離する。

snapshot の鮮度は用途別に扱う。

- recent: 返信支援と routing に使える
- stale: 返信生成には使わず、routing / 診断にだけ stale 明示つきで使える
- missing: 読めた扱いにしない

diagnostic JSON は、機械が判断できる reason / freshness / recency / stale policy / next action を返す。
ただし、Discord URL、raw text、local path は出力しない。

## Consequences

良い影響:

- URL を貼っただけで AI が読めたことにする誤判定を避けられる。
- `snapshot_missing` を、raw cache 不足、AI log stale、forum parent channel 欠落に分解できる。
- 古い snapshot を返信本文に使う事故を避けつつ、診断や routing には限定利用できる。
- same-guild fuzzy match による誤読と private context 混線を防げる。
- public-safe な JSON で原因だけを共有できる。

トレードオフ:

- exact evidence が無い時は、同じ guild 内に似た文脈があっても blocked になる。
- stale snapshot を返信に使うには、再取得または同期の一手が必要になる。
- 診断 JSON の schema は少し増える。

## Implementation Notes

現在の実装では、次の CLI / helper がこの ADR を担う。

- `url-intake-fast-path`
- `verify-url-intake`
- `coverage-report`
- `build_url_intake_fast_path`
- `analyze_discord_forum_url_shape`
- `build_url_intake_gate`
- `build_coverage_report`

期待する blocked reason は次を含む。

- `raw_cache_missing`
- `ai_log_stale`
- `forum_parent_channel_missing`
- `raw_cache_gate_skipped`

`stale_policy` は次の判断を返す。

- `usable_for_reply`
- `usable_for_routing`
- `required_action`
- `fallback_allowed`

## Test Evidence

この ADR の実装は、fixture test で次を確認する。

- forum parent channel の有無を URL shape として判定する
- raw cache exact match があり AI-facing log が無い時は `ai_log_stale` になる
- `--sync` 相当の処理で AI-facing log に exact match を同期できる
- forum parent channel 欠落は `forum_parent_channel_missing` として分離される
- stale snapshot は `usable_for_reply: false` / `usable_for_routing: true` になる
- diagnostic JSON に raw text / Discord URL / local path を出さない

## Operational Note

hook が既に `snapshot_missing` / exact `target_key` / `snapshot_count=0` を出している時は、
ユーザー応答では追加 CLI を走らせずに「本文未取得、可視テキストが必要」で停止するのが最短経路。
実測が必要な時だけ `url-intake-fast-path` を 1 回使う。
`context-passport` / `guide-reply` / `snapshot-discord-url-text` は、可視テキストまたは messages がある時だけ呼ぶ。

ローカル環境では、互換用の README pointer directory と実装用の git worktree / clone が分かれる場合がある。
実装と検証は、実際の git worktree / clone で行う。
作業前には `git rev-parse --show-toplevel` と CLI help / targeted test で、触っている repository を確認する。
