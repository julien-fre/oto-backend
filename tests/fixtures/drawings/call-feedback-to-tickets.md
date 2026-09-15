# Turn customer call feedback into Linear tickets

**When to use it**: a customer describes a bug or a missing feature on a call, the person on the call makes a mental note, and the note doesn't survive the afternoon. The write-up exists and is searchable, but nobody re-reads it — so the same issue gets reported again months later and is treated as new.

```
              Scheduled nightly
              "Turn yesterday's calls into tickets and doc gaps."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Pull the day's calls                       │   granola_content
│  Every call recorded yesterday, with its        │   folk_record
│  account and its write-up.                      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Extract only what is actionable            │
│  A bug with a symptom, a request with a         │
│  need, or a documentation gap.                  │
└────────────────────────┬────────────────────────┘
                         ▼  an item a reader could act on
╔═════════════════════════════════════════════════╗
║  3 · Is it already built?                       ║
║  What the team answered on the call             ║
║  outranks what the knowledge base says.         ║
╚════════════════════════╤════════════════════════╝
                         ▼  genuinely missing
╔═════════════════════════════════════════════════╗
║  4 · Hold the bar before creating               ║
║  Would a person reading only this item          ║
║  know what to do next?                          ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · File it                                    │   linear_issue
│  Search the tracker first. Comment and          │
│  link the account, or create and link.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Tell the team                              │──▶  Slack thread  the tracker action, per item
│  One message per call as it happens,            │──▶  Friday digest  the week, by corroboration
│  one digest closing the week.                   │
└─────────────────────────────────────────────────┘
```

## 1. Pull the day's calls
- <tool:granola_content> — window on when the call happened, not when the note was last edited.
- <tool:folk_record> — if your CRM also stores call notes (e.g. via a separate transcription tool), **deduplicate across both on a shared reference** (a call URL cited inside the CRM note) rather than treating them as two calls. Skip internal-only calls; most of what makes a good ticket comes from a customer saying it.

## 2. Extract only what is actionable
Four categories, everything else discarded:
- **A bug** — a described symptom, not just dissatisfaction.
- **A feature request** — a stated need, not a hypothetical.
- **An integration request** — a named third-party tool. The answer is usually "check the connector catalogue" before it's "file a feature request".
- **A documentation gap** — they couldn't find out how to do something that already works.

Praise, pricing talk and scheduling aren't tickets. **Keep the customer's own words as a quote, with the call date.** The quote is the payload — "it says the connector is active but the run comes back empty, so I check by hand before every launch" tells an engineer the symptom, the consequence and the workaround; "user reports connector state is unreliable" tells them nothing. Log every item, filed or not, before any gate runs — an item that never becomes a ticket is still evidence.

## 3. Is it already built?
Two checks, and the first is worth more than the second.

**What your team answered on the call.** If someone said "that already exists" or "that shipped last week", the item ends there — nothing goes to the tracker. If they said "it's on the roadmap" or "we're building it", that does **not** end the item — a roadmap is a promise, not a shipped feature, and this whole system exists partly to count how many accounts are waiting on that promise. This is the distinction most likely to get flipped, because both answers sound reassuring on the call.

**What your knowledge base already records.** Search it before assuming absence means the feature doesn't exist — a search returning nothing is not proof of absence if your knowledge base is thin or your indexing is lexical rather than semantic. **Finding the capability closes the item; failing to find it closes nothing** — carry the item forward rather than closing it on a search that came back empty.

## 4. Hold the bar
One filter before creating anything: **would a person reading only this item know what to do next?** If not, hold it back with a reason, and let it surface in the weekly digest as an unclear signal rather than as a ticket. A low bar here is what turns a tracker into a place nobody trusts — better to hold a few back than bury real issues under speculative ones.

## 5. File it
- <tool:linear_issue> — search on substance, not wording: a customer's plain-language description and an existing issue's technical title are often the same request under different words. Search two or three phrasings.
- **Issue exists**: comment on it with the account, quote and call date. Attach the account so recurring corroboration is visible, and increment a corroboration count.
- **Issue doesn't exist**: create it with a title describing the symptom, not a guessed cause, plus the quote, account and call date. Set severity from what was actually said — a workaround the customer now performs daily is high; a passing mention isn't. Leave triage priority for a human.

## 6. Tell the team
**Per call, as it happens** — one message per call that produced at least one action, not one per item, so a call raising three things reads as one event. Name the account, the ask, and its value to the business if you track that. The tracker action (created/commented) goes in a thread reply underneath, kept separate from the business-facing message.

**Once a week** — a digest grouped by what's new, what gained corroboration (the same issue reported by multiple accounts is a priority signal a single mention isn't), what was closed as already-built, documentation gaps, and anything held back as unclear. Post even on a quiet week — a one-line "nothing this week" is what tells the difference between a quiet week and a broken run.

## What this never does
- Never sets ticket priority, never moves an issue out of a backlog state, never closes anything — a person reads the digest and decides. This process files, links and counts; it doesn't argue that something should be built.
- Never answers the customer. If an item came from a call where someone is waiting on a reply, that's a separate, human-owned action.
- Never closes an item on an absence alone — only on a positive find (a founder's sentence, a named page, a connector that exists).