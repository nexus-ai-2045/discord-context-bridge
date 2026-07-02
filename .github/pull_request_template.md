## 概要
- 

## 検証
- 

## 境界
- Discord 送信、reaction、delete、自動返信は対象外です。
- raw Discord 本文、実参加者名、secret、local path は PR / log / JSON に出していません。

## 日本語レビュー
- [ ] PRタイトルと本文の日本語を確認しました。
- [ ] 公開境界、Discord送信なし、repository visibility変更なしを確認しました。

japanese_pr_ok: yes

## 公開前ガード
- [ ] `python3 scripts/gh_guard.py --json`
- [ ] `python3 scripts/pr_scope_guard.py --base origin/main --head HEAD --json`
- [ ] `python3 scripts/pr_readiness_preflight.py --fetch --json`
- [ ] `python3 scripts/check_pr_language.py --title "<PR title>" --body-file .github/pull_request_template.md`
- [ ] `python3 scripts/ops_check.py --gh`

## マージ後closeout
- [ ] `python3 scripts/post_merge_closeout_report.py --pr <number> --head-ref <branch> --fetch --json`
- [ ] `.local/`、`extracts/` などのローカル保持物はrepo外へ退避した。
