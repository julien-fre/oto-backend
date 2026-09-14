# Email paying customers who have gone quiet

**When to use it**: against your paying accounts, to find the ones that have gone quiet and prepare each a short, specific note grounded in their own history — never a blast, and never sent without a person reading it first.

```
              Natural language input in Claude
              "Find our quiet paying accounts and draft a re-engagement note for each."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Define the target segment                  │   folk_record
│  A revenue floor, not churned, quiet past       │   data_rows
│  your threshold, and not touched recently.      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Pick the contact and gather context        │   folk_record
│  The most senior person on file, and            │
│  one real detail from their own notes.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Draft the email                            │
│  Short, warm, specific — a two-word subject     │
│  and a concrete ask, never an open one.         │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · Save as drafts for human review            ║   gmail_compose
║  The run never sends — a person reads,          ║
║  edits and sends each note from Gmail.          ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Log the run                                │   data_write
│  Stamp today's date on every account            │
│  drafted, so nobody's touched twice.            │
└─────────────────────────────────────────────────┘
```

## 1. Define the target segment
- <tool:folk_record> — filter paying accounts to your own revenue floor, not already churned, and quiet longer than your own threshold (e.g. no logged interaction in 3+ months). Combine all three filters in one company search rather than fetching everything and filtering client-side — a search without filters drains every page of the workspace, and this list gets re-run regularly, keep it cheap. Custom fields such as status or revenue sit under the group they belong to, so read one record first to learn their shape. Text filters match as a prefix, not a "contains": a zero count on a compound name is not proof the account doesn't exist.
- <tool:data_rows> — read your re-engagement log and drop every account stamped within your own **[cool-off window]**, so an account drafted last cycle doesn't come back as a fresh draft.

## 2. Pick the contact and gather context
- <tool:folk_record> — prefer the account's stated main point of contact; otherwise the most senior person on file with an email, someone the account has actually interacted with before over a name that's merely listed. Skip contacts from clearly unrelated categories, and skip anyone with no email rather than guessing one.
- Before drafting anything, pull what the CRM already knows: past notes, description and status fields, and recent interactions. Notes and interactions hang off the person or company record — fetch them by that record's id, not by a keyword — then scan them for the company and contact name plus a few topical terms relevant to your product, most recent first. An interaction search returns only a subject and snippet; open the one or two that matter to read the body, and treat a missing body as a privacy setting, not an empty conversation. Pull out exactly one concrete detail worth referencing — something they actually said or did. **If the notes are empty or too generic, fall back to a plain template rather than inventing a detail** — a fabricated one is worse than a generic email.

## 3. Draft the email
Short, warm, specific. Reference how long it's actually been since the last interaction and weave in the one real detail so it reads as "we noticed," not "we're checking a box." A two-word subject line reads as a real note, not a campaign. Offer a short call and ask for a couple of concrete times — a specific ask converts better than an open-ended "let's catch up."

## 4. Save as drafts for human review
- <tool:gmail_compose> — one draft per account, in the mailbox of whoever owns the relationship (pick it with the account parameter), with the recipient, the two-word subject and the note as the body. Leave the mode at its default so the call saves a draft; never pass the send mode — this is outbound to paying customers, and a person reads every note before it goes. CC whoever on the team should have visibility, so a reply doesn't land in one inbox nobody else can see.
- Read the `kind` field on every response and report what it says: "draft" means saved, not sent. A fresh draft can take a while to appear in a Gmail tab that is already open — tell the reviewer to reload or search the drafts folder rather than re-running the step, which would create a duplicate.
- Hand the reviewer the full list (account, contact, email, the detail used or "plain template") so they can work through the drafts in one sitting: edit, send, or delete.

## 5. Log the run
- <tool:data_write> — one row per account, written as a single batch with the CRM company id as the business key, so a re-run updates the same row instead of adding a second one. Record the segment fields that qualified it, the contact used, the detail referenced, and the outcome (drafted, or held back and why). Stamp today's actual date (fetched at run time, not hardcoded) on every account that got a draft. It is the draft date, not a send date — the run can't see what the reviewer did — and a draft the reviewer deletes still counts as touched for this cycle, because "not now" is a decision too. Leave the date blank for anyone held back before drafting.

## Output
Report: how many accounts matched the segment, how many were skipped by the cool-off window, how many drafts were saved (and in which mailbox), how many accounts were held back and why, and how many drafts used a real context detail vs. the plain fallback.