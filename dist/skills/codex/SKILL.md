---
name: discord-context-bridge
description: Runtime adapter for the Discord Context Bridge SSOT. Generated for codex; do not edit by hand.
ssot_repo: nexus-ai-2045/discord-context-bridge
ssot_commit: 41070bc5d01295260471eaefa8d5bcac23848a30
manifest_version: discord_context_bridge_capability_manifest.v1
manifest_checksum: 3fe358a9875cdf721414c2dd198a786cd9a3fb5db429ac57e264717796bc7d43
contract_checksum: 91dd105884840006c2b0a0f0d5fde05ff2a94e891f675d94f1888b90175539c0
generated_at: 2026-09-01T11:41:42+00:00
runtime_target: codex
---

# Discord Context Bridge (codex)

This skill is generated from `nexus-ai-2045/discord-context-bridge`. Do not edit this generated file by hand; update `capability/manifest.yaml` or `docs/operating-contract.md`, then run `python3 scripts/export_runtime_skills.py`.

## Contract

# Discord Context Bridge operating contract

この文書は `nexus-ai-2045/discord-context-bridge` の runtime 横断 SSOT です。runtime skill はこの契約と `capability/manifest.yaml` から生成し、詳細手順は末尾の参照先を使います。

## 不変条件

- この public core は Discord への send / 自動返信 / reaction / delete / edit を直接実行しない。自動送信は、現在会話の明示承認と `auto-send-preflight=ready_for_auto_send_adapter` を含む全証跡が揃った private adapter の一回実行に限る。
- 外部共有、公開投稿、GitHub への raw Discord text 追加をしない。raw本文、実ID、handle、token、cookie、local absolute pathをvisible outputへ出さない。
- 取得は local-first / read-only とする。Bot REST tokenは環境変数またはsecret-command境界だけに置き、DCB本体、stdout、manifest、tracked file、runtime skillへ保存しない。
- Chrome profileからuser token、cookie、localStorage、profile directoryを抽出・転用しない。selfbot、browser console token抽出、MITM captureを採用しない。
- `no_ocr_for_dcb_text_intake`: OCR / screenshot / visionを本文取得経路にしない。
- `no_clipboard_without_explicit_clipboard_request`: clipboardはユーザーが明示した場合だけ読む。
- `no_unapproved_visible_ui_automation`: Computer Use 的な画面操作、SendKeys、AppActivate、クリック、スクロール、スクショ取得、Chromeを勝手に開く操作は、ユーザーの明示許可なしに実行しない。
- `no_browser_before_dcb_preflight`: Discord URLや返信案を扱う時は、内部ブラウザやChromeより先にDCB ingress、cache、coverage、route判定を通す。対象到達後の `ready_for_bridge` を別に確認し、ambient UIのDiscord URLだけを根拠にDCBを迂回しない。
- `no_visible_read_without_snapshot_closeout`: 可視DOMを読んだ場合は同じturnで `bridge-intake` へ渡し、`snapshot.saved=true`、対象一致、鮮度更新を確認する。保存確認より先に要約・判断・返信案・完了報告を返さない。raw本文は private artifact / local store に保存する。visible output には raw本文を貼らず、安全な件数と状態だけを返す。
- Discord文脈取得では Playwright / headless browser / 新規 browser profile を既定経路にしない。正規adapter、Bot REST、private inbox、cic可視DOM、Discord Desktop cache、承認済みmacOS Accessibilityの順に使う。
- DCB workflowから別projectのbot、ai-party、connector、外部 MCPを自動探索しない。範囲を広げる時はユーザー承認を取る。
- 判断を `[事実: source]` / `[推測]` / `[不明]` に分け、未確認文脈を断定しない。
- 投稿先推奨は `coverage-report --require-summary-ready` が終了コード `0` の時だけ行う。`captured_at` は取得鮮度だけに使い、keyword / topic / temperatureなどのheuristicは探索hintに限り、`metadata_only` 情報をユーザー本人の感想として書かない。条件不足は `unknown` として不足情報を質問する段階へ戻す。
- 外部action状態は `not_sent` / `staged` / `human_sent` / `blocked` / `unknown` で表し、入力・添付試行・宛先確認を送信完了としない。

## URLイベントと最新化

1. runtime入力にDiscord URLがあれば、追加依頼を待たず `discord_url_event_intake.py` へ `prompt_url_received` eventとして渡す。
2. 本文付きeventは同一呼出しでsnapshot保存まで閉じる。本文なしeventはdurable queueへ入れ、同じruntime turnで設定済みread-only sourceを使い `--drain-once` を一回実行する。
3. Gateway runtimeは通知時、常駐runnerは起動・再接続・通知時にdrainする。pendingだけを15〜60分のREST reconciliationで補完する。
4. workerはtimeoutより長いleaseでjobをclaimし、期限切れjobを再取得する。同一event IDを冪等に扱い、leaseを失ったworkerは成功を返さない。
5. 新着はGateway、欠落補完はREST、Chrome eventは補助経路とする。Chrome eventだけで完全性を主張しない。
6. source未設定、認証不可、rate limit、取得失敗はpendingまたはblockedとし、古いcacheを返信判断へ昇格しない。

## 取得・保存・完了

- intentを `read-current-visible` / `full-capture` / `reply-review` / `posted-record` に分け、依頼より広い取得へ自動拡張しない。
- 取得順は Gateway live event → Bot REST → private inbox/adapter → 承認済みChrome visible fallback。Chrome前にタブ棚卸しを行い、既存対象タブを優先する。
- recent cacheでも、現在turnでURLを受けた場合は完全一致URLのlive refreshを試す。`use_local_snapshot` は明示的なoffline調査だけに使う。
- 観測本文はlocal-private append-only ledgerへ保存する。Markdown、report、context reconstruction、TODOはprojectionであり履歴正本ではない。
- fullを名乗るには、対象結合、最古端、最新watermark、2回以上の安定走査、gap/duplicate 0、raw/Markdown/ledgerの集合・順序・hash一致、添付inventory、pending retry 0、外部action無効を `full-capture-gate` で確認する。
- 条件不足は `partial` または `blocked` とし、取得済み範囲、未取得理由、次の安全な一手をmanifest / closeoutへ残す。0件cacheは本文不存在や完全保存の証明にしない。
- snapshot保存とcloseoutを分ける。可視本文を読めても、ledger、capture、manifest保存前に取得完了と言わない。

## 返信と送信境界

- 返信案には、スレッド起点、返信対象、対象までの直前10件（全履歴が10件未満なら終端確認済み全件）、未解決参照0件を要求する。
- 不足時は `reply_context_expand_required`、上限到達は `reply_context_limit_reached`、認証・権限・rate limitは取得層のreason codeを保持して停止する。
- `guide-reply` / `review-draft` は文脈gateと `summary_ready` を迂回しない。出力はraw JSONではなく、安全ラベル、件数、reason code、短い日本語要約にする。
- 自動送信要求でも `stage-discord-send` → `verify-chrome-fill-dry-run` → `auto-send-preflight` の順を守る。public coreは送信しない。
- `human_sent` のcloseoutにはmetadata-only receiptと `learning_handoff` を付ける。`not_sent` の下書きを本人の返信スタイルとして学習しない。

## Chrome境界

- Chrome visible fallbackは正規取得口が使えない時だけ選び、明示承認前は `blocked_need_chrome_visible_read_go` で止める。
- `browser.user.openTabs()` 相当で棚卸しし、対象タブclaim → 既存Discordタブclaimと対象遷移 → 既存Chrome内の新規タブ、の順にする。新規windowは既定で開かない。
- DOM/APIを優先し、Computer Use、wheel、key入力を自動fallbackにしない。DOMが使えなければ `paused_human_approval` とする。
- send / reaction / edit / delete、token表示・copy、permission変更は別の人間承認境界とする。

## OSS参照境界

- API仕様は `discord/discord-api-docs` を優先し、SDK、bot template、exporter、MCP実装は比較材料に限る。
- readとmutationが同一面にある実装は分離し、人間承認gateを追加するまで採用しない。

## Codex chat title

- Discord URLの内容確認ではCodex task titleを対象にし、認証済みlive title → 同一対象のsaved evidence → safe route labelの順で根拠を選ぶ。
- 状態suffixは `｜Ingress確認済み・本文未取得` / `｜本文未完了・要追加読取` / `｜Review待ち・送信なし` を使い、raw本文、参加者名、実ID、private pathを入れない。

## Runtime projectionと検証

- 生成物は `dist/skills/<runtime>/SKILL.md`。直接編集せず、`capability/manifest.yaml` またはこの契約を変更して `scripts/export_runtime_skills.py` で再生成する。
- PR前とcloseoutで `verify_ssot_projection.py`、`lint_ingest_route_policy.py`、必要範囲のtestsを実行する。local runtimeへの反映は `lint_runtime_skill_sync.py` で差分確認後、対象を明示して同期する。

## 詳細SSOT

- route選択と状態: [`routes.md`](./routes.md)
- 全文取得、ledger、lease、full receipt: [`capture-loop-operations.md`](./capture-loop-operations.md)
- 返信前文脈gate: [`reply-context-routing.md`](./reply-context-routing.md)
- Chrome能力、fill-only、送信停止: [`codex-chrome-extension-capability-inventory.md`](./codex-chrome-extension-capability-inventory.md)
- snapshot / closeout分離: [`architecture-context-closeout.md`](./architecture-context-closeout.md)
- CLI・schema・MCPの全参照: [`full-reference.md`](./full-reference.md)

## Stoplines

- `no_public_core_direct_discord_send`
- `no_external_share`
- `no_raw_discord_text_in_visible_output`
- `no_tokens_or_cookies`
- `no_playwright_default_for_discord_context`
- `no_unapproved_visible_ui_automation`
- `no_browser_before_dcb_preflight`
- `no_visible_read_without_snapshot_closeout`
- `no_complete_claim_without_full_local_capture`
- `no_ocr_for_dcb_text_intake`
- `no_clipboard_without_explicit_clipboard_request`
- `no_visible_read_route_expansion_after_blocked_need_chrome_visible_read_go`
- `no_cross_route_webhook_or_bot_guessing`
- `no_recommendation_without_summary_ready`
- `no_metadata_only_personal_impression`

## Commands

- `python3 scripts/discord_url_event_intake.py --url <discord-url> --event-id <runtime-event-id> --source prompt_url_received --json`: Discord URLを含むruntime入力を冪等なlive-refreshイベントとして受け、Gateway / REST / inbox / Chrome補助経路へ接続する
- `python3 scripts/discord_url_event_intake.py --drain-once --source-command <configured-read-only-source> --json`: pending URL jobをlease付きでclaimし、設定済みread-only sourceから本文を取得してsnapshotまで閉じる
- `python3 scripts/codex_discord_ingress_smoke.py --preflight-only --current-url <discord-url> --json`: 内部ブラウザやChromeより先にDiscord URLをsafe metadataとしてDCB ingressへ通す
- `python3 scripts/discord_rest_backfill.py --url <discord-url> --json`: Bot REST API で履歴を read-only backfill し、private raw artifact と metadata-only manifest を作る
- `thread-capture-plan`: Discord スレッド全文取得に必要な route 配線状態を本文なしで確認する
- `full-capture-gate`: 対象結合、境界、ID集合と順序、添付inventory、再走査、再試行残件を照合し、全文取得をfail-closedで判定する
- `reply-context-plan`: 返信前のスレッド起点・返信対象・直前10件と追加取得要否を本文なしで判定する
- `cache-first-intake`: ローカル cache / snapshot を先に見て private book を作る
- `cache-inventory`: URL完全一致のsnapshot件数、Markdown件数、title根拠、鮮度と次の取得判断をmetadata-onlyで返す
- `configure-local-cache`: cache場所をdry-runし、明示されたapply時だけuser configへ安全に保存する
- `desktop-cache-probe`: Discord Desktop cacheの対象URL参照を本文なしのread-only metadataとして確認する
- `python3 scripts/pdca_e2e_inventory.py --json`: E2E caseをbounded実行し、失敗を修正・環境・外部依存・人間レビューへ分類する
- `coverage-report --require-summary-ready`: Discord URL / target_key の coverage と freshness を本文なしで確認し、投稿先推奨前はsummary_ready未達を終了コード2で拒否する
- `python3 scripts/chrome_visible_fallback_guard.py --json`: Chrome visible fallback の前に既存Discordタブ棚卸しを評価し、対象タブclaimまたは既存Discordタブclaim+target navigationで新規タブ作成を迂回する
- `import-visible-text`: 可視テキストをローカル event store に取り込む
- `context-passport`: 可視テキストから文脈カードを作る
- `guide-reply`: 可視テキストと下書きから返信前ガイドを作る
- `review-draft`: 下書きの文脈適合・トーン・不足前提を確認する
- `auto-send-preflight`: private adapter に自動送信を許可してよいかを明示承認・宛先一致・dry-run・監査証跡で fail-closed 判定する
- `closeout-discord-send`: 人間送信後のmetadata-only記録を閉じ、posted-recordから抽象化した学びをabsorbed-dialogue-routerへ渡すlearning_handoffを返す

## Verification

- `python3 scripts/verify_ssot_projection.py --json`: SSOT から生成された runtime skill が最新か確認する
- `python3 scripts/lint_runtime_skill_sync.py --target codex=/path/to/SKILL.md --json`: runtime skill directory の実体が SSOT 生成物と一致するか read-only で確認する
- `python3 scripts/lint_ingest_route_policy.py --json`: Discord 文脈取得で Playwright を既定経路にしない運用を確認する
- `python3 scripts/ops_check.py --gh`: test / smoke / secret scan / GitHub account をまとめて確認する
- `python3 scripts/codex_chrome_bundle_smoke.py --json`: Chrome browser bundleのhost process衝突回帰を外部操作なしで検出する
