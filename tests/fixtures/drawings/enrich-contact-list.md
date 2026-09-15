# Find emails and phone numbers for a contact list

**When to use it**: you have a batch of 1-250 partial contacts (email, LinkedIn identifier, or name + company) and want them enriched with a real email and phone — cheaply, with every genuine gap flagged instead of silently dropped.

```
              Natural language input in Claude
              "Enrich this list of 80 contacts with email and phone."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Assemble the batch                         │
│  One identifying combination per contact,       │
│  each tagged with your own reference id.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Submit and poll                            │   dropcontact_enrich
│  One batch call, not one per contact —          │   dropcontact_result
│  the result comes back async.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Backfill missing emails                    │   kaspr_enrich_linkedin
│  Only the real misses, email only, through      │
│  the second provider on the profile URL.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Present the results, flagging gaps         │
│  One clean table — every unresolved contact     │
│  flagged, never silently dropped.               │
└─────────────────────────────────────────────────┘
```

## 1. Assemble the batch
Each contact needs at least one identifying combination: an email, a LinkedIn URL/slug, or a first name + last name + company. Attach your own reference id to every row, inside the freeform custom fields the provider echoes back unchanged — a batch call doesn't guarantee it returns rows in the order you sent them. Keep the LinkedIn identifier on hand for any contact that might need the fallback later. An under-specified contact doesn't fail the whole batch, only its own row.

## 2. Submit and poll for results
- <tool:dropcontact_enrich> — the whole batch in one call (up to 250 contacts), split into chunks only if you're over that limit. It returns a request id immediately; the enrichment runs async. Set the processing language explicitly if your contacts aren't French — the provider defaults to it.
- <tool:dropcontact_result> — poll starting around 30 seconds after submission, then every 20-30 seconds until done.
- Treat email quality labels literally: a personal verified email on the company's own domain is the best outcome, a generic mailbox is usable but weaker, anything marked invalid counts as no email at all.

## 3. Backfill missing emails through the fallback
- <tool:kaspr_enrich_linkedin> — for every contact still without a usable email that has a LinkedIn identifier, email only; don't spend credits pulling phone here. Ask for the work and direct email fields by name and leave phone off — the call's default field list includes phone. If it finds an email, mark its source as the fallback provider. If it finds nothing, or there's no LinkedIn identifier to fall back on, leave it blank and flag it in the final step — never drop the row.

## 4. Present the results, flagging gaps
One clean table: name, best-qualified email with its source, phone/mobile if found, and any company data the enrichment returned. Flag every contact where neither provider found a usable email — a flagged gap tells a human exactly who still needs manual work. This is a standalone step: it doesn't write anything back to a CRM unless that's asked for separately.

## Output
Report: total contacts submitted, how many resolved on the first provider, how many needed the fallback and how many of those resolved, and the final list still missing an email.