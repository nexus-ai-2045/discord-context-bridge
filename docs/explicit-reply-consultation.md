# Botなしの明示返信相談

既存の承認済み内部ブラウザで必要範囲を取得し、DCBへ保存してから、現在のAIが対人skillとLCS正本を読んで本人への短い案を返す。常駐UI、LLMの自動起動、投稿、自動送信は含まない。

## ローカル入口

先に既存のingress・route判定と可視取得の承認を済ませる。このコマンドはブラウザを開かず、取得済みfileだけを扱う。通常対象照会は `target-cache-lookup`、全保存先の発見・棚卸しは `cache-inventory` を使う。

```sh
discord-context-bridge prepare-reply-consultation \
  --url 'https://discord.com/channels/1/10' \
  --capture-file /private/capture.json \
  --snapshot-store /private/dcb/text-snapshots.ndjson \
  --goal '相手の意図を確かめる返信を相談したい' \
  --draft-file /private/unsent-draft.txt \
  --skill-file /approved/skills/interpersonal-communication/SKILL.md \
  --output /private/dcb/consultation-new.json --json
```

上記は例示パス。既存private保存先を指定する。stdoutはmetadataのみ、相談packetはsnapshotと同じdirectoryの新規fileへmode `0600`で作り、既存fileを上書きしない。未送信draftと目的はその相談packetだけに保存し、投稿済み本文や本人が採用した返信スタイルとは扱わない。

`capture.json`は既存site adapterのstructured messages形式を使う。

```json
{
  "source_url": "https://discord.com/channels/1/10",
  "captured_at": "2026-10-18T00:00:00+00:00",
  "history_exhausted": true,
  "messages": [
    {"id": "100", "sequence": 0, "visible_timestamp": "2026-10-18T00:00:00+00:00", "author_label": "A", "body_text": "相談してもいいですか", "is_thread_root": true},
    {"id": "101", "sequence": 1, "visible_timestamp": "2026-10-18T00:01:00+00:00", "author_label": "B", "body_text": "どの点ですか", "is_reply_target": true}
  ]
}
```

日時・対象・関係情報は実観測に置き換える。各messageには一意の数値文字列 `id` と、一意の非負整数 `sequence` が必要。3-ID以上の曖昧なURLは、観測で解決した `target_kind: message` と返信対象IDを照合する。`target_kind: thread` は `thread_id` と各messageの `channel_id` を照合する。明示されたguild/channel情報の矛盾も拒否する。未取得の返信参照と未読添付はgateで停止する。取得時刻は保存時刻と別にcanonicalへ保持し、consumerも元の鮮度期限を確認する。短threadの `history_exhausted` は履歴終端の観測を表すcallerの申告で、DOMをこのコマンドが独立検証した証拠ではない。通常は返信対象より前10件を取得し、引用・添付などに未解決参照があれば `unresolved_reference: true` を付ける。自動的に全文capture済みとはしない。単語「攻撃」等で人物や文面の危険度を決めない。

## AI hostへの接続

`ready_for_ai_consultation` の時だけ、現在のAIがprivate consumerを使う。

```python
from pathlib import Path
from discord_context_bridge import load_reply_consultation

consultation = load_reply_consultation(Path('/private/dcb/consultation-new.json'))
# saved_contextはこの呼出元のprivate文脈だけで扱い、stdoutや公開ログへ出さない。
# packetのassistant_taskに従い、skillとlcs_referencesの正本を読み、
# 本人に短い判断・必要なら返信案を返す。使ったLCS安定IDを区別する。
```

consumerは正規台帳のchain・失効、receipt、保存本文hash、保存された関係情報からのgate、鮮度、参照先の現在hashを再検証する。packet準備後に参照先が変わった場合は再準備する。別の投稿が追記されても、相談対象を新しい本文へ切り替えない。原文はcanonicalに残し、LCS本文はpacketへコピーしない。

`--lcs-root`か`LCS_REPO_ROOT`の指定先を使う。既定の場所は持たない。未指定・空指定・文書不在ではLCS未参照として停止する。skillは明示したfileを読み、LCS参照口を持たない旧home版なら停止する。skill参照先を指定してもhome配布は行われない。

`ready_for_ai_consultation` は助言生成完了ではない。hostによる本文理解と短い提案、本人の採否、実利用の確認は別段階。Bot、provider API、拡張、常駐processは追加していない。`guide-reply`は既存の局所ルールで、LCSを参照したAI相談の代替にはしない。`watch-guide`は構造化関係情報を受け取れず、文脈gateで停止する。

## 検証範囲

fixtureで保存・文脈不足・対象切替・login redirect・LCS不在・引用・保存失敗・台帳改変と参照先変更を試す。fixture成功は実ブラウザ取得、home反映、実対人相談の品質や効果を示さない。自動発動は後続の本人判断とする。
