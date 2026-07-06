# Discord Context Bridge operating contract

この文書は `nexus-ai-2045/discord-context-bridge` の runtime 横断 SSOT です。Codex / Claude Code / Grok / AntiGravity などの skill は、この contract と `capability/manifest.yaml` から生成される派生物として扱います。

## 不変条件

- Discord への send / 自動返信 / reaction / delete / edit はしない。
- 外部共有、公開投稿、GitHub への raw Discord text の追加はしない。
- raw Discord 本文、実 guild/channel/message ID、handle、token、cookie、local absolute path を visible output に出さない。
- 取得経路は local-first / read-only とし、可視テキスト・clipboard・private adapter のいずれも outbound action を持たない。
- Discord 文脈取得では Playwright / headless browser / 新規 browser profile を既定経路にしない。既定は cic（claude-in-chrome）可視DOM、貼り付け/ファイル、Discord Desktop cache、macOS Accessibility とする。Playwright はユーザー明示、または Discord 本文取得ではない周辺UIの限定調査だけに使う。
- `discord-context-bridge` の Discord URL / 返信下書き workflow では、別プロジェクトの Discord bot、ai-party、ChatGPT connector、外部 MCP を自動探索しない。既定の順序で未設定なら DCB 内の fallback reason を返し、スコープを広げる時はユーザーの明示承認を取る。
- 判断は `[事実: source]` / `[推測]` / `[不明]` に分け、未確認の文脈を断定しない。

## 標準フロー

1. Discord URL ingress を `codex_discord_ingress_smoke.py` で safe metadata として確認する。
2. API / bot inbox / private adapter / clipboard / visible fallback の DCB 内順序で本文取得経路を確認する。
3. 可視テキストを local file または stdin から `import-visible-text` に渡す。
4. `context-passport` で文脈カードを作る。
5. 返信案がある時だけ `guide-reply` または `review-draft` で確認する。
6. 出力は JSON をそのまま貼らず、安全ラベル、件数、reason code、短い日本語要約にする。

## 更新・保存・完了保証

Discord 文脈を読んだり、返信確認、資料DL、下書き、送信後追跡を行う時は、作業完了より先にローカル保存を閉じる。

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

- 可視本文を読めた場合、append-only ledger、`capture`、`manifest` を保存する前に「取得完了」と言わない。
- 同一内容の再取得でも、観測した事実は ledger に追記する。重複排除は保存停止ではなく `content_hash` / `previous_content_hash` / `changed` / `duplicate_content` で表す。
- 追記 ledger の 1 行は immutable event として扱う。訂正が必要な場合は既存行を書き換えず、補正・再取得・metadata 補足を新しい observation として追記する。
- `stream_id` は target 単位の履歴を再生するキー、`stream_sequence` は target 内の順序、`event_id` は観測行そのものの一意識別子として使う。
- `previous_event_hash` と `event_hash` で target stream 内の hash chain を作る。これは改ざん検知を助ける local-private metadata であり、公開証明や外部監査への提出を意味しない。
- 返信チェックをした場合、`reply-check-*.md` または同等の artifact と active TODO 更新が済むまで「チェック完了」と言わない。
- ユーザー手動送信を追跡する場合、posted-record または TODO への明示記録が済むまで「送信後追跡完了」と言わない。
- full read でない場合は `本文全文: 未完了`、`partial`、`blocked` のどれかを残し、理由を人間語で書く。
- snapshot 保存と closeout は混ぜない。本文保存は private snapshot / capture bundle、完了確認は metadata-only closeout として扱う。

本文取得の既定順序:

1. `codex_discord_ingress_smoke.py`: URL / Chrome 状態の safe metadata 確認。
2. `discord_route_retry_decider.py`: `gateway_live_event`、`rest_backfill`、`bot_text_event_inbox` の順に確認。
3. `private_adapter_probe.py`: `DISCORD_CONTEXT_BRIDGE_PRIVATE_ADAPTER` / `DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND` の状態を確認。
4. `read_visible_discord_text.py --from-clipboard` または明示された local file / source command。
5. Chrome visible fallback / OCR region は、DCB 内の safe fallback reason を返した後、ユーザーが明示した場合だけ使う。

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
