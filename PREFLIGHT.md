<!-- repo-preflight:review-record -->

# 公開準備状況

- base main: 424a7da47446dc7a1e660346483c3444c7fbfe65 (PR #58 merge tip)
- reviewed candidate: 4112ef0 (residual closeout、PREFLIGHT更新前のcode/docs tip)
- 確認日時: 2026-08-14
- 判定: attention（local release gate / reviewはpass。public branch push、PR CI、exact diffの人間確認は未実施）

## 確認済み

- [x] README / LICENSE / SECURITY.md
- [x] CONTRIBUTING.md / PREFLIGHT.md
- [x] test / ops_check / SSOT projection
- [x] secret 現行 + 到達可能 history（finding_count=0）
- [x] personal path 現行 tree redaction
- [x] personal path history rewrite（2026-08-07 filter-repo + force-with-lease）
- [x] dependency 設定 / CI workflow 構造
- [x] dependency vulnerability の**現行監査証跡**（下記）
- [x] remote CI の**最新証跡**（下記）
- [x] project 登録 / GitHub owner / author identity policy 固定（下記）
- [x] operations gate (`ops_check.py --profile release --skip-http`) success（2026-08-14）
- [x] open PR / CodeQL / Dependabot / secret-scanning alert 0（2026-08-14）
- [ ] current candidate のpublic branch push / PR CI / merge review（別承認）
- [ ] production monitoring / rollback live environment review (live Discord / on-call; separate decision)

## Identity policy（SSOT）

公開 GitHub owner / repository:

- nexus-ai-2045/discord-context-bridge

許可する git author 名義は **scripts/gh_guard.py の EXPECTED_GIT_AUTHORS を SSOT** とする（複数 allowlist）。

- 検証: release gateのGitHub account確認で `nexus-ai-2045` とremote ownerの一致を確認（2026-08-14: success）
- 実測: history に nexus_ai / nexus-ai-2045 / Dependabot / GitHub merge committer が混在
- **採用しない**: repo-preflight の単一 --expected-identity で全 history を強制（identity rewrite が必要になるため別承認）
- ローカル作業名義の推奨: nexus_ai <273569186+nexus-ai-2045@users.noreply.github.com>

## Dependency vulnerability 現行監査

- 実行日: 2026-08-07
- 方法: 一時 venv に pip install -e ".[mcp]" 後 python -m pip_audit
- 範囲: プロジェクト依存 + その transitive（global site-packages は対象外）
- 結果: **No known vulnerabilities found**（exit 0）
- 生 JSON: private local archive のみ（repo に commit しない）
- Dependabot: pip / github-actions 週次は継続（ongoing）。今回の現行監査の代替ではない

## remote CI 証跡

| tip / event | workflow | conclusion | URL |
|---|---|---|---|
| PR #58 merge tip 424a7da | CI | success | https://github.com/nexus-ai-2045/discord-context-bridge/actions/runs/31775287301 |
| PR #58 merge tip 424a7da | CodeQL | success | https://github.com/nexus-ai-2045/discord-context-bridge/actions/runs/31775287267 |
| PR #58 merge tip 424a7da | Dependency Graph | success | https://github.com/nexus-ai-2045/discord-context-bridge/actions/runs/31775289829 |
| PR #55 merge tip 4b65029 | CI | success | https://github.com/nexus-ai-2045/discord-context-bridge/actions/runs/31184266910 |
| PR #55 merge tip 4b65029 | CodeQL | success | https://github.com/nexus-ai-2045/discord-context-bridge/actions/runs/31184266926 |

current candidateは未pushのためremote CI未実施。PR作成後とmerge後のmain tipで再確認する。

## 人間目視

- reviewer: CEO（やす） / session operator
- reviewed_at: 2026-08-14（残務ゼロ方針、version/tagは変更しない判断）
- exact HEAD / PR diff: 未確認（branch未push・PR未作成）
- reviewed content: 残務分類、local test / release gate結果、version/tagの現状
- decision: public branch push / PR / mergeはpending
- 外から見える files と commit history: repositoryはPUBLIC。branchをpushすると今回の10 filesとPREFLIGHT更新がweb上で可視になる
- review 済み: secret/path自動、GitHub account、main CI、local release gate、Python / code review
- 未 review: current final diffのpublic push、PR CI、merge、production live monitoring / rollback、GitHub Release / tag
- 残余リスク:
  - repo-preflight CLI の dep/ci/human は設計上 unknown（証跡は本ファイル）
  - publication_decision は常に human review required
  - package versionは0.11.0、最新tagはv0.4.0。v0.11.0 tag / GitHub Releaseは未作成
  - auto-push opt-inはdisabledで、canonical push wrapperはdeny済み
- 次に承認する正確な操作: auto-push opt-in有効化後、`codex/dcb-residual-zero-20260814`をPUBLIC repositoryへpushしてPRを作成する。merge / release / Discord liveは別承認

## 次の PR 候補（適切なサイズ）

1. release readiness: version/tag/GitHub Release（PUBLIC_READY.md / 別承認）
2. production live monitoring / rollback 実環境目視（別判断）
3. ISSUE_LISTのparked製品候補（parser quality / cache / refactor）は次回注文ごとに独立PR

## Ops gate 証跡（2026-08-14）

| 項目 | 結果 |
|---|---|
| base main | 424a7da47446dc7a1e660346483c3444c7fbfe65 |
| reviewed candidate before PREFLIGHT update | 4112ef0 |
| full pytest | 866 passed、slowest 0.93秒（P1-2 closeout run） |
| ops_check.py --profile release --skip-http | success、15.69秒、GitHub account確認を含む |
| Python / code review | approve、P0-P3 findingなし（指摘修正後） |
| main CI / CodeQL / Dependency Graph | 424a7daでsuccess |
| GitHub open PR / security alerts | 0 / 0 |
| ISSUE_LIST active | 0。parked / later / blockedは未実装として分離 |
| PUBLIC_READY.md | 2026-08-07の記録。release/tag承認には再確認が必要 |

Discord送信、Release作成、tag作成、repository visibility変更は未実行。
