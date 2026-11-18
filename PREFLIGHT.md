<!-- repo-preflight:review-record -->

# 公開準備状況

## v0.12.0候補（2026-10-15）

- base main: `8b4ba158ae081f0d80c9686ea9264379743325d2`（PR #92、#93統合済み）
- 内容: v0.11.1以降に統合した #76、#89〜#93 をCHANGELOGの0.12.0へ記録し、`pyproject.toml` を0.12.0へ上げた。LICENSEの著作権者を公開名義 `nexus-ai-2045` に揃えた。
- README: 0.12.0の変更、取得経路の順序、表明ゲートとボットのアクセス確認の入口、検証コマンドを現在の実装に合わせて更新した。案内するサブコマンドと相対リンクの実在を確認済み。
- 統合: 2026-10-14に手元だけで作られていた `codex/dcb-v0.12.0-release`（未push）をこのbranchへmergeし、README・CHANGELOGの固有内容（MCP追加依存、保存先の選び方、単発保存と全文取得の区別、保存先破損時の停止、非保証範囲）と公開チェックリストのrelease用運用検査を取り込んだ。0.12.0の候補はこのbranch 1本に統一する。
- local verification: 全pytestは1509件成功・9件スキップ。`ops_check.py --profile release --skip-http`、SSOT projection、取得経路方針、表明ゲートの配線、version整合の各検査が成功。
- 依存監査と配布物（2026-10-14、Codex実施）: coreとoptional MCPを新規解決した28依存の `pip-audit` は既知脆弱性0件。`uv build` でwheelとsource distributionを生成し、private取得データ・`.local` を含まないことを確認。共有Python環境全体や利用者環境の安全保証ではない。
- 個人パス検査: 手元の全参照を対象にした `run_preflight.py --intent release` は101件で `blocked`。内訳は、手元だけの退避参照（`refs/backup/...`、remoteに無い）由来が99件、公開tag `v0.4.0` 由来が2件。後者は伏字化用の正規表現とテスト用の架空のユーザー名を使った例示パスで、実パスではない。remoteを新しくcloneした状態の同検査では個人パス検査は成功（`needs_human_input` は依存監査・CI・人間レビューの確認項目のみ）。手元の退避参照は消していない。
- remote CI: PR #94 の同一HEADで10件すべて成功（マージ前に再確認する）。
- public release: `v0.12.0` tag／GitHub Releaseは未作成。PR #94のマージ後、承認されたmain SHAで作成する。

## v0.11.1候補（2026-09-29）

- base main: `ff3df4ee0430cc3e2bb86750d71ec0e7d0486397`（PR #73統合済み）
- reviewed code candidate: `5c51756e26c2d30cdcd627a9e788c1be9d1880f7`。この記録を追加する文書commitは検査対象code commitと区別する。
- README: 現在の機能・制約を更新済み。読みやすさ検査と前回tag `v0.11.0`からの内容更新検査が成功。
- code review: 内部ブラウザ既定・Windows検証・SQLite接続管理を独立レビューし、未解消指摘なし。
- local verification: release profileの全pytest成功。Windowsの対象43件、process runner20件、SQLite53件成功。後続のlocal runtime driftとSQLite smoke失敗は、それぞれ公式skill同期と接続close修正後に再確認して成功。
- dependency audit: 新規解決したcore＋optional MCPの30依存は既知脆弱性0件。現在の共有Python環境には古い依存が残るため、新規解決の監査結果を既存環境の安全保証に流用しない。
- Windows private保存: fixtureの保存往復は確認済み、ACLは別確認。`permission_verification=not_verified`を成功へ丸めない。
- remote CI: このrelease準備branchの同一HEAD CIはPR作成後に確認する。
- public release: `v0.11.1` tag／GitHub Releaseは未作成。公開内容とREADMEの人間レビュー後、承認されたmain SHAで作成する。

## 過去の検証記録（2026-08-14）

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
