## prerequisite — authorize Google Chat on your Google account

From this card, click **connect**: Google asks you to authorize **Google Chat only** (scopes `chat.spaces.readonly` and `chat.messages`) on the account you choose. The Google account itself (address, token) is carried by the **Google Account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- multiple Google accounts: each tool acts on the default account, or on the one you target with `account=<email>`; `google_accounts` tells you which ones have authorized Google Chat
- an account that has not authorized Google Chat is refused by the tools, naming this card — come back here to authorize it

## usage — spaces and messages

`chat_spaces` and `chat_message(op=list|read|post)` — under the chosen account.
- "list my Google Chat spaces"
- "summarize the thread in the `#ventes` space since Monday"
- "post this message in `#ventes`"
