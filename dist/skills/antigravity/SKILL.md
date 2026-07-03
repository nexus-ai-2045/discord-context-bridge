---
name: discord-context-bridge
description: Runtime adapter for the Discord Context Bridge SSOT. Generated for antigravity; do not edit by hand.
ssot_repo: nexus-ai-2045/discord-context-bridge
ssot_commit: 02d407cda1c2abff4ec3e0e8a80cb889ba10dd6b
manifest_version: discord_context_bridge_capability_manifest.v1
manifest_checksum: e45d44c2e0849518ab35f002c2677491a03c429aefe78b07ddb4160e416c0639
contract_checksum: 640e8397df3eccfbd53071c63ad39d0393c932a4b7f693ae42d04727866f204b
generated_at: 2026-07-03T18:56:13+00:00
runtime_target: antigravity
---

# Discord Context Bridge (antigravity)

This skill is generated from `nexus-ai-2045/discord-context-bridge`. Do not edit this generated file by hand; update `capability/manifest.yaml` or `docs/operating-contract.md`, then run `python3 scripts/export_runtime_skills.py`.

## Contract

# Discord Context Bridge operating contract

この文書は `nexus-ai-2045/discord-context-bridge` の runtime 横断 SSOT です。Codex / Claude Code / Grok / AntiGravity などの skill は、この contract と `capability/manifest.yaml` から生成される派生物として扱います。

## 不変条件

- Discord への send / 自動返信 / reaction / delete / edit はしない。
- 外部共有、公開投稿、GitHub への raw Discord text の追加はしない。
- raw Discord 本文、実 guild/channel/message ID、handle、token、cookie、local absolute path を visible output に出さない。
- 取得経路は local-first / read-only とし、可視テキスト・clipboard・private adapter のいずれも outbound action を持たない。
- Discord 文脈取得では Playwright / headless browser / 新規 browser profile を既定経路にしない。既定は cic（claude-in-chrome）可視DOM、貼り付け/ファイル、Discord Desktop cache、macOS Accessibility とする。Playwright はユーザー明示、または Discord 本文取得ではない周辺UIの限定調査だけに使う。
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
python3 scripts/lint_ingest_route_policy.py --json
python3 scripts/ops_check.py --gh
```

`verify_ssot_projection.py` は runtime skill の欠落、stale、provenance 不一致、private / raw data 混入を検出する。

`lint_ingest_route_policy.py` は contract / runtime skill / local Claude skill に Playwright fallback 禁止の運用文が残っているかを検出する。

各 runtime の local skill directory は、勝手に書き換えず read-only lint で同期状態を確認する。

```bash
python3 scripts/lint_runtime_skill_sync.py \
  --target codex=/path/to/discord-context-bridge/SKILL.md \
  --json
```

`lint_runtime_skill_sync.py` は target file を読み、`dist/skills/<runtime>/SKILL.md` と完全一致するか、`ssot_commit` / checksum / privacy pattern を確認する。更新が必要な場合でも、この script は書き込みを行わない。

## Stoplines

- `no_discord_send`
- `no_external_share`
- `no_raw_discord_text_in_visible_output`
- `no_tokens_or_cookies`
- `no_playwright_default_for_discord_context`

## Commands

- `import-visible-text`: 可視テキストをローカル event store に取り込む
- `context-passport`: 可視テキストから文脈カードを作る
- `guide-reply`: 可視テキストと下書きから返信前ガイドを作る
- `review-draft`: 下書きの文脈適合・トーン・不足前提を確認する

## Verification

- `python3 scripts/verify_ssot_projection.py --json`: SSOT から生成された runtime skill が最新か確認する
- `python3 scripts/lint_runtime_skill_sync.py --target codex=/path/to/SKILL.md --json`: runtime skill directory の実体が SSOT 生成物と一致するか read-only で確認する
- `python3 scripts/lint_ingest_route_policy.py --json`: Discord 文脈取得で Playwright を既定経路にしない運用を確認する
- `python3 scripts/ops_check.py --gh`: test / smoke / secret scan / GitHub account をまとめて確認する
