## prerequisite — authorize Gmail on your Google account

From this card, click **connect**: Google asks you to authorize **Gmail only** (scope `gmail.modify`) on the account you choose. The Google account itself (address, token) is carried by the **Google Account** connector — one account can authorize several services, one service at a time, without re-authorizing the others.
- multiple Google accounts: each tool acts on the one the call names with `_account=<email>` (the tools' `account=<email>` is the same choice — two different addresses are refused), otherwise the one the project pins, otherwise the default; an unknown address is refused, never replaced by another, and the response names the account that served (`_account`); `google_accounts` tells you which ones have authorized Gmail
- an account that has not authorized Gmail is refused by the tools, naming this card — come back here to authorize it

## usage — search, read, draft, send

`gmail_message(op=search|get|attachment|drafts|archive|trash)` and `gmail_compose` — under the chosen account.
- "find this week's unread emails and archive the newsletters"
- "draft a reply to this email" or "send it"
- `gmail_compose` appends the sending account's **Gmail signature** (after `--`), like the web client — the Gmail API never does it on its own. `sign=False` composes without it; the response reports `signature`: `appended`, `none_configured` or `disabled`
- "read the `.xlsx` spreadsheet attached to this email, `devis` sheet" — an attachment (`op=attachment`) comes back as CSV per sheet, truncated

## note — project scope (#605)

A `{kind: "url"}` attachment in `gmail_compose` is read server-side: under a project with `excluded_url_prefixes`, a matching url is refused, naming the pattern (`file_source` seam). Details: `docs/projects.md`.
