# ローカルcache inventory / Chrome回帰レビュー

## 結論

この変更は、Discord URLを受け取った後に「保存済みを使うか、最新取得へ進むか」をmetadata-onlyで決める入口を追加する。ローカル設定、正規化済みsnapshot、Discord Desktop cache、Chrome browser bundleを別レイヤーとして扱う。

PR #24は2026-07-15に人間レビュー後マージ済みである。生成runtime skillは後続PR #25のprovenance更新を含め、Codex / Claude Codeのinstalled copyと一致している。

## アーキテクチャ

| レイヤー | 正本性 | 役割 | 完了主張の上限 |
|---|---|---|---|
| user config | 設定の正本 | cache場所を継続利用する | 場所を解決できる |
| append-only ledger / normalized snapshot | 本文観測の正本 | 完全一致、鮮度、coverageを判断する | 保存済み観測の範囲 |
| Markdown | projection | 人間が読むtitleやdigestを提供する | 派生viewの存在 |
| Discord Desktop cache | 補助証拠 | 対象URL参照候補をread-only探索する | reference hitの存在 |
| Chrome browser bundle smoke | bootstrap検査 | host `process` 衝突を事前検出する | bundle import可否 |
| Chrome実機E2E | 外部運用 | タブ接続、DOM読取、入力を確認する | 実測した工程だけ |

## 実装内容

- `configure-local-cache`: dry-runを既定にし、`--apply`時だけconfigをatomic writeする。
- `cache-inventory`: URL完全一致snapshot、Markdown/NDJSON件数、title evidence、freshness、次の判断を返す。
- `desktop-cache-probe`: Discord DesktopのChromium Simple Cacheをread-onlyで調べ、本文なしの参照状態だけ返す。
- `codex_chrome_bundle_smoke.py`: 通常importと保護済み`process`条件を別々に検査する。
- `pdca_e2e_inventory.py`: 全E2E caseを棚卸しし、同じfailureを繰り返さずActへ変換する。
- synthetic fixtureによる単体・CLI E2E回帰を追加する。実plugin bundleを標準CI依存にはしない。

## 現在判明している外部停止

旧ChromeCodex bundle `26.707.71524` は、hostが保護している`globalThis.process`を再定義しようとして `Cannot redefine property: process` で停止していた。現在のinstalled bundle `26.707.72221` は同じsmokeを通過し、Chrome extension接続、指定Discord URL到達、ログイン済みUI、可視DOM取得まで実測済みである。入力・投稿は実行していない。

`codex_chrome_bundle_smoke.py` はinstalled bundleの最新版を自動検出する。本変更が恒常的に保証するのは、同じbootstrap不具合を投稿や読取の直前ではなく、外部操作なしのpreflightで検出し、Chrome成功と誤認しないことまでである。bundle更新後は同じsmokeと実機E2Eを再実行する。チャンネル名・本文・投稿までの保証は今回のread-only E2Eに含めない。

## 初回レビューで確認した項目

- 出力にraw本文、実ID、title値、local absolute pathが既定で含まれないか。
- configの優先順位と`--apply`停止線が妥当か。
- Desktop cacheを正本や完全保存として扱っていないか。
- Chrome bundle smokeの保証範囲を実機E2Eと混同していないか。
- レビュー後にcommit、runtime skill生成、read-only同期確認、pushへ進めてよいか。承認後にPR #24で実施し、マージ済み。

## レビュー時点の検証状態

- focused tests: 23 passed。
- 回帰本体: SSOT provenance停止線3件を除き447 passed。3件は、人間レビュー前にcommitしないため未確定の`ssot_commit`を検出している。
- `ops_check.py --profile fast`: 全9チェック成功。
- boundary logic / ingest route policy / secret scan / compile / `git diff --check`: 成功。
- 実cache inventory / Desktop cache probe / Chrome bundle smoke / Chrome read-only E2E: 実行済み。
- PR #24: 必須CI 4件成功後にマージ済み。head branch削除済み。
- runtime skill projection: PR #25後のprovenance検証成功。Codex / Claude Codeのinstalled copyと生成物のchecksum一致。

## 2026-07-16 運用closeout追補

現行`main`で、site adapterのschema依存が未導入のPythonからPDCA runnerを直接起動すると、runner自身が分類前に停止する運用gapを確認した。原因はpackage rootとCLIがsite adapterをeager importし、cache inventoryやPDCA計画まで同じ依存に結合していたことである。

運用境界を次のように修正する。

- package rootとCLIはsite adapterを使用時だけ遅延読込する。
- site adapter依存不足はtracebackではなく`dependency_missing`として返す。
- PDCA runnerは同じfailureを`environment_blocked`へ分類し、コード不良と混同しない。
- clean installの正規経路はREADMEの仮想環境と`pip install -e .`に固定する。
- `python -S`回帰で、外部site packageが無くてもPDCA dry-runとcache inventoryが起動することを検査する。
- repo残務closeoutがfull運用チェックを待てるよう、外側timeoutを30秒から180秒へ広げ、正常実行中の終了コード124を防ぐ。

この追補が保証するのは、ローカルread-only入口の起動、依存不足の安全な分類、生成skillの同期までである。Chrome/Discordの外部UI状態、Discord本文の完全保存、投稿成功は実行時の別証拠が必要であり、恒久保証には含めない。

### 追補の検証結果

- clean仮想環境へのeditable install: 成功。
- 全回帰: 460 passed。
- `ops_check.py --profile fast`: 全9チェック成功。
- `repo_goal_status.py --run-smoke`: full運用チェック成功。残るdirty判定は人間レビュー前の未commit差分だけ。
- SSOT projection / boundary logic / installed Codex・Claude Code skill同期: 全て成功。
- 実URLのbounded PDCA: 8ケース中6件成功。exact snapshot未保存とDesktop cache partial scanの2件は`partial_evidence`。コード修正、認証、Chrome bundle、人間承認のblockerは0件。
- 安全境界: Discord write、settings変更、自動コード修正は無効。raw本文とlocal pathは出力しない。

## PDCA運用

通常cycleは個別E2Eを直接1回ずつ実行する。timeout等だけ最大1回再試行し、SSOT provenance、人間承認、認証・権限は再試行せず停止線へ送る。重いDesktop cacheと入れ子になる`ops_fast`は明示opt-inとする。前回reportを渡すと`resolved / persisting / new_issues`が分かるため、解消済みfailureを人間が再調査しなくてよい。

実cycleでは、入れ子実行した`ops_fast`が子process環境で偽陰性になった。直接`ops_fast`は全成功したため、PDCA runnerの既定caseから入れ子orchestratorを外し、前回差分で`ops_fast: resolved`を確認した。Desktop cache全件読込は120秒timeoutになったため、header/key先読みと20秒budgetへ変更し、再実行は`partial_evidence`として約32秒で終了した。
