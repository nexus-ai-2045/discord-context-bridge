# Contributing

Discord Context Bridge への貢献ガイドです。

## まず読むもの

- `README.md` — 目的と使い方
- `SECURITY.md` — 安全境界と secret の扱い
- `AGENTS.md` — エージェント向け停止線
- `PREFLIGHT.md` — 公開・PR 前の機械/人間チェック状態

## 安全境界（必須）

- public core は Discord への send / reaction / edit / delete を行わない
- token / cookie / webhook / 実 ID / 参加者名 / raw 本文を commit や PR 本文に載せない
- 可視テキスト・metadata-only 出力を既定にする
- Playwright を Discord 本文取得の既定経路にしない

## 開発の進め方

1. 小さく変更する（1 PR = 1 意図）
2. 既存 script / test を使う（車輪の再発明を避ける）
3. 変更後は最低限次を通す

```bash
python -m pytest -q
python scripts/ops_check.py
python scripts/verify_ssot_projection.py --json
```

4. PR 本文は日本語見出しを含める（`## 概要` / `## 検証` / `## 境界` / `## 日本語レビュー`）
5. `japanese_pr_ok: yes` を付け、人間レビュー材料を残す

## 変更の粒度

- 文書だけ / 実装だけ / 危険な履歴操作 を混ぜない
- secret 候補や個人 path の除去は別 PR にする
- runtime skill は手編集せず、contract / manifest 更新後に export する

## やらないこと

- 公開 repo での raw Discord 本文追加
- Chrome profile / user token の抽出
- ignore して secret 検出を握りつぶすこと
