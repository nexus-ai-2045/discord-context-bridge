---
title: PR Scope Guardrails
type: operations
status: active
---

# PRスコープ混入防止

回収PRや小さな安全修正では、別プロジェクト、別bot構成、ローカル保持物を同じPRに混ぜない。

## PR前ゲート

PR作成前に次を実行する。

```bash
python3 scripts/pr_scope_guard.py --base origin/main --head HEAD --json
python3 scripts/pr_readiness_preflight.py --fetch --json
python3 scripts/check_pr_language.py --title "<PR title>" --body-file <body.md>
```

`pr_scope_guard.py` は次を止める。

- スコープ外キーワードの混入。
- `extracts/` や `.local/` など、repo外へ退避すべきローカル保持物。
- local absolute path のPR差分混入。
- 回収PRで説明されていない新規 `scripts/`、`tests/`、`docs/` の追加。
- 差分ファイル数が大きすぎるPR。

必要な新規ファイルがある場合は、PR本文で理由を説明したうえで `--allow-new-path` を明示する。
テストfixtureとして禁止語そのものを含める場合は、PR本文で理由を説明したうえで `--allow-scope-path` を明示する。

## PR本文

PR本文には必ず次を入れる。

```markdown
## 日本語レビュー

- PRタイトルと本文の日本語を確認しました。
- 公開境界、Discord送信なし、repository visibility変更なしを確認しました。

japanese_pr_ok: yes
```

Dependabot が生成した dependency update PR は例外レーンとして扱う。`author=dependabot[bot]`
または `headRefName=dependabot/...` で、かつタイトルまたは本文に dependency update の信号がある場合だけ、
`check_pr_language.py` は `dependabot_update_allowed=true` として通す。これは人間レビュー済みPRとは別扱いで、
通常PRの日本語タイトル、本文見出し、`japanese_pr_ok: yes` 要件は維持する。

## post-merge closeout

マージ後は次を確認する。

```bash
python3 scripts/post_merge_closeout_report.py --pr <number> --head-ref <branch> --fetch --json
```

closeout は、PRがmerge済みであること、`main` が同期していること、merge済みhead branchがremoteに残っていないこと、
worktreeがcleanであることを確認する。`extracts/` や `.local/` のような保持物は削除せず、repo外の
`*-local-retained-*` へ退避してから完了扱いにする。
