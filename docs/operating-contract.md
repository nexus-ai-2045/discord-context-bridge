# Discord Context Bridge operating contract

この文書は `nexus-ai-2045/discord-context-bridge` の runtime 横断 SSOT です。Codex / Claude Code / Grok / AntiGravity などの skill は、この contract と `capability/manifest.yaml` から生成される派生物として扱います。

## 不変条件

- この public core は Discord への send / 自動返信 / reaction / delete / edit を直接実行しない。
- 自動送信は private adapter 境界の任意機能としてのみ扱う。`auto-send-preflight` が `ready_for_auto_send_adapter` を返し、現在会話の明示承認、対象 route 一致、ready な staging packet、ready な fill dry-run、transport 設定、operator label、idempotency key、rollback plan、metadata-only audit log が揃う場合だけ、private adapter が一回送信してよい。
- `auto-send-preflight` が `blocked` の場合、または `send_message()` public API しかない場合は送信しない。
- 外部共有、公開投稿、GitHub への raw Discord text の追加はしない。
- raw Discord 本文、実 guild/channel/message ID、handle、token、cookie、local absolute path を visible output に出さない。
- 取得経路は local-first / read-only とし、可視テキスト・clipboard・private adapter のいずれも outbound action を持たない。
- `no_ocr_for_dcb_text_intake`: OCR / screenshot / vision を Discord 本文取得ルートにしない。
- `no_clipboard_without_explicit_clipboard_request`: clipboard はユーザーが「clipboard から」と明示した場合だけ読む。
- `no_unapproved_visible_ui_automation`: Computer Use 的な画面操作、`SendKeys`、`AppActivate`、クリック、スクロール、スクショ取得、Chromeを勝手に開く・遷移する操作は、ユーザーの明示許可なしに実行しない。DCB の Chrome visible fallback は、正規 adapter / DOM取得口 / clipboard / local file が使える場合だけ進め、Windows UI 自動操作へ迂回しない。
- Bot REST backfill は read-only 主経路として扱う。bot token は環境変数または private control plane にだけ置き、値を stdout、manifest、repo-tracked file、runtime skill に出さない。Keychain / credential store の継続利用は `DISCORD_CONTEXT_BRIDGE_TOKEN_COMMAND` などの secret-command 経由に限定し、DCB 本体は token 値や vault 内部を保持しない。
- Chrome profile から user token、cookie、localStorage、profile directory を抽出して REST / selfbot に流用しない。Chrome は既存タブの可視読取、手動コピー支援、限定 fallback に留める。
- Chrome visible fallback では、本文読取や新規タブ作成より先に `browser.user.openTabs()` 相当の棚卸しを `chrome_visible_fallback_guard.py` に通す。対象URLの既存タブがあれば claim し、対象外の Discord タブしかない場合も既存Discordタブを claim して対象URLへ移動する。再利用可能な Discord タブがない場合だけ、既存Chromeウィンドウ内で新規タブを開く。
- Discord 文脈取得では Playwright / headless browser / 新規 browser profile を既定経路にしない。既定は cic（claude-in-chrome）可視DOM、貼り付け/ファイル、Discord Desktop cache、macOS Accessibility とする。Playwright はユーザー明示、または Discord 本文取得ではない周辺UIの限定調査だけに使う。
- `discord-context-bridge` の Discord URL / 返信下書き workflow では、別プロジェクトの Discord bot、ai-party、ChatGPT connector、外部 MCP を自動探索しない。既定の順序で未設定なら DCB 内の fallback reason を返し、スコープを広げる時はユーザーの明示承認を取る。
- 送信補助 workflow で webhook / bot / browser の投稿先が一致しない場合は送信しない。通知用 webhook や別 guild の bot token を、目的チャンネルの代替経路として使わない。
- 判断は `[事実: source]` / `[推測]` / `[不明]` に分け、未確認の文脈を断定しない。
- 送信補助 workflow の状態は、外部 action 状態と照合して `not_sent` / `staged` / `human_sent` / `blocked` / `unknown` に分ける。下書き入力、添付試行、送信先確認を送信完了として扱わない。

## 標準フロー

1. Discord URL ingress を `codex_discord_ingress_smoke.py` で safe metadata として確認する。
2. API / bot inbox / private adapter / visible fallback の DCB 内順序で本文取得経路を確認する。clipboard はユーザーが明示した場合だけ使う。
3. `rest-backfill` または private command で読めた本文を private raw artifact と metadata-only manifest に保存する。
4. 可視テキストを local file または stdin から `import-visible-text` / `snapshot-discord-url-text` に渡す。
5. `coverage-report` と `thread-capture-plan` で full / partial / blocked を確認する。
6. `context-passport` で文脈カードを作る。
7. 返信案の前に `reply-context-plan` を通し、スレッド起点、返信対象、返信対象までの直前10件を最低限取得する。スレッド全体が10件未満なら履歴終端の確認を必須にする。
8. 指示語、引用、添付、過去回答などの未解決参照が残る場合は10件ずつ追加取得する。
9. `reply-context-plan` が `ready` / `ready_short_thread` の時だけ、`guide-reply` または `review-draft` で確認する。
10. 自動送信要求がある場合でも、`stage-discord-send` と `verify-chrome-fill-dry-run` を先に通し、最後に `auto-send-preflight` で private adapter 実行可否を判定する。public core 自体は送信しない。

## ローカルcache解決と鮮度判断

- 正規化済みsnapshot rootは、`--cache-root`、`DISCORD_CONTEXT_BRIDGE_SHARED_SNAPSHOT_ROOT`、user config、OS既定の順で解決する。最初にユーザーが場所を指定して保存した後は、同じuser configを参照する。
- user configは既定で `~/.config/discord-context-bridge/config.json` とする。`DISCORD_CONTEXT_BRIDGE_CONFIG` で変更できる。`configure-local-cache` はdry-runを既定とし、`--apply` の時だけatomic writeとmode `0600`で保存する。
- `cache-inventory` は対象URLの完全一致snapshot件数、ローカルMarkdown/NDJSON件数、title evidence、freshnessをmetadata-onlyで返す。raw本文、実ID、local path、title値は既定出力に含めない。
- `cache-inventory.decision` は `use_local_snapshot`、`refresh_exact_url_snapshot`、`capture_visible_or_read_only_adapter` のいずれかとし、古いsnapshotを最新として扱わない。
- Discord Desktop cacheは対象参照の有無を調べるread-only補助経路であり、append-only ledgerや正規化済みsnapshotの正本ではない。cache hitだけで本文取得、完全保存、title確定を主張しない。
- `codex_chrome_bundle_smoke.py` はbrowser bundleの通常importと、host側の保護済み`process`がある条件でのimportだけを検査する。Node REPL接続、Chrome接続、Discord DOM到達、投稿成功の保証には使わない。
- browser bundleが`protected_process_conflict`なら、Chrome可視読取へ進まず、人間語の原因とread-only fallbackを返す。生成済みplugin cacheを直接書き換えず、修正元sourceまたは上流更新で直す。
- `pdca_e2e_inventory.py` はE2E caseを一度ずつbounded実行し、成功、証拠不足、コード修正、環境、外部依存、人間レビューへ分類する。同じ失敗経路を無制限に再試行せず、timeout等の一時失敗だけ最大1回再試行する。
- PDCA runnerは別orchestratorである`ops_check.py`を既定で入れ子実行しない。`--include-ops-fast`が明示された場合だけ含める。コード自動修正、設定変更、Discord writeは行わない。
- 前回reportを指定した場合、case単位で`resolved`、`persisting`、`new_issues`を返し、既に解消したfailureを再調査対象にしない。

## 返信前最低文脈 gate

返信支援の生成入口は fail-closed とする。本文が1件以上あることや、人間が理解確認を押したことだけでは返信候補を生成しない。

- スレッド起点を取得済み。
- 返信対象を取得済み。
- 返信対象までの直前10件を取得済み。ただしスレッド全履歴が10件未満で、履歴終端を確認した場合は取得可能な全件でよい。
- 未解決参照が0件。

不足時は `reply_context_expand_required` と次の取得件数を返す。取得上限に達した場合は `reply_context_limit_reached`、認証・権限・rate limit の場合は取得層の reason code を保持して停止する。いずれも raw本文、参加者名、実IDをstdoutへ返さず、Discord writeは無効のままにする。
8. 出力は JSON をそのまま貼らず、安全ラベル、件数、reason code、短い日本語要約にする。

## 更新・保存・完了保証

Discord 文脈を読んだり、返信確認、資料DL、下書き、送信後追跡を行う時は、作業完了より先にローカル保存を閉じる。

この skill を Discord URL / スレッド保存に使った場合、「スレ本文・全メッセージ・添付を完全保存」を完了条件にする。
取得経路の未設定、認証、可視範囲不足、rate limit、キャッシュ欠落などで完全保存できない場合でも、完了とは言わない。
その場合は `blocked` として、人間語の原因、未取得範囲、次に必要な操作、保存済み partial artifact を manifest / checklist / closeout に残す。
ただし、依頼種別を先に分ける。ユーザーが「いま見えている本文」「スレッド本文」「本文を出せ」「Chrome可視読取GO」など、可視範囲の読取を求めている場合は `read-current-visible` とし、完全保存ラダーへ拡張しない。
`read-current-visible` では、REST / private adapter / exact local cache が未設定または不足なら、即 `blocked_need_chrome_visible_read_go` または承認済みなら Chrome 既存タブ可視DOM読取へ進む。clipboard、Discord Desktop cache、OCR、古い snapshot 探索へ横道に入らない。
保存要求である場合だけ、最初の API / adapter 不足だけで止めず、DCB 内の取得ラダーを順に実行し、可視テキスト、明示された local file、Discord Desktop cache、Chrome 既存タブ visible fallback、添付候補の保存まで試す。clipboard はユーザーが「クリップボードから」と明示した場合だけ使う。
止まってよいのは、認証不可、ブラウザ制御不能、対象画面未到達、ユーザー操作待ち、対象本文が実測 0 件、または安全境界上これ以上進めないことを確認した後だけにする。

この領域は event sourcing / structured logging の考え方に寄せる。append-only store を system of record とし、Markdown、latest report、context reconstruction、TODO は projection / view として扱う。

標準順序:

1. `target`: URL / safe label / title evidence / read scope を確定する。
2. `ledger`: 一度でも取得した可視テキスト観測は、重複でも append-only snapshot store に追記する。DCB の既定台帳は `.local/discord-context-bridge/text-snapshots.ndjson`。同一内容でも保存を省略せず、`changed=false` / `duplicate_content=true` のように差分状態を metadata で表す。各行には `schema`、`event_id`、`event_type`、`stream_id`、`stream_sequence`、`expected_previous_stream_sequence`、`time`、`content_hash`、`previous_content_hash`、`previous_event_hash`、`event_hash`、`acquisition_context` を持たせる。
3. `capture`: 可視テキスト、添付候補、画像、Drive等の取得結果を、生データまたは抽出済みデータとして保存する。Markdown や画像フォルダは読みやすい view / bundle であり、履歴正本は append-only ledger とする。
4. `manifest`: 取得時刻、取得方法、full / partial / blocked、対象一致、未取得理由を manifest または front matter に書く。
5. `digest`: 読解メモ、reply-check、FDEメモ、context reconstruction など、人間が読むための派生artifactを作る。
6. `todo`: active TODO / checklist / handoff に、新規artifact path、現状態、次の安全な一手、未取得点を追記する。
7. `closeout`: 最後に、Discord writeなし / human sent / blocked reason / next action を報告する。

保証:

- Discord URL / スレッド保存では、スレ本文、全メッセージ、添付の3点が local private artifact として保存され、raw JSONL parse、normalized Markdown coverage、attachment ledger / manifest の整合が確認できるまで「全部保存」「完全保存」「取得完了」「運用保証」と言わない。
- 3点のどれかが欠ける場合は `blocked` または `partial` として扱い、保存できた証跡だけを完成物と呼ぶ。0件の cache extract は「ローカルキャッシュに対象本文が存在しない証跡」であり、「スレ本文なし」や「完全保存」とは扱わない。
- `blocked` と言う前に、どの取得ラダーを実行し、どの hard blocker で止まったかを manifest / closeout に列挙する。未実行ルートが残っている場合は「未完了・次に実行」と言い、blocked closeout にしない。
- 可視本文を読めた場合、append-only ledger、`capture`、`manifest` を保存する前に「取得完了」と言わない。
- 同一内容の再取得でも、観測した事実は ledger に追記する。重複排除は保存停止ではなく `content_hash` / `previous_content_hash` / `changed` / `duplicate_content` で表す。
- 追記 ledger の 1 行は immutable event として扱う。訂正が必要な場合は既存行を書き換えず、補正・再取得・metadata 補足を新しい observation として追記する。
- `stream_id` は target 単位の履歴を再生するキー、`stream_sequence` は target 内の順序、`event_id` は観測行そのものの一意識別子として使う。
- `previous_event_hash` と `event_hash` で target stream 内の hash chain を作る。これは改ざん検知を助ける local-private metadata であり、公開証明や外部監査への提出を意味しない。
- 返信チェックをした場合、`reply-check-*.md` または同等の artifact と active TODO 更新が済むまで「チェック完了」と言わない。
- ユーザー手動送信を追跡する場合、posted-record または TODO への明示記録が済むまで「送信後追跡完了」と言わない。
- 自動送信を使う場合、`auto-send-preflight` の ready packet、private adapter の idempotency receipt、post-send closeout の3点が metadata-only artifact として揃うまで「自動送信完了」「運用保証」と言わない。
- ユーザーが途中で停止した場合は、送信しなかったことを `not_sent` として closeout する。入力欄準備、添付試行、送信先確認は送信完了とは別の状態として扱う。
- full read でない場合は `本文全文: 未完了`、`partial`、`blocked` のどれかを残し、理由を人間語で書く。
- snapshot 保存と closeout は混ぜない。本文保存は private snapshot / capture bundle、完了確認は metadata-only closeout として扱う。

本文取得の既定順序:

0. `intent_router`: `read-current-visible` / `full-capture` / `reply-review` / `posted-record` を先に切る。
1. `codex_discord_ingress_smoke.py`: URL / Chrome 状態の safe metadata 確認。
2. `discord_route_retry_decider.py`: `gateway_live_event`、`rest_backfill`、`bot_text_event_inbox` の順に確認。
3. `discord_rest_backfill.py`: bot token の環境変数または secret-command provider が設定済みの場合だけ Bot REST API で read-only に履歴を backfill し、stdout は metadata-only にする。provider の状態は `env` / `secret_command` / `missing` の安全ラベルだけを返し、rate limit は `rate_limited_retryable` として closeout に残す。
4. `private_adapter_probe.py`: private adapter / private command の設定状態を確認。
5. `read-current-visible` で 2-4 が未設定なら、ここで `blocked_need_chrome_visible_read_go` を返す。すでにユーザーが Chrome 可視読取を許可している場合だけ次へ進む。
6. `chrome_visible_fallback_guard.py`: Chrome visible fallback の前に既存タブ棚卸しを評価し、既存対象タブ claim / 既存Discordタブ claim + target navigation / 既存Chromeウィンドウ内の新規タブ作成を決める。
7. `read-current-visible` では Chrome 可視DOMからスレッド本文だけを抽出し、raw本文は private artifact / local store に保存する。ユーザーが「本文を出して」と求めた場合でも visible output には raw本文を貼らず、件数、coverage、保存先の safe label、未取得理由だけを返す。
8. `full-capture` では明示された local file / source command、Discord Desktop cache、添付候補の保存まで進める。clipboard は明示時のみ。
9. OCR / screenshot / vision は DCB 本文取得ルートから除外する。使う場合は DCB ではなく別 skill / 別 task として Type1 明示承認を取る。

この順序から外れて別 repository や別 Discord bot workflow へ行った場合は、作業を止めて DCB スコープに戻す。

## Codex chat title reflection

Discord URL の内容確認、スレッド名確認、または「スレタイをチャットタイトルに反映」の依頼では、
Codex chat/thread title 自体を更新対象にする。ファイル名、Discord 側タイトル、投稿タイトルではない。

title evidence は次の順で扱う。

1. 認証済み browser / trusted local capture から現在読める Discord visible title。
2. 同一 URL / thread / channel と明確に対応する saved snapshot、posted-record、extract manifest、prior rollout record。
3. 実 title が未取得の場合だけ `Discord channel ...` のような safe route label。

実 title が分かっている場合は generic fallback label を残さない。title には verified-state suffix を付ける。

- `｜Ingress確認済み・本文未取得`
- `｜本文未完了・要追加読取`
- `｜Review待ち・送信なし`

suffix は intent ではなく確認済み状態を表す。title が fresh live read ではなく prior local record 由来の場合は、
final status でその根拠を明示する。title に raw message text、participant names、token、webhook URL、
browser profile path、private local path を入れない。

## Runtime skill projection

各 runtime 用 skill は `scripts/export_runtime_skills.py` で生成する。生成物は `dist/skills/<runtime>/SKILL.md` に置き、先頭に次の provenance を含める。

- `ssot_repo`
- `ssot_commit`
- `manifest_version`
- `manifest_checksum`
- `contract_checksum`
- `generated_at`

生成物は手編集しない。変更は `capability/manifest.yaml` またはこの contract に入れ、再生成する。

## Verification

PR 前と運用 closeout では次を確認する。

```bash
python3 scripts/verify_ssot_projection.py --json
python3 scripts/lint_ingest_route_policy.py --json
python3 scripts/ops_check.py --gh
```

`verify_ssot_projection.py` は runtime skill の欠落、stale、provenance 不一致、private / raw data 混入を検出する。

`lint_ingest_route_policy.py` は contract / runtime skill / local Claude skill に Playwright fallback 禁止の運用文が残っているかを検出する。

各 runtime の local skill directory は、勝手に書き換えず read-only lint で同期状態を確認する。

```bash
python3 scripts/lint_runtime_skill_sync.py \
  --target codex=/path/to/discord-context-bridge/SKILL.md \
  --json
```

`lint_runtime_skill_sync.py` は target file を読み、`dist/skills/<runtime>/SKILL.md` と完全一致するか、`ssot_commit` / checksum / privacy pattern を確認する。更新が必要な場合でも、この script は書き込みを行わない。
