<!-- repo-preflight:review-record -->

# 公開範囲とレビュー条件

状態: 非公開（公開済みではない）

このリポジトリは 会話の文脈を手元で取り込み、作業に渡すための橋渡しの道具 を対象とします。

## 公開対象

- (実装後に記入)

## 公開対象外

- secret / token / 個人の絶対 path / アカウント情報
- 公開・push・merge・visibility 変更を自動実行する機能

## 判定上の停止線

`readiness_scan.py` の `status: pass` はローカルで機械検査できた範囲だけを示す。公開・push・merge・visibility 変更は人が別に判断する。

## レビュー記録 (2026-11-19)

- bootstrap_repo.py で作成。repo-preflight の検査結果は作成時の report を参照。
