# Security Policy

Discord Context Bridge は local-first かつ read-only が既定です。

## Supported Boundary

- ユーザーにすでに見えているテキストだけを受け取ります。
- 小さな local event snippet を append-only file に保存します。
- Discord credential を要求しません。
- Discord message を送信しません。

## Sensitive Data

次のものを commit しません。

- Discord user token、bot token、webhook URL。
- Discord snowflake らしき ID、HTTP 認証 header 形式の credential。
- Browser cookie、profile、session store、screenshot。
- 実 guild ID、channel ID、user ID、handle、local absolute path、private message 本文。
- private conversation を含む local event store。

## Reporting

sensitive data が release candidate に見つかった場合は、公開を止めます。
必要なら credential を rotate し、release branch から該当データを取り除き、
public release checklist を再実行します。
