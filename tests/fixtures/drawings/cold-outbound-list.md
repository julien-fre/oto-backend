# Build an outreach list from the French company registry

**When to use it**: you want a cold outbound list from an official company registry — verified, tiered by real signal, and written up without sounding like a template.

```
              Natural language input in Claude
              "Build a cold outbound list of French accounting firms, 10-50 staff."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Build the universe                         │   fr_search
│  Pull every company matching sector,            │
│  headcount and age — filtered, not raw.         │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Verify the website before spending         │   serper_search
│  A registry or directory listing is             │
│  never the answer — find the real site.         │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Tier by the strongest signal               │   serper_search
│  An open role beats a known system beats        │
│  press beats nothing — nothing still writes.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Resolve email in bulk                      │   fullenrich_enrich_linkedin
│  Batches of 20 through a waterfall, polled      │
│  to completion — never a fixed wait.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Write one opener per row, then check it    │
│  One grounded fact per row, rotated across      │
│  frames — then a mechanical repetition count.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Load the campaign                          │
│  Route by signal strength into tracks,          │
│  verify it landed, then stop for review.        │
└─────────────────────────────────────────────────┘
```

## 1. Build the universe
- <tool:fr_search> — every company matching sector (NAF code), headcount band and age. Filter to active status and the right bands before anything else — a first pull too broad is fine, a filtered list is what the rest runs against.
- Write the full list to one running table. Don't re-pull per batch.

## 2. Verify the website before spending a credit
- <tool:serper_search> — one search per company: name + city + "official site." A registry listing, review site or map pin is never the answer; the real site is often under a different brand name than the legal one.
- Treat this as qualification, not enrichment: no verified first-party website, no email credit spent. Expect roughly one in four rows to fail this gate (wrong sector, dissolved, a subsidiary with no local buyer) — that's not a bad query, that's the registry.

## 3. Tier by the strongest signal you can find
In order of strength: a relevant open role (back-office, not sales) → a known back-office system → recent relevant press (acquisition, new location, generational handover — not a routine mention) → nothing, which still gets an opener written from the company's own site. Record which signal each row got, and the evidence itself — the next step writes from this, never from a guess.

## 4. Resolve email in bulk
- <tool:fullenrich_enrich_linkedin> — submit in batches of 20 (larger batches get rejected outright); poll until each batch finishes rather than assuming a fixed wait.
- A miss on email that still returns a verified profile match routes to a different channel, not the trash. An email on an unrelated or foreign-HQ domain is the wrong buyer — drop that one for real.

## 5. Write one opener per row, then check the batch for a pattern
One sentence, two short ones at most, under 35 words, no em dash — a specific fact a stranger couldn't have guessed, turned toward the actual daily pain. Rotate across several structural frames (a density number, the company's own history, an event addressed directly, a plain two-sentence long-tail, a question built from their own site) so a hundred true lines don't read as one template.

Then count, don't eyeball, across the whole batch: no more than 30% on the same structural frame, no more than 20% on a colon pivot or the same closing phrase, no group over 5% sharing an opening two words, zero em dashes or over-length lines. A batch that hasn't passed gets rewritten and re-checked before it moves on.

## 6. Load the campaign, ready for a person to launch
Route rows with a real trigger (role, system, press) into your highest-touch track, everything else into standard. Read back what you wrote and confirm the opener field actually changed per row — a silent write failure ships a generic line where a personal one should be, with no visible symptom. Stage everything, then stop. Never call a send or launch action yourself.

## Output
Report: how many companies pulled, how many passed the website gate, how many landed in each signal tier, how many resolved to a usable email or fallback channel, how many openers passed the mechanical check on the first pass, and the campaign staged for review.