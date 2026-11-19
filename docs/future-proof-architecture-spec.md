# Discord Context Bridge / Nexus リポ共通 変更耐性アーキテクチャ仕様

Status: proposed
Measured: 2026-07-14
Scope: local-first / read-only capture, normalization, projection, review support, Nexus repository pattern

obsidian_check:
- query_or_file: Documents/brain/pre-execution-fact-check-gate.md / Documents/brain/scope-routing-gate.md / Documents/nexus_ai/references/ssot-output-gate-and-repo-routing.md
- source_status: active
- adopted_ssot: 事前 fact tag、scope_route、SSOT repo routing、公開境界を採用
- fact_tags_used: yes

scope_route:
- selected_scope: multi-file design artifact / no external action
- why_this_scope: `discord-context-bridge` の変更耐性設計を、Nexus 系リポへ展開できる共通型へ磨くため
- owner: Codex main review
- write_scope: `discord-context-bridge` 専用 worktree の docs 追加のみ
- collision_risk: low。元の dirty main には書かない
- worktree: dedicated local worktree for `codex/dcb-contracts-task1`
- type1_risk: none。push、PR、公開、外部送信は行わない
- evidence_required: source docs、git status、diff check

## 1. この仕様の狙い

Discord API、権限、DOM、保存形式、CLI/MCP のいずれかが変わっても、変更を一つの境界内に閉じ込める。
既存の公開 API、metadata-only 出力、append-only ledger、Discord write 無効化は互換契約として維持する。

さらに、この設計を Nexus 系リポ全体へ展開する時の共通型として、次の 5 点を抽出する。

1. 公開・外部操作の前に固定する `contract`
2. 実測と判断を分けて残す `evidence`
3. 外部依存を閉じ込める `adapter`
4. local success と external unverified を分ける `readiness gate`
5. public / push / PR / send を止める `publication stopline`

この仕様は `docs/superpowers/plans/2026-07-11-maintainability-refactor.md` を置き換えない。同計画の Task 1-10 が従う設計契約を定義し、Nexus 展開時の判断基準を補う。

## 2. 実測ベースライン

2026-07-14 のローカル実測:

- `src/discord_context_bridge/core.py`: 3,921 lines / 168,599 bytes
- `src/discord_context_bridge/cli.py`: 1,629 lines / 84,063 bytes
- `tests/test_core.py`: 5,173 lines / 195,294 bytes
- full pytest: 335 passed
- 最遅 test: 約10秒。`ISSUE_LIST.md` の「最上位1秒台以下」は未達
- repo content sync: `HEAD...origin/main = 0/0`
- working tree: 既存未commit差分あり
- residual dashboard: `attention_required`。dirty tree と open PR 2件を local safety failure と分離表示
- safety boundary / ops smoke: green

2026-07-14 の作業用 branch 実測:

- branch: `codex/dcb-contracts-task1`
- latest commit: `ba62200 test: freeze public API and safety contracts`
- state: clean, `origin/main` から ahead 1
- verified: focused tests 6 passed、full pytest 338 passed、full ops gate success、`git diff --check` success

結論: 機能の全面再実装ではなく、互換 facade を残した責務分割が最小リスクである。Nexus 全体へ展開する場合も、まず契約と gate を固定し、repo ごとの実装詳細は adapter に閉じ込める。

## 3. 採用アーキテクチャ

```text
Ingress Adapter
    -> Policy / Capability Gate
    -> Capture Port
    -> Immutable Evidence Store
    -> Normalizer / Upcaster
    -> Canonical Projection
    -> Application Services
    -> CLI / MCP / Report Adapters
```

### 3.1 Nexus 共通形

各 Nexus リポは、最初から同じ module 構造へ揃える必要はない。先に揃えるのは責務境界である。

```text
External Surface
    -> Adapter
    -> Raw Evidence
    -> Canonical Projection
    -> Application Use Case
    -> Human / CLI / MCP / Report Output
```

| 共通責務 | 目的 | リポごとの差し替え点 |
|---|---|---|
| Contract | 公開 API、CLI、出力、データ形式、禁止操作を固定する | package export、script interface、docs entrypoint |
| Adapter | Discord、GitHub、note、Obsidian、Google Cloud などの変更を隔離する | API version、auth、pagination、rate limit、DOM |
| Evidence | 実測ログ、raw snapshot、hash、件数、route 状態を残す | 保存先、retention、private/public 境界 |
| Projection | raw から人間・ツールが読む安定 view を作る | schema、upcaster、unknown field policy |
| Readiness Gate | local success と external unverified を分ける | pytest、smoke、secret scan、PR/auth check |
| Publication Stopline | push、PR、公開、送信を人間承認まで止める | repo visibility、PR creation、Discord send、note post |

この共通形は「全部を一つの framework に寄せる」ためではない。リポごとの自然な形を保ちながら、壊れやすい境界を同じ言葉で扱うための型である。

### 3.2 Discord Context Bridge 固有形

Discord REST、Gateway、visible DOM、clipboard、local file、private command は同じ `CapturePort` を実装する。
domain は URL、HTTP、DOM selector、bot token、Chrome API を知らない。

adapter が持つ変更点:

- `adapter_id`, `adapter_version`, `upstream_api_version`
- capability (`content`, `attachments`, `history`, `thread_root`)
- pagination / checkpoint
- rate-limit classification
- source 固有の fallback と drift detector

## 4. Immutable Evidence

取得した raw evidence は local-private append-only store に保存し、既存 event を書き換えない。
各 event は最低限、次を持つ。

- `event_id`, `stream_id`, `stream_sequence`
- `event_type`, `source`, `occurred_at`, `captured_at`
- `schema_version`, `adapter_version`, `upstream_api_version`
- `raw_hash`, `previous_event_hash`, `event_hash`
- `acquisition_context`, `capabilities_observed`

同一内容の再取得も observation として追記し、重複は projection 側で表現する。

Nexus 共通では、raw evidence を公開 docs や PR 本文に混ぜない。公開可能な output は、件数、hash、route 状態、redacted reason、next action に限定する。

## 5. Canonical Projection

DCB の core domain は次だけを扱う。

- `CaptureRequest`
- `CaptureResult`
- `Conversation`
- `Message`
- `Attachment`
- `CoverageProof`
- `BlockedReason`

Discord 固有 field は `source_extensions` に隔離する。reader は未知 field を無視または保持できる tolerant reader とする。

Nexus 共通では、projection を repo ごとの「人間が判断するための view」として扱う。例えば:

- `nexus_ai`: automation readiness report、post-publish queue result、repo routing report
- `note-publishing-suite`: publish readiness、PUBLIC_READY、secret scan summary
- `fractal-decision-ecosystem`: routing rule、ADR status、gate result
- `discord-context-bridge`: capture manifest、context passport、decision card

## 6. Application Service

CLI と MCP は同じ use case を呼び、引数変換と安全な出力整形だけを担当する。

- `capture_context`
- `reconcile_capture`
- `build_context_passport`
- `review_reply_draft`
- `build_closeout`

Path、clock、store、source adapter、policy は依存注入する。CLI/MCP から `core.py` の内部関数を直接組み合わせない。

Nexus 共通では、script、CLI、automation、MCP、human report が同じ判定関数を使うのを理想形にする。表示先だけでロジックが分岐している場合は、先に characterization test で契約を固定してから薄い adapter へ寄せる。

## 7. 完全取得 / 完了主張の証明

DCB では取得件数だけで `full` にしない。次がすべて成立した時だけ `full` とする。

1. requested scope の inventory が確定している。
2. 対象 channel/thread ごとに pagination cursor の終端を確認している。
3. thread root、対象範囲の全 message、attachment inventory を reconcile している。
4. unresolved reference と unattributed attachment が 0 件である。
5. source route の失敗が 0 件、または requested scope に不要と証明されている。
6. raw evidence、normalized projection、manifest の hash と件数が整合する。

状態は `full | partial | blocked | stale` とし、`partial` と `blocked` を混同しない。

Nexus 共通では `done` を一枚岩にしない。最低でも次を分ける。

- local implementation
- local verification
- branch / commit
- push / PR
- external state
- cleanup
- unrelated dirty state

これにより「テストは通ったが PR は未作成」「PR は merge 済みだが worktree cleanup が残る」「local は成功だが GitHub auth 未確認」のような状態を正直に表現できる。

## 8. Discord API 変更への耐性

### Pagination

- channel history は Snowflake cursor/checkpoint を adapter 内で管理する。
- page size や response 件数を終端根拠にしない。
- search endpoint は補助 discovery に限定し、完全取得の正本にしない。

### Rate Limit

- limit 値をハードコードせず response header と `retry_after` に従う。
- bucket queue、global gate、jitter 付き retry を transport adapter に閉じ込める。
- 401、403、429、5xx を別 reason code にし、無限 retry を禁止する。

### Capability

- `MESSAGE_CONTENT`、履歴権限、attachment access を起動時に preflight する。
- content が利用不能なら metadata-only へ縮退し、成功扱いにしない。
- API version は Discord adapter の設定に閉じ込め、domain に露出させない。

## 9. Schema 互換性

- JSON Schema は安定した `$id` と明示的 `schema_version` を持つ。
- optional field 追加は minor、意味変更・削除・identity変更は major。
- `vN -> current` upcaster を用意し、raw evidence は migration しない。
- current projection は任意の対応 raw version から再生成可能にする。
- golden fixture で旧version読取、upcast、再projection、未知field許容を検証する。

Nexus 共通では、schema を持たない Markdown や JSON 出力も「読み手が依存している field / heading / status name」を契約として扱う。変更する時は migration note または compatibility wrapper を用意する。

## 10. Read-only / Privacy 境界

- capture transport は取得 endpoint の allowlist とし、POST/PATCH/PUT/DELETE を構造的に拒否する。
- domain/application layer に send/delete/manage port を定義しない。
- bot 権限は `VIEW_CHANNEL` / `READ_MESSAGE_HISTORY` 等の必要最小限にする。
- capture はユーザー開始、対象限定、allowlist、retention明示を前提にする。
- raw本文、token、signed attachment URL、実ID、local path は通常ログ、stdout、public artifact に出さない。
- manifest と raw blob の hash 整合を検証し、保存先は OS ACL または同等の local-private boundary に置く。

Nexus 共通 stopline:

- public / release / debut / announce / broad share は現在会話の明示承認まで止める。
- repository visibility 変更は repo ごとの明示確認まで止める。
- Discord / note / X / GitHub comment / email などの外部送信は、対象、本文、操作を再提示して止める。
- GUI / Computer Use は現在会話の明示承認まで使わない。

## 11. Drift と運用

adapter drift は障害を隠して fallback するのではなく、次を manifest に残す。

- `selector_hit_rate`
- `required_field_fill_rate`
- `fallback_used`
- `previous_adapter_version`
- `needs_adapter_update`
- `first_seen_at`, `last_seen_at`

Discord changelog、API version、privileged intent、schema、adapter fingerprint を依存監視対象にする。自動追随ではなく、fixture追加 -> adapter version更新 -> compatibility gate の順で採用する。

Nexus 共通では、drift を次の分類で扱う。

| drift | 例 | 期待する扱い |
|---|---|---|
| upstream | Discord API、GitHub auth、note UI、Cloud API | adapter と capability gate で吸収 |
| local env | Windows encoding、PATH、Python version、missing CLI | readiness gate に分離 |
| repo ownership | SSOT clone、nested repo、GitHub account | repo routing gate で停止 |
| publication boundary | push、PR、visibility、external send | human review stopline で停止 |
| schema/output | JSON field、Markdown heading、status name | contract test と migration note |

## 12. テスト契約

### Contract tests

- 公開 export と signature
- CLI/MCP result parity
- metadata-only / redaction / outbound disabled
- old schema upcast と未知field許容
- adapter capability と reason code

### Capture tests

- cursor終端、重複page、途中再開、削除/追加競合
- header-driven rate limit、429、401、403、timeout
- `MESSAGE_CONTENT` unavailable の metadata-only 縮退
- partial-to-full reconcile と attachment帰属

### Performance tests

- unit test は外部process待ちをfixture化する。
- live/external boundary test は別markerへ分離する。
- fast gate <= 5秒、full gate <= 20秒を目標とし、超過理由をtest名単位で出す。

Nexus 共通の最初の一手は、実装分割ではなく contract test である。既存挙動を固定せずに共通 module へ寄せると、リポごとの暗黙契約を壊す。

## 13. Nexus 全リポへの適用順

全リポへ一括適用しない。次の順で、小さい contract PR / commit に分ける。

1. 対象 repo の SSOT、公開境界、現行 gate、dirty state を実測する。
2. 公開 API / CLI / docs output / external stopline の characterization test を追加する。
3. `local success` と `external unverified` を分ける readiness result を固定する。
4. 外部依存を adapter と capability gate に閉じ込める。
5. raw evidence と public-safe projection を分離する。
6. schema / heading / status name の versioning または compatibility wrapper を追加する。
7. fast gate、full gate、diff check、secret/path scan を通す。
8. 人間レビュー用に、未検証外部境界と rollback 単位を日本語でまとめる。

推奨 rollout unit:

| Wave | 対象 | 目的 |
|---|---|---|
| 0 | `discord-context-bridge` | 型の実証。contract -> split -> adapter -> gate の順で進める |
| 1 | `nexus_ai` local automation / readiness scripts | local/external 分離と出力契約を揃える |
| 2 | public package repos | PUBLIC_READY、secret scan、visibility stopline を揃える |
| 3 | FDE / routing repos | scope_route、fact tag、ADR/gate contract を揃える |
| 4 | 共同 repo clones | clone placement、identity guard、PR stopline を揃える |

## 14. 段階的移行順

1. 公開 API、安全境界、ledger byte/hash の characterization test を固定する。
2. 巨大 test を責務別に分割し、test数と挙動を維持する。
3. model / redaction / storage を抽出する。
4. source adapter、capture、schema upcaster、coverage proof を実装する。
5. context/review application service を抽出する。
6. CLI/MCP を薄い adapter にする。
7. ops checks を副作用のない合成可能な check にする。
8. full/release gate、rollback、SSOT projection を検証してから旧 facade の廃止時期を判断する。

各段階は旧 import と新 import の parity test が green の状態で閉じる。file move と behavior change を同じ commit に混ぜない。

## 15. 非目標

- Discord DOM/API の永久互換
- 自動送信、reaction、edit、delete
- raw Discord data の公開・外部共有
- Playwright を中核にした大型 browser automation framework
- 全 Nexus リポを同一 framework / directory layout に強制統一すること
- repo ごとの公開判断、push、PR、visibility 変更を自動化すること
- 現行 event store の一括書換え

## 16. 採用判定

本仕様の方向は既存 DCB の `surface -> adapter -> raw_snapshot -> manifest -> decision_card` と整合する。
追加すべき核心は、versioned canonical projection、schema upcaster、capability gate、cursor終端による完全性証明、GET allowlist の構造的 read-only enforcement である。

Nexus 全体へ展開する場合の最適解は、実装形ではなく判断形を共通化すること。
つまり、各 repo に同じ folder を作るのではなく、次の contract を先に固定する。

- 何を公開 API / public output とみなすか
- 何を raw evidence として local-private に置くか
- どの外部依存を adapter に閉じ込めるか
- どの gate が local / external / publication を分けるか
- どの変更単位なら rollback 可能か

この型であれば、Discord、note、GitHub、Obsidian、Cloud、FDE の変更に対して、同じ運用言語で耐えられる。
