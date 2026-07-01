# Codex Discord Ingress

この入口は、ねくが最初にスレッドURLやDiscord本文を渡さない前提で使う。

## 開始

ねくが「Discordチェックしたい」と言ったら、コデたんはこの流れを開始する。
`https://discord.com/channels/...` の Discord link だけが貼られた場合も同じ入口として扱い、
汎用URL調査ではなく read-only の `discord-context-bridge` ingress へ route する。

1. コデたんが Chrome で Discord を開く。
2. ねくが Chrome 上で目的の場所へ移動する。
3. コデたんが現在の Chrome 状態を観測する。
4. コデたんが「ここで開始してよさそう？」だけ確認する。
5. ねくがOKしたら、Discord Context Bridge の13工程へ接続する。

## Draft Before Understanding Gate

返信下書きは、スレッド理解が人間に確認されるまで作らない。

まず出すのは draft ではなく、理解サマリです。

1. 観測範囲を確認する。
2. 既存SSOT、サーバールール、チャンネルルール、スレッドルール、ピン留めを確認する。
3. スレッド内容を3〜8個の要点でまとめる。
4. 未確認点を出す。
5. ねくに「この理解で合ってる？」と確認する。

`understanding-ok` になるまで、reply draft、final candidate、copy block は作らない。

この判断は [ADR 0001](adr/0001-understanding-before-draft.md) を正本にする。

実地テストで見つかった補強項目は
[Live Test Reinforcements](live-test-reinforcements.md) に残す。

## しないこと

- ねくにDiscord本文コピーを要求しない。
- 最初にスレッドURLを要求しない。
- Discordアプリ操作を前提にしない。
- Discordへ送信、reaction、edit、deleteしない。
- MCP、plugin、ChatGPT connectorを必須にしない。

## Codex側の責務

Chrome 操作は Codex の browser / chrome skill 側で行う。bridge は Chrome を直接操作する
実装を持たず、Codex が観測した現在タブ状態を safe metadata として受け取る。

出力してよいもの:

- Discordを開けたか
- 人間の移動待ちか
- 現在タブがDiscordらしいか
- 13工程へ接続してよいか
- 次に人間が見るべき短い action

出力しないもの:

- raw URL
- raw title
- raw Discord本文
- 参加者名
- local path
- token、cookie、webhook

## Smoke

Codex/Chrome ingress の契約だけを確認する。

```bash
python3 scripts/codex_discord_ingress_smoke.py \
  --chrome-opened \
  --current-url "https://discord.com/channels/@me/111/222" \
  --human-state ready \
  --confirm-decision approved \
  --json
```

これは実際のChromeを操作しない。Codex側のChrome観測結果を受け取った時に、
bridgeへ接続できる状態かどうかだけを本文なしで判定する。

13工程へつなぐ時は、Codex 側で確認済みの safe metadata を `fixture_13_step_e2e.py`
へ渡せる。

```bash
python3 scripts/fixture_13_step_e2e.py \
  --entry-metadata tests/fixtures/codex_ingress_ready.json \
  --json
```

## 13工程への接続条件

- ChromeでDiscordが開いている。
- ねくがChrome上で目的の場所へ移動済み。
- コデたんが現在状態を観測済み。
- ねくが「ここで開始してOK」と確認済み。
- スレッド内容とルール照合の理解確認が済むまでは draft を作らない。
- outbound actions は disabled のまま。
