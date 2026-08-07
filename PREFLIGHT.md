<!-- repo-preflight:review-record -->

# 公開準備状況

- HEAD: `c1d6a83709dad17a8b488142617639cf567d8822`（PREFLIGHT 追加前の基準。更新時は差し替え）
- 確認日時: 2026-08-07
- 判定: `blocked`

## 確認済み

- [x] README / LICENSE / SECURITY.md
- [x] CONTRIBUTING.md / PREFLIGHT.md（本 PR で追加）
- [x] test / ops_check / SSOT projection（main 運用保証済み）
- [x] secret 現行 + 到達可能 history（local junk ref 除去後 finding=0。実 secret なし）
- [x] personal path 現行 tree redaction（本 PR）
- [ ] personal path **history** rewrite（別判断・force-push 要）
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
- 未 review: personal path **history**、identity policy、dependency vuln audit
- 残余リスク:
  - personal path 候補が **git history** の旧 detector/test/docs に残る（現行 tree は redaction 済み）
  - dependency vulnerability / remote CI の毎回機械確定は unknown
  - history rewrite / force-push は未実施
- 次に承認する正確な操作: 本 PR の merge のみ（publish / visibility 変更は別承認）

## 次の PR 候補（適切なサイズ）

1. personal path の **history rewrite**（force-push + 人間明示承認）
2. expected identity policy の設定
3. dependency vulnerability の現行監査証跡
