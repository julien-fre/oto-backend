# Find shops that could stock your product, city by city

**When to use it**: you make something that sells through independent shops, and finding them still means one map search, the first page of results, and the same email to all of them. This searches every target city from two angles, ranks what it finds before anyone writes, and runs the follow-up on a schedule instead of on memory.

```
              Natural language input in Claude
              "Find independent shops in these three cities that could stock our new collection."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Set the cities and the bar                 │   data_rows
│  Target cities, the labels to sit beside, your  │
│  price band, and what earlier runs found.       │
└────────────────────────┬────────────────────────┘
                         ▼  per city
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  2 · Search the map listings     │  │  3 · Mine the local roundups     │   serper_search
│  Several phrasings a city, paged │  │  Recent best-of guides, scraped  │   serper_scrape
│  until a page adds nothing new.  │  │  for every shop they name.       │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     │
                 └───────┬─────────────────────────────┘
                         ▼  every name from both sources
┌─────────────────────────────────────────────────┐
│  4 · Resolve each shop to one row               │   serper_search
│  Every name matched to its map listing, keyed   │   data_write
│  on its place id, in one table for all cities.  │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Rank into tiers                            ║   serper_scrape
║  Positioning from the shop's own site, then     ║   serper_search
║  reviews, price point and activity.             ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ off-brand         fails positioning fit
                         ▼  tier A or B; tier C waits for a person
┌─────────────────────────────────────────────────┐
│  6 · Find the address, draft the opener         │   serper_scrape
│  A real address from the site, a review detail  │   serper_reviews
│  for tier A, saved as a draft.                  │   gmail_compose
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  7 · Review, then send                          ║   gmail_message
║  A person edits and sends each draft from       ║   data_write
║  Gmail; the run finds it in Sent afterwards.    ║
╚════════════════════════╤════════════════════════╝
                         ▼  on a daily scheduled run
┌─────────────────────────────────────────────────┐
│  8 · Follow up on a fixed schedule              │   data_rows
│  Check for a reply first, nudge at day 7,       │   gmail_message
│  switch channel at 14, archive at 21.           │   gmail_compose
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ archived          no reply after the last touch
                         ▼  the shop starts stocking you
┌─────────────────────────────────────────────────┐
│  9 · Keep active accounts coming back           │   data_rows
│  Each season, the new collection and a real     │   gmail_compose
│  check-in, from the same list.                  │
└─────────────────────────────────────────────────┘

▪ terminal — nothing more is sent to that shop in this cycle
```

## 1. Set the cities and the bar
Write the bar down before anything is searched, because step 5 can only rank against something explicit:
- **[target cities]**, each with its country and language (they set the search market for every call below).
- **[positioning]**: the three to five labels or kinds of product you would want to sit next to on a shelf, and the ones you would not.
- **[wholesale price band]**: the retail price range a shop has to carry for your terms to work.
- <tool:data_rows> — read the shop table first, filtered to the cities in this run. A shop already there is resumed from its status, never re-found and never re-contacted. One table for every city, filtered by city — never one table per city, or the next pass starts from zero.

## 2. Search the map listings
- <tool:serper_search> with `kind="places"`, `location="[city], [country]"` and the market's `country`/`language` — a first query in your own words ("independent [category] shop"), then two or three variants by neighborhood or style keyword. A single places query returns a short, anchor-biased list and says nothing about what it left out; the variants are what widen it.
- Page each variant with `page` until a page adds no place id you have not already seen. Pages overlap, so "until the page is empty" wastes calls and "first page only" misses most of the city.
- Keep, per result: the place id (`cid`), name, address, phone, website, rating and rating count. The `cid` is the key for step 4 and the handle step 6 passes to the reviews call.
- When the category is a plain business type rather than a positioning (every bakery, not "shops that carry independent labels"), the Serper maps census tool tiles the city and dedupes server-side instead — it is built for exhaustive counts, and costs accordingly.

## 3. Mine the local roundups
- <tool:serper_search> `kind="web"` for "best [category] shops [city]", "[category] [city] guide" and the same in the local language, with `tbs="qdr:y"` to keep to the last year. An old roundup is a list of shops that may have closed since.
- <tool:serper_scrape> each roundup URL exactly as the search returned it, never an address built from a site name — a made-up URL costs a full timeout. A timeout is not retried. A 200 with an almost empty body means the page renders in the browser, not that the article is empty: skip it rather than conclude anything from it.
- Pull every shop the article names into a list (name, neighborhood, the article's one-line description) instead of reading it as prose. That list is where most of the volume comes from: a guide names shops a places search never surfaces.
- Keep the roundup URL on each name. A shop named by several guides is itself a signal for step 5.

## 4. Resolve each shop to one row
- <tool:serper_search> `kind="places"` with "[shop name] [city]" for every roundup name that has no place id yet. Accept the match only when the listing's address is in the same city and the name agrees; otherwise keep the row as `unresolved` rather than attach the nearest similarly named listing, which is how one shop ends up with another shop's reviews and phone number.
- **Dedup key: the place id**, then the website's domain when there is no listing, then normalized name plus street as a last resort. The same shop shows up as "[Name]" in the listings and "[Name] Shop & Café" in a guide; the place id is the only identifier both agree on.
- <tool:data_write> — upsert one row per shop on that key: name, city, neighborhood, address, phone, website, rating, rating count, sources (listing and roundup URLs), and `status="found"`. An upsert means a second pass over the same city merges into the rows it already has.

## 5. Rank into tiers
- <tool:serper_scrape> the shop's own website — the brand or stockist page, a few product pages, the about page. Check the labels it carries against your **[positioning]** and its prices against your **[wholesale price band]**. No website: rank on the listing and the roundup text alone, and say so in the reason.
- Review score is weighted by count, not read alone: below **[minimum review count]** the rating does not move the tier. A near-perfect score on a handful of reviews is not a strong signal.
- Social activity: <tool:serper_search> `kind="web"` with `site_filter="instagram.com"` and the shop's name. The snippet often carries a recent post or a follower count; the profile page itself sits behind a login, so a scrape of it returns a shell. When nothing is visible, record `unknown` — never read a missing snippet as an inactive shop.
- Tiers, not a score, so the reasoning stays readable: **A** fits and has strong signals, **B** fits with weaker ones, **C** might fit and is held for a person to look at before anything is drafted. Anything that clearly fails positioning is dropped with its reason and never reaches an inbox review. Write the tier and a one-line reason on the row.

## 6. Find the address, draft the opener
- <tool:serper_scrape> the contact or about page for an email address. Common obfuscation patterns are decoded and returned in `adresses_obfusquees`; when the result reports an obfuscation pattern but no address, read the page again with `format="html"`. Prefer an address with a person's name on it over a generic inbox. **Never construct an address** from a name and a domain. No address found: flag the row for a call or a message from the shop's social account, don't drop it.
- <tool:serper_reviews> — tier A only, with the place id from step 2 and `op="page"`. One page is enough here because you want one concrete detail, not a verdict on the shop; the default `op="all"` pages through up to two hundred reviews and costs accordingly. Take something customers mention repeatedly (a kind of product, the way the shop is run), never a single complaint. No usable detail: tier A gets the plain opener, and a detail is never invented.
- <tool:gmail_compose> without `mode`, so it saves a **draft**: the detail where there is one, two lines on what you make and where it sits, and one concrete next step — **[catalog and wholesale terms]** or a short call. Read `kind` in the response and store the draft id and the date on the row with `status="drafted"`.

## 7. Review, then send
- A person opens each draft in Gmail, edits what reads wrong, and sends it from there. Nothing leaves the mailbox without that. A freshly created draft can take a while to appear in a Gmail tab that is already open — reload it or search `in:drafts` before deciding a draft is missing.
- The run never sends a reviewed opener itself. A send call builds a new message from the body it is given, so it would drop the reviewer's edits and leave the reviewed draft sitting in Drafts, where a later click on Send becomes a second opener to the same shop.
- <tool:gmail_message> `op="search"` with `in:sent to:[address] after:[draft date]` for every `drafted` row, on the next pass. A match returns the message `id` and `threadId`: write both, with the send date, on the row with <tool:data_write> and `status="contacted"`. Step 8 replies on that `id`.
- No match in Sent: <tool:gmail_message> `op="drafts"` tells the two cases apart. A draft still listed stays `drafted` and is never followed up. A draft that is in neither place was discarded by the reviewer: mark the row `declined` so the next run does not draft it again.

## 8. Follow up on a fixed schedule
The cadence runs on a recurring routine (daily is enough) that reads the contacted rows. It is decided once and applied the same way to every shop, so nothing depends on someone remembering:
- <tool:data_rows> — the rows with `status="contacted"` whose last touch has reached the next step of the cadence.
- <tool:gmail_message> `op="search"` with `from:[shop domain] newer_than:30d` before any follow-up — the domain, not the exact address, because the owner often answers from a different mailbox than the one you wrote to (for a shop on a free webmail domain, search the exact address and the subject line instead). A reply of any kind stops the cadence and moves the row to `replied`. Nudging a shop that already answered is the one mistake this schedule can make, and this check is what prevents it.
- **[First nudge, e.g. day 7]:** <tool:gmail_compose> with `reply_to` set to the message id stored in step 7, so the short nudge lands in the same thread under the same subject. No `mode`, so it is saved as a draft and goes through the same review as step 7, and is found in Sent the same way before the touch is logged.
- **[Channel switch, e.g. day 14]:** a call on the listing's phone number or a message to the shop's social account. A person does this one and logs it on the row.
- **[Archive, e.g. day 21]:** `status="archived"` with the date. A later run leaves it alone until **[re-contact window]** has passed.
- Every touch is appended to the row: date, channel, outcome. That log is what keeps a later pass from sending the same opener twice.

## 9. Keep active accounts coming back
- A shop that starts stocking you stays on the same row with `status="active"` — the list continues past the yes.
- <tool:data_rows> on **[seasonal cadence]** — active shops whose last touch is older than the cadence. Draft the new-collection note with a real check-in question (what sold through, what customers asked for) through <tool:gmail_compose>, and send it through the same review as step 7.
- An active shop with no order logged on the row and no reply in **[quiet threshold]** is marked `quiet`, the same way a prospect is tracked, so a live account doesn't drift off unnoticed.

## Output
Per city: shops found by source (listings, roundups, both), resolved and unresolved, count per tier and dropped for positioning, drafts written, sent by the reviewer and still waiting, replies, archived, and active and quiet accounts — plus the rows flagged for a person (no address, tier C, no website).