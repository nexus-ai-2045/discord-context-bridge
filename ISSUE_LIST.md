# Discord context bridge issue list

## P0

- Discordで対象を見つけた後の bridge intake を主導線にする。
  - done when: 対象URLまたは可視snapshotを渡すだけで、保存、coverage、context passport、reply guide、review draft へ進む最短導線が分かる。
  - current: 個別コマンドはあるが、理想体験としての M1 導線はまだROADMAP上で次フェーズ。
- `references/initial-thread-ruleset.md` を MVP 正本として固定する。
  - done when: 入口指定から handoff までの13工程が読め、任意割り込みと stopline が分かる。
- 13工程の validation plan を ROADMAP に固定する。
  - done when: smoke、preflight、fixture E2E の役割が分かれ、MVP done 条件が読める。
- CLI / MCP / plugin / ChatGPT connector / 実機 capture を MVP 条件から外す。
  - done when: README / PROCESS_BOUNDARY / ROADMAP が任意 adapter / developer verification として説明し、MVP 必須経路にしない。
- Markdown review artifact skeleton を作る。
  - done when: 文脈理解、仮下書き、risk review、unknown、選択肢、copy block が1つの `.md` に保存され、人間が直接編集できる。
- 13工程 fixture E2E を作る。
  - done when: 合成 fixture だけで入口 metadata から handoff packet まで通り、Discord send / reaction / edit / delete が実行されない。

## P1

- 速度・精度・軽量化の指標を計測できるようにする。
  - done when: intake、report、context-passport、review、stage の実行時間、入力サイズ、parsed件数、cache利用、read-more理由がmetadata-onlyで確認できる。
  - target: intake 1秒以内、保存済みsnapshot文脈整理 3秒以内、送信前gate 1秒以内、full smoke 10秒以内、raw/private漏えい 0件、outbound誤許可 0件。
- 送信テスト運転表を実ログで閉じる。
  - done when: `stage-discord-send`、`verify-chrome-fill-dry-run`、`closeout-discord-send` のJSONログを `send-operation-status` に渡し、対象safe label、本文レビュー、dry-run、テスト用チャンネルでの人間送信、送信後closeout、失敗時回復方針、本番手順固定が1つのJSONで確認できる。
  - current: `send-operation-status` と `docs/discord-send-operation-runbook.md` は実装済み。実チャンネルでの人間送信ログは次フェーズ。
- release version 採番を運用チェックへ入れる。
  - done when: `scripts/bump_version.py --part patch|minor|major --write` で `pyproject.toml` と `CHANGELOG.md` を同時更新でき、`scripts/bump_version.py --check` が `ops_check.py` に含まれる。
- human gate と copy block を作る。
  - done when: `final_candidate` を作っても自動送信せず、`copy_block` と `human_decision_required=true` が残る。
- Context SSOT update と handoff packet を作る。
  - done when: safe metadata と判断結果だけを temp registry に保存し、current state / read scope / stopline / next action を返す。
- 文脈カードの最小 schema と fixture を作る。
  - fields: thread purpose, recent flow, participant roles, rules, drift, safe opening, NG tone, missing premise, one check before reply.
  - done when: raw 本文と実参加者名を出さず、safe label / role / 要約だけで `discord_context_card.v1` を返す。
- 返信前ゲート quick verdict を作る。
  - values: `go`, `wait`, `ask-context`, `risky`。
  - done when: 1-3 秒で短い日本語 verdict と一点確認だけを返し、send / reaction / delete を実行しない。
  - current: `review-draft` / `guide-reply` が `quick_verdict` と `one_check_before_reply` を返す。
- status dashboard command を作る。
  - output: `now`, `done`, `broken`, `blocked`, `next`, `github`, `residual`。
  - done when: raw 本文、参加者名、secret、local path を出さず、運用状態を日本語で返す。

## P2

- 運用 UI / ログ表示を整える。
  - logs: implementation, live probe, GitHub, residual, blocker, safety.
- conversation parser quality を実サンプルで上げる。
  - done when: 周辺会話から目的、前提、参加者ロール、温度感、ルール、返信前の一点確認が安定して返る。
- スレッド選択とルール紐付けを追加する。
  - first route: manual rule registration + thread key.
- Context Budget Gate と handoff template を追加する。
- `adapter_failed` を人間語 / JSON で分類する。
  - done when: `failure_stage`, `reason`, `source_ready`, `gate_verdict`, `text_output=omitted`, `outbound_actions=disabled` が出る。

## P3

- 送信自動化を検討する。
  - stopline: Discord send, auto reply, reaction, delete, external posting require explicit GO.
- 実機 probe を private adapter として検討する。
  - done when: public repo に credential、browser profile、raw 本文を入れず、失敗理由だけを返す。
- macOS OCR private command を repo 外 adapter で検討する。
  - done when: public package には stdout contract だけを渡す。
- `@discord` bot route preflight を検討する。
  - done when: token値、snowflake値、local path を出さずに token有無、dmPolicy、allow/group/pending件数だけを返す。

## Cross-cutting guards

- public repo は token, cookie, webhook, browser profile を扱わない。
- raw Discord 本文、参加者名、local username、local path、secret-like 文字列を visible output に出さない。
- Discord send、auto reply、reaction、delete、Git push、PR 公開、外部投稿は明示 GO まで禁止する。
- README / PR / docs / developer smoke output は日本語を既定にする。
- GitHub 作業前に auth / permission / PR state / merge state / branch cleanup permission を確認する。
