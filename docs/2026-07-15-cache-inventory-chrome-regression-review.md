# ローカルcache inventory / Chrome回帰レビュー

## 結論

この変更は、Discord URLを受け取った後に「保存済みを使うか、最新取得へ進むか」をmetadata-onlyで決める入口を追加する。ローカル設定、正規化済みsnapshot、Discord Desktop cache、Chrome browser bundleを別レイヤーとして扱う。

コミット・push・runtime skillへの反映は、人間レビュー後に行う。

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

## 人間レビュー項目

- 出力にraw本文、実ID、title値、local absolute pathが既定で含まれないか。
- configの優先順位と`--apply`停止線が妥当か。
- Desktop cacheを正本や完全保存として扱っていないか。
- Chrome bundle smokeの保証範囲を実機E2Eと混同していないか。
- レビュー後にcommit、runtime skill生成、read-only同期確認、pushへ進めてよいか。

## レビュー時点の検証状態

- focused tests: 23 passed。
- 回帰本体: SSOT provenance停止線3件を除き447 passed。3件は、人間レビュー前にcommitしないため未確定の`ssot_commit`を検出している。
- `ops_check.py --profile fast`: 全9チェック成功。
- boundary logic / ingest route policy / secret scan / compile / `git diff --check`: 成功。
- 実cache inventory / Desktop cache probe / Chrome bundle smoke / Chrome read-only E2E: 実行済み。
- commit / push / installed runtime skill同期: 未実行。

## PDCA運用

通常cycleは個別E2Eを直接1回ずつ実行する。timeout等だけ最大1回再試行し、SSOT provenance、人間承認、認証・権限は再試行せず停止線へ送る。重いDesktop cacheと入れ子になる`ops_fast`は明示opt-inとする。前回reportを渡すと`resolved / persisting / new_issues`が分かるため、解消済みfailureを人間が再調査しなくてよい。

実cycleでは、入れ子実行した`ops_fast`が子process環境で偽陰性になった。直接`ops_fast`は全成功したため、PDCA runnerの既定caseから入れ子orchestratorを外し、前回差分で`ops_fast: resolved`を確認した。Desktop cache全件読込は120秒timeoutになったため、header/key先読みと20秒budgetへ変更し、再実行は`partial_evidence`として約32秒で終了した。
