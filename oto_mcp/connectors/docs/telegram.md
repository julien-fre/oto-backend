## prerequisite — connect your Telegram account

your Telegram account is connected through a hosted flow (login by number + code).
- the subscription key lives on the **Unipile account** connector (your BYO key, or the platform's with the **hosted messaging** option granted by an admin);
- then "Connect my Telegram account" here. no cookie to paste, no extension.

## usage — read and reply from your account

`telegram_chat(op=list|read|send)` — you act as yourself, under your own account.
- "read my latest Telegram conversations"
- "reply to [contact] on Telegram"
- "summarize this Telegram thread"

## note — no inboxes, a flat list

Telegram does not sort its threads into inboxes: the conversation list is flat, unlike LinkedIn.
