## prerequisite — authorize Google Chat on your Google account

From this card, click **connect**: Google asks you to authorize **Google Chat only** (scopes `chat.spaces.readonly` and `chat.messages`) on the account you choose. The Google account itself (address, token) is carried by the **Google Account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- multiple Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` tells you which ones have authorized Google Chat
- an account that has not authorized Google Chat is refused by the tools, naming this card — come back here to authorize it

## usage — spaces and messages

`chat_spaces` and `chat_message(op=list|read|post)` — under the chosen account.
- "list my Google Chat spaces"
- "summarize the thread in the `#ventes` space since Monday"
- "post this message in `#ventes`"
