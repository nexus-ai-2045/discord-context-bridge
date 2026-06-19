# Discord context bridge issue list

## P0

- `adapter_failed` を人間語 / JSON で分類する。
  - done when: `failure_stage`, `reason`, `source_ready`, `gate_verdict`, `text_output=omitted`, `outbound_actions=disabled` が出る。
- 実機 probe が raw 本文を出さずに成功 / 失敗理由を返す。
  - done when: `scripts/private_adapter_probe.py` と `scripts/live_ops_smoke.py` が fixture / private adapter test を通る。
- macOS OCR private command を repo 内 runner で再現可能にする。
  - done when: `scripts/read_screenshot_ocr_text.py --capture-profile macos-screencapture-region` を `DISCORD_CONTEXT_BRIDGE_PRIVATE_COMMAND` に差し込み、`live_ops_smoke.py` が `text_output=omitted` のまま pass する。

## P1

- 文脈カードの最小 schema と fixture を作る。
  - fields: thread purpose, recent flow, participant roles, rules, drift, safe opening, NG tone, missing premise, one check before reply.
  - done when: raw 本文と実参加者名を出さず、safe label / role / 要約だけで `discord_context_card.v1` を返す。
- 返信前ゲート quick verdict を作る。
  - values: `go`, `wait`, `ask-context`, `risky`。
  - done when: 1-3 秒で短い日本語 verdict と一点確認だけを返し、send / reaction / delete を実行しない。
- status dashboard command を作る。
  - output: `now`, `done`, `broken`, `blocked`, `next`, `github`, `residual`。
  - done when: raw 本文、参加者名、secret、local path を出さず、運用状態を日本語で返す。

## P2

- 運用 UI / ログ表示を整える。
  - logs: implementation, live probe, GitHub, residual, blocker, safety.
- スレッド選択とルール紐付けを追加する。
  - first route: manual rule registration + thread key.
- Context Budget Gate と handoff template を追加する。

## P3

- 送信自動化を検討する。
  - stopline: Discord send, auto reply, reaction, delete, external posting require explicit GO.

## Cross-cutting guards

- public repo は token, cookie, webhook, browser profile を扱わない。
- raw Discord 本文、参加者名、local username、local path、secret-like 文字列を visible output に出さない。
- Discord send、auto reply、reaction、delete、Git push、PR 公開、外部投稿は明示 GO まで禁止する。
- README / PR / docs / CLI human output は日本語を既定にする。
- GitHub 作業前に auth / permission / PR state / merge state / branch cleanup permission を確認する。
