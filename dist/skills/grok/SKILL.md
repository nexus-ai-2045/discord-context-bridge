---
name: discord-context-bridge
description: Runtime adapter for the Discord Context Bridge SSOT. Generated for grok; do not edit by hand.
ssot_repo: nexus-ai-2045/discord-context-bridge
ssot_commit: 13d16ac90474283e09d9c87f4396b855c2e9ed51
manifest_version: discord_context_bridge_capability_manifest.v1
manifest_checksum: 5b70a012ef07d581c4b74822fe79e24c1204876096b4eaf7dacc055fa3d5d9c8
contract_checksum: a722316a74476be56039da6c8efb6ecb1a71b79f180d61bfb6b6195b7d7f6da1
generated_at: 2026-07-03T11:47:03+00:00
runtime_target: grok
---

# Discord Context Bridge (grok)

This skill is generated from `nexus-ai-2045/discord-context-bridge`. Do not edit this generated file by hand; update `capability/manifest.yaml` or `docs/operating-contract.md`, then run `python3 scripts/export_runtime_skills.py`.

## Contract

# Discord Context Bridge operating contract

この文書は `nexus-ai-2045/discord-context-bridge` の runtime 横断 SSOT です。Codex / Claude Code / Grok / AntiGravity などの skill は、この contract と `capability/manifest.yaml` から生成される派生物として扱います。

## 不変条件

- Discord への send / 自動返信 / reaction / delete / edit はしない。
- 外部共有、公開投稿、GitHub への raw Discord text の追加はしない。
- raw Discord 本文、実 guild/channel/message ID、handle、token、cookie、local absolute path を visible output に出さない。
- 取得経路は local-first / read-only とし、可視テキスト・clipboard・private adapter のいずれも outbound action を持たない。
- 判断は `[事実: source]` / `[推測]` / `[不明]` に分け、未確認の文脈を断定しない。

## 標準フロー

1. 可視テキストを local file または stdin から `import-visible-text` に渡す。
2. `context-passport` で文脈カードを作る。
3. 返信案がある時だけ `guide-reply` または `review-draft` で確認する。
4. 出力は JSON をそのまま貼らず、安全ラベル、件数、reason code、短い日本語要約にする。

## Runtime skill projection

各 runtime 用 skill は `scripts/export_runtime_skills.py` で生成する。生成物は `dist/skills/<runtime>/SKILL.md` に置き、先頭に次の provenance を含める。

- `ssot_repo`
- `ssot_commit`
- `manifest_version`
- `manifest_checksum`
- `contract_checksum`
- `generated_at`

生成物は手編集しない。変更は `capability/manifest.yaml` またはこの contract に入れ、再生成する。

## Verification

PR 前と運用 closeout では次を確認する。

```bash
python3 scripts/verify_ssot_projection.py --json
python3 scripts/ops_check.py --gh
```

`verify_ssot_projection.py` は runtime skill の欠落、stale、provenance 不一致、private / raw data 混入を検出する。

## Stoplines

- `no_discord_send`
- `no_external_share`
- `no_raw_discord_text_in_visible_output`
- `no_tokens_or_cookies`

## Commands

- `import-visible-text`: 可視テキストをローカル event store に取り込む
- `context-passport`: 可視テキストから文脈カードを作る
- `guide-reply`: 可視テキストと下書きから返信前ガイドを作る
- `review-draft`: 下書きの文脈適合・トーン・不足前提を確認する

## Verification

- `python3 scripts/verify_ssot_projection.py --json`: SSOT から生成された runtime skill が最新か確認する
- `python3 scripts/ops_check.py --gh`: test / smoke / secret scan / GitHub account をまとめて確認する
