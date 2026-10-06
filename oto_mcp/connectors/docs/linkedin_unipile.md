## prerequisite — connect your LinkedIn account

connect YOUR LinkedIn account from the oto dashboard — no cookie to paste, no extension. the session runs on a real hosted browser (residential proxy), which avoids fingerprint blocks.
- the subscription key lives on the **Unipile account** connector (your BYO key, or the platform's with the **hosted messaging** option granted by an admin);
- then "Connect my LinkedIn account" here.

## usage — LinkedIn prospecting and messaging

search, profiles, posts, network, job offers and messaging — you act as yourself, under your own account.
- "search LinkedIn for CFOs in the Lyon area in my 1st-degree network"
- "open this slug's LinkedIn profile and summarize their career"
- "send an invitation to this prospect with a note" then "reply in the thread when they accept"
- "show my recent LinkedIn home feed" or "comment on this post"

## note — it's YOUR session, not a database

results come from what YOUR account sees (network, subscriptions, premium products) and are subject to LinkedIn's limits — not from a purchased file. for an email or a mobile number a profile doesn't publish, use an enrichment connector (dropcontact, fullenrich, kaspr, lusha).

## note — Recruiter and Sales Navigator are activated at connection

a premium product (`recruiter` or `sales_navigator`, **mutually exclusive** — only one per account) is attached **at connection time**. without it, premium endpoints answer 403 "out of your scope". on an already-connected account, it is a **reconnection** that attaches it, not a second connection.

## note — the feed is served in a triage view

`linkedin_unipile_post(op="feed")` reads your home feed live, one page per call (the next one via the returned `cursor`), storing nothing. it returns an excerpt by default (text cut at 600 characters, triage columns only): a full page exceeded the cap on an MCP result. `fields=["*"]` and `text_max_chars=None` return the raw data; the response always says what it trimmed.
