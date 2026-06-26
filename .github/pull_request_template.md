## 概要
- 

## 検証
- 

## 境界
- Discord 送信、reaction、delete、自動返信は対象外です。
- raw Discord 本文、実参加者名、secret、local path は PR / log / JSON に出していません。

## 日本語レビュー
- [ ] やすさんがPRタイトルと本文の日本語を確認した。
- [ ] やすさんの「日本語OK」を受けた。

japanese_pr_ok: no

## 公開前ガード
- [ ] `python3 scripts/gh_guard.py --json`
- [ ] `python3 scripts/check_pr_language.py --title "<PR title>" --body-file .github/pull_request_template.md`
- [ ] `python3 scripts/ops_check.py --gh`
