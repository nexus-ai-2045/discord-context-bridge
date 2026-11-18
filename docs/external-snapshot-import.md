# 外部snapshot競合の正式取込

## 範囲と正本

対象は非公開の一塊snapshotを持つ1行のNDJSONです。メッセージ単位のcaptureへ偽装しません。INDEX、legacy manifest、添付実体はこの入口の本文入力ではありません。

本文正本は、検証したサーバー・thread IDに対応する既存thread storeです。異なる対象の同本文を重複扱いしません。元の競合ファイルは削除・上書きしません。

## 入口

`scripts/import_external_snapshot.py` をDCB環境のPythonで実行します。必須引数は `--snapshot-root`、`--source-file`、`--target-url`、`--expected-source-sha256` です。値には既存の非公開台帳から解決した対象を使い、推測しません。

既定または `--dry-run` は検査のみです。`--json` の出力は状態・件数・安全なハッシュ・理由コードに限定します。本文、参加者、URL、実ID、保存パスは出力しません。

`--apply` の際はdry-runの正本ハッシュを `--expected-store-sha256` に渡します。元ファイルのSHA、対象一致、既存chainが不正なら停止します。

## 保存契約

- 元の `captured_at` は保持し、取込時刻を別の `ingested_at` として記録します。過去データを最新の可視取得として扱いません。
- 元ファイルの実バイトSHA、元行のハッシュ、観測元の来歴を新しいsnapshot観測eventへ結び付けます。
- 同じ入力の再実行は追加0件です。同じ本文でも別観測なら来歴を保持し、内容重複と観測重複を区別します。
- 既存single-writerのlock・chain検証・CAS追記を再利用します。元の正本バイト列をstageへ保持して正式eventを追記し、検証したstageだけをatomic swapします。反映前に元正本と入力を再照合し、反映後もread-backします。
- swap後の障害は `applied_unverified` と追記1件を返し、未反映の追記0件と区別します。この場合は隔離せず再検証します。lock終了まで成功した場合だけ `applied_verified` とします。
- private-only、外部共有不可、outbound無効を必須にします。symlink、対象外パス、不正入力、未証明のchainはfail-closedです。

## 競合ファイルと派生物

`absorbed` または検証済み `already_present` のread-back後に限り、別の管理手順で競合原本をrecoverableな隔離場所へ移せます。この取込コマンド自体は隔離・削除を行いません。

INDEX／manifestは本文正本にせず、参照先の吸収がすべて証明され、元の内容を再生成できる場合だけ更新します。未証明なら競合を保持してblockedとします。

## 依存と未保証

この変更はsingle-writer修復 `d37a08e` に積み重ねたものです。同修復の先行配送後にmainへrebaseして配送します。Mac内のlockをWindowsとの分散排他の証明には使いません。外部writerが継続する環境では別途single-writer構成の実証が必要です。

fixture成功は実データの吸収保証ではありません。実データのdry-run、必要なapply、chain再検証、4件別の判定と競合残数を別に記録します。
