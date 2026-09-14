# Flag competitor battlecards that are out of date

**When to use it**: reps repeat battlecards to buyers, so a card that quietly went out of date does more harm than having no card. Run this weekly over the competitors you track: it re-reads their own pages, names the cards that went stale and exactly what moved, and drafts the refresh without touching the published cards.

```
              Scheduled routine, weekly on Monday
              "Which battlecards have gone stale this week, and who is new?"
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  0 · Read the card set                          ║   data_rows on the card table
║  Every tracked competitor with its claims, and  ║   oldest-verified first
║  the URL and date behind each.                  ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no cards          the card table is empty
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Re-read each competitor in public          │   serper_scrape, three pages
│  Pricing, product and home pages as served      │   serper_search, news
│  today, plus one news search.                   │   the served page, never a snippet
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Compare against what the card claims       ║   a rewording is not a change
║  Field by field, and only against the source    ║
║  URL the card itself cited.                     ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ unchanged         the card still matches its sources
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Rank what actually changed                 ║   material, notable or cosmetic
║  A price, plan or module move outranks new      ║
║  adjectives on a homepage.                      ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ nothing material  notable is reported, never drafted
                         ▼  something material moved
┌─────────────────────────────────────────────────┐
│  4 · Draft the refresh in the card's shape      │   the card's structure is the contract
│  Only the sections the evidence moved, each     │
│  line with its quote and URL.                   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Look for who is new                        │   hubspot_object, lost deals
│  Competitor names recurring in lost deals and   │   serper_search, category queries
│  searches that no card covers.                  │   a mention is a candidate, not a rival
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Stage every change for a person            │──▶  card table  evidence and a drafted refresh per card
│  Nothing reaches the published cards until      │──▶  candidates table  one row per recurring new name
│  somebody has read it.                          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Report what went stale                     │   slack_post_message
│  One message naming stale cards and new names,  │
│  the detail in its thread.                      │
└─────────────────────────────────────────────────┘

▪ terminal — nothing more is drafted there; a checked card is still stamped and counted
```

Steps 1 to 4 run once per card, oldest-verified first. Steps 5 to 7 run once per run, whatever happened to the cards.

## Rule zero: a quoted source or nothing
- **Every claim carries the URL it came from and the date it was read.** A battlecard line without a source is not written. Without the recorded URL, next week's comparison in step 2 has nothing to check against.
- **The competitor's own words, quoted.** Prices, plan names, feature claims and positioning come from the competitor's own pages. A review site, an analyst summary or a third-party comparison page is context, labeled as such where it is used at all, and never a card claim.
- **Nothing is published.** The drafted refresh waits in a table for a person, because whatever lands on a card ends up said to a buyer.

## 0. Read the card set
- <tool:data_rows> — the card table, with `layers="nested"` so each cell comes back with its layers. One row per competitor: its name, domain, and one column per field (plans and pricing, modules, positioning, integrations, proof, company). **Each cell's value is the claim as the card states it, its `link` layer is the source URL, and its `comment` layer is the date it was last verified.** Keeping the evidence in the cell itself means a claim and its source cannot drift apart. The row also carries `status`, `last_checked`, `changes`, `draft_sections`, a link to the card document people maintain, and the review columns that belong to a person, including the one that says whether a drafted refresh is `awaiting_review`, `applied` or `declined`.
- Sort by `last_checked` ascending, so a run cut short has already done the cards most likely to be wrong.
- The card document defines the sections, and the table holds the evidence. A competitor with no row is not watched, which is exactly the gap step 5 exists to catch.
- **Cards that were never sourced** come back mostly `unverifiable` on the first run, and that is expected. Where a page read in step 1 quotes an unsourced claim as the card states it, step 6 records that URL and date beside the claim; the rest are listed for a person to source. Change detection only starts working field by field once claims carry sources.

## 1. Re-read each competitor in public
Four reads per competitor, plus one free raw-HTML re-read of a pricing page that came back without numbers, and no more, because this runs weekly across the whole set.
- <tool:serper_scrape> — the pricing page, the product page and the homepage: **the URLs the card cited**, never URLs rebuilt from the company name. A scrape waits 15 seconds by default (`timeout_s`), and a page that does not exist times out exactly like a slow one, so a guessed URL costs a timeout and proves nothing. Do not retry the same URL in the same run.
- **Scrape the page, do not trust the snippet.** A search result's description is often a meta tag written years ago. A card refreshed from a snippet has been refreshed from the competitor's oldest copy.
- **A client-rendered pricing page serves no prices.** It answers 200 with a near-empty body and no error, so a short body does not prove an empty page. When the markdown carries no numbers, re-read with `format="html"` (the raw HTML, fetched without the scraper and without a credit) to see whether the prices are in the served markup or arrive by JavaScript. If neither shows them, the pricing field is `unreachable`. **Never read it as "they removed their prices."**
- **"Contact us" where prices used to be is a finding**, not an absence: a vendor who published prices last quarter and now does not has made a packaging decision.
- **A page that now serves something else is information.** A product URL that lands on a different module, or on a generic page, usually means the module was folded, renamed or dropped. Record the cited URL and what it serves now, never the destination alone.
- <tool:serper_search> — `kind="news"`, the competitor's name plus **[your category term]**, `tbs="qdr:m"`. This catches what the site does not say: a funding round, an acquisition, a new market, a certification. **Set `country` and `language` to your market**: the tool applies its own defaults, and a news search in the wrong locale comes back nearly empty and looks like a quiet month. Keep the payload small with `fields=["title","link","snippet","date"]`.
- For every page, keep the URL, the fetch date and the text you will compare.

## 2. Compare against what the card claims
Field by field, and only against the source the card cited for that field.

**The baseline is the claim cell's value.** The one exception is a field with a change still staged on a card whose review column says `applied`: the card document already carries that new text, so the staged text is the baseline, and step 6 moves it into the cell.

| Field | What counts as movement |
|---|---|
| Plans and pricing | A price, a plan name, a bundle boundary, or prices disappearing behind a form |
| Modules | A capability appearing on the product page, or one that is gone |
| Positioning | The sentence they lead with, or the segment they name |
| Integrations | A named integration added or withdrawn, where the card lists them |
| Proof | A named customer, a certification, an analyst placement |
| Company | Funding, an acquisition, a market or a language added |

Every field ends as one of four states:
- `unchanged`.
- `changed`, with **both** the old and the new text quoted. The old text is what reps have been telling buyers, and the person applying the refresh needs to see it next to the new one. A change an earlier run already staged, with the same new text, is not new: the card stays stale and is reported as awaiting review, without a second draft. If that refresh was marked `declined`, the same text is not reported again, and only different new text counts.
- `unverifiable`, when the card's claim has no source URL. It is reported as a card to backfill, never as a change. **Checking a claim against a page it was never taken from invents changes that did not happen.**
- `unreachable`, when the page would not answer or served no content for that field.

A rewording is not a change. Only movement that would change what a rep says counts.

## 3. Rank what actually changed
- **Material**: a rep would say something different because of it, such as a price, a plan, a module, the segment they claim, or a certification.
- **Notable**: worth knowing, but it does not change the pitch, such as a funding round, a new office, or a rebrand of an existing module. Reported in the message and recorded in `changes`, with no drafted sections, because the card's claims still hold.
- **Cosmetic**: new words for the same claims. Recorded, never reported. A card with nothing material, only notable or cosmetic movement, stops here.

A card with at least one material change is **stale**: only a stale card gets a drafted refresh, and the report leads with it. Without the ranking every copy edit becomes an alert, and people stop reading the channel.
- Count consecutive runs in which a card's pricing came back `unreachable`. At **[three runs]**, report it once as a notable finding ("pricing may have moved behind a form") instead of as a failure every Monday.

## 4. Draft the refresh in the card's shape
- Rewrite **only the sections the evidence moved**, in the structure the card already uses, and leave everything else untouched. Someone maintains that card by hand, and a fully regenerated card would wipe their edits and bury the lines that actually changed.
- Every rewritten line carries its quote, its URL and the date it was read.
- Where a change affects how you answer the competitor, add one line under the card's objection or counter section, grounded in **[your own product documentation]** and **[the claims you are cleared to make]**. **Never write a counter you cannot support**: an overstated counter gets challenged by the buyer on the same call where the rep uses it.

## 5. Look for who is new
Once per run, independent of the cards: a competitor with no card never shows up in steps 1 to 4.
- <tool:hubspot_object> — `op="search"` on deals with `dealstage` `IN` **[your lost stage ids]** and `closedate` `GTE` a date **[90 days, or your own lookback]** back, computed at run time. Ask for **[your loss-reason property]** and any competitor field your team fills in, 100 per page, paged with `after`. Read the competitor names out of the loss reasons, normalize them (case, legal suffixes, the domain where one is written), and keep the names that match no card.
- <tool:serper_search> — `kind="web"` on **[the category and "alternative to" queries your own comparison pages target]**, same `country` and `language` as step 1. Keep the vendors that keep appearing across those results.
- **A name is a candidate, not a rival.** It becomes a row in the candidates table when it appears in at least two lost deals, or in one lost deal and in the search set. A single appearance is recorded with a count and reported only once it recurs: a one-off mention usually comes from a prospect who was not a serious buyer, and a card built on it is wasted effort.
- A candidate that crosses the threshold gets a **full first draft** in the same fixed structure, from the same four reads as step 1. It has no cited URLs yet, so scrape its homepage first and take the pricing and product URLs from the homepage's own links rather than guessing them.

## 6. Stage every change for a person
- <tool:data_write> — the card table in one batch with `key="competitor"`: `status` (`current`, `stale`, `unverifiable`, `unreachable`), `last_checked`, `changes` (per ranked field: the card's text, the new text as quoted, its URL and read date; a staged entry stays until it is promoted or replaced by different new text), `draft_sections`, the count of consecutive runs with unreadable pricing, and the run id. When it stages a refresh that is new, the run sets the review column to `awaiting_review`. That is the only value it ever writes there.
- **A changed claim's cell is left exactly as it is.** The new text waits in `changes` and `draft_sections` until a person has applied it to the card document. Overwriting the cell during the run would point next week's comparison at the competitor's new text: the field would come back `unchanged` and a card nobody updated would stop being flagged. So `status` stays `stale` for as long as a material change is staged and the review column does not say `applied`.
- **A claim re-verified unchanged** gets only its `comment` layer re-dated, with no `valeur` key (`{"<field>": {"comment": "verified <date>"}}`), so the value and its source stay in place. An unsourced claim that a page read this run quotes as the card states it gets its `link` and `comment` the same way, value untouched.
- **Once the review column says `applied`**, the run promotes the staged text: the new value, its URL in `link` and the read date in `comment`, all three in the same cell in the same call, then clears that field from `changes` and `draft_sections`. **Writing a value drops the `link` and `comment` that came with it**, so a value written alone silently loses its source, and next week's comparison with it.
- **The judgment belongs to a person**: whether a refresh was applied or declined, and the card's own narrative. The run never turns `applied` or `declined` back on a refresh it already staged, and never puts the narrative in its batch, so a later run cannot undo a decision someone made.
- <tool:data_write> — the candidates table with `key="domain"`: name, domain, what they appear to sell, where the name came from, how many lost deals and searches, first and last seen, and the draft card once the threshold is crossed.
- Nothing is written to the card documents themselves. The row links to the card a refresh belongs to, so the person moving it does not have to search for it.

## 7. Report what went stale
- <tool:slack_post_message> — one top message to **[your competitive intel channel id]**, then the per-card detail as replies with `thread_ts` set to the top message's `ts`. The Slack app must be a member of the channel, or the call answers `not_in_channel`.

```text
Battlecards, week of <date> · <n> checked
Stale (<n>), refresh drafted: <competitor> · <competitor>
Still stale, awaiting review (<n>): <competitor>
Notable (<n>): <competitor>, <what moved>
Seen again, no card (<n>): <name>, <n> lost deals, <n> searches
Could not read (<n>) · No source on the card (<n>) · Unchanged: <n>
```

- Each thread reply names the card, the field, `"<old>" → "<new>"` and the URL, and says the drafted refresh is waiting on that card's row in the card table.
- **A quiet week still posts**, even if all it says is the unchanged count, because no message at all would look the same as a run that crashed.
- A message can be deleted but not edited, and text over roughly 4,000 characters is split into threaded parts rather than truncated. Read `split_into` before deciding to post again.

## Output
Cards checked; cards newly stale, with the material fields and both values; stale cards still awaiting review; refreshes applied and promoted into the table; notable changes; cards unchanged or cosmetic only; fields unreachable and cards with unsourced claims to backfill; candidate new competitors with their counts, and which of them got a first draft; and the posted message.