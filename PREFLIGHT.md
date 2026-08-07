<!-- repo-preflight:review-record -->

# 公開準備状況

- HEAD: `c1d6a83709dad17a8b488142617639cf567d8822`（PREFLIGHT 追加前の基準。更新時は差し替え）
- 確認日時: 2026-08-07
- 判定: `blocked`

## 確認済み

- [x] README / LICENSE / SECURITY.md
- [x] CONTRIBUTING.md / PREFLIGHT.md（本 PR で追加）
- [x] test / ops_check / SSOT projection（main 運用保証済み）
- [ ] secret / PII / personal path / history（repo-preflight: secret 候補あり・別 PR）
- [x] dependency 設定 / CI workflow 構造
- [ ] dependency vulnerability の現行監査
- [ ] remote CI の毎回の機械確定（直近 PR #48/#49 は green で merge 済み）
- [ ] operations / monitoring / rollback の実運用目視
- [ ] project 登録 / GitHub owner / author identity policy 固定

## 人間目視

- reviewer:
- reviewed_at:
- exact HEAD / PR diff:
- reviewed content: preflight 文書 baseline
- decision: `approve / changes_requested`
- 外から見える files と commit history:
- review 済み:
- 未 review: secret 候補、personal path 履歴、untracked local notes
- 残余リスク:
  - secret_scan finding_count > 0（値は非表示）
  - personal path 候補が現行 tree / history に残る
  - ローカル untracked の consolidation notes は public に載せない
- 次に承認する正確な操作: 本 PR の merge のみ（publish / visibility 変更は別承認）

## 次の PR 候補（適切なサイズ）

1. secret 候補の棚卸しと除去（ignore なし）
2. personal path の現行 tree redaction（history rewrite は別判断）
3. expected identity policy の設定
