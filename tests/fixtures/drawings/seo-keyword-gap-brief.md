# Find keywords your competitors rank for and you don't

**When to use it**: you have a list of competitors and a blog, and nobody has measured which searches those competitors already win that no page of yours answers. Run it monthly, or before planning a content quarter: it turns "what should we write next" into a ranked queue of queries, each with the shape of page that ranks and the page of yours that should answer it.

```
              Natural language input in Claude
              "Run the keyword gap against our five closest competitors for the US market."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Set the scope and the budget               ║   ahrefs_account
║  The country, the competitor domains and a unit ║   ahrefs_project_competitors
║  cap come from the operator, never a guess.     ║   units are billed per row returned
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Read who ranks for what                    │   ahrefs_site_explorer
│  Your domain down to position 100, each         │   four columns, the run date, an explicit limit
│  competitor to position 20, one domain a call.  │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Compute the gap and score it               ║   buyer words weigh double
║  A competitor ranks top 20 and you are absent   ║
║  or below 30; score it as volume times intent.  ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ owned already     your own URL ranks in the top 30
                         ▼  a real gap, scored
┌─────────────────────────────────────────────────┐
│  4 · Cluster by topic and product line          │
│  Your own blog categories and product pillars;  │
│  a query that fits none is itself a finding.    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Read the live SERP for the top clusters    │   serper_search
│  The page shape that ranks, the questions       │   ahrefs_serp_overview
│  people also ask, and who holds each spot.      │   top query of each top cluster only
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Write the brief                            │──▶  gap table  one row per query and country
│  One row per target query with a proposed page, │──▶  brief doc  the ranked table, owned queries, units spent
│  and a doc holding the ranked table.            │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  7 · A person picks the ten                     ║   the judgment call stays human
║  Each pick goes to a writer or a page refresh;  ║
║  nothing is written to the website here.        ║
╚═════════════════════════════════════════════════╝

▪ terminal — the query gets no proposal; its row still records which of your URLs owns it
```

## 1. Set the scope and the budget
Three decisions are taken before anything is spent, all from the operator, and written into the run's label so a later reader knows what the brief covers: **the country** (one market per run, since volumes and positions are per market; a multilingual site is one run per language), **the competitor set**, and **the unit cap**.
- <tool:ahrefs_account> — free. Read API-unit consumption and the subscription limit now, and again after every metered call in step 2. Ahrefs bills per row returned, so a run over a dozen domains can burn through more than a month's allowance before anyone notices.
- <tool:ahrefs_project_competitors> — free with `op="list"`. When you already track competitors in a Rank Tracker project, take their domains from here, with the `project_id` given by the operator or read from `ahrefs_project` `op="list"` (also free); otherwise from your own competitor or battlecard page. **Never derive a domain from a brand name** — a guessed domain reads the wrong site and still bills.
- No competitor list at all? `ahrefs_site_explorer` with `report="organic-competitors"` on your own domain, with `country` and `date` (both required by that report), names who you actually compete with in search, which is often not who sales calls a competitor.

## 2. Read who ranks for what
- <tool:ahrefs_site_explorer> — `report="organic-keywords"` on your own domain, with `country`, `date=<the run date, YYYY-MM-DD>` (the report refuses a call without it; the same date is stored as `read_at`), `select="keyword,best_position,best_position_url,volume"`, a `where` filter keeping `best_position` up to 100, and an **explicit `limit`**: Ahrefs returns 1,000 rows when the limit is omitted, and every one is billed. Ask for the four columns you use and nothing else.
- **A read that returns exactly `limit` rows is truncated**, and the tool has no offset to page on. Left alone, the missing rows turn queries you already own into false gaps at step 3. Split the read with `where` (`best_position` 1 to 30 first, since that band decides "owned", then 31 to 100) or raise the limit, and the brief says whether any read stayed truncated.
- The same report, with the same `date`, once per competitor domain, filtered to `best_position` 1 to 20. **One domain per call**, units recorded after each one, and the run stops cleanly at the cap. Any competitor not read goes in the brief under "not read this run" — the brief has to say which part of the market it did not see.
- The asymmetry is deliberate: your own domain is read deep so that "absent or below 30" in step 3 is a measured fact, while a competitor only matters where it already wins.

## 3. Compute the gap and score it
For every keyword any competitor holds in positions 1 to 20:
- **Owned already** — your domain holds it in positions 1 to 30. The row is still written, with `status="owned"` and `owner_url`, and nothing is proposed for it. One query, one page: a second page aimed at an intent you already rank for splits the signal, and both pages rank worse. Keeping the owned rows in the table is what lets a writing or page-refresh process refuse that query later.
- **A gap** — otherwise. Score it:

  `score = volume × intent`, where `intent = 2` when the query carries a buyer word (software, platform, alternative, vs, pricing, tool) and `1` otherwise. The operator adds the equivalents for the site's other languages. Drop anything under **[your monthly volume floor]**.
- <tool:data_write> — a batch keyed on one derived column, `gap_key = <country>:<lowercased query>`, passed as `key` (the batch key is a single field, so the composite is built into the row), so a monthly refresh **upserts instead of duplicating**: `gap_key`, `query`, `country`, `volume`, `intent_word`, `score`, `competitor`, `competitor_url`, `competitor_position`, `competitors_holding`, `your_position`, `owner_url`, `status`, `cluster`, `pillar`, `serp_shape`, `paa`, `proposal`, `assigned_to`, `read_at`. When several competitors hold the same query, keep the best-placed one and the count — a query three competitors rank for is stronger evidence than one.

## 4. Cluster by topic and product line
- Read your own blog categories and product pillars from wherever your team keeps them (a positioning doc, the blog's own category list) before touching the web, and use your site's words for them, not a keyword tool's.
- Give every gap query one `cluster` from the categories and one `pillar`. A query that fits no category gets `cluster="none"` and **stays in the table**: a topic your blog doesn't cover at all is a finding, not noise.
- Rank clusters by the sum of their scores. Step 5 only reads the top ones.

## 5. Read the live SERP for the top clusters
- <tool:serper_search> — `query=<target query>` with `country` and `language`, and **`full=true`**: the default response is trimmed and drops the People Also Ask box, which is half of what this step reads. Only the top query of each of the top **[N]** clusters, with **[N]** at least the number of picks step 7 makes, because Serper is billed per query and the operator's cap covers it too.
- Record `serp_shape` (buyer's guide, listicle, comparison table, vendor page, alternatives page), the People Also Ask questions verbatim in `paa`, and who holds positions 1 to 3.
- <tool:ahrefs_serp_overview> — `keyword`, `country`, `top_positions=10`: the domain rating and traffic of the pages holding the spot, so the person picking in step 7 sees what each query is up against. Optional, and metered.
- **The shape decides the proposal, and the shape is observed, not guessed:** a guide or a listicle means `new article`; a query where one of your pages already ranks 31 to 50 means `existing page pass`, with that URL; an "**[competitor]** alternative" or "**[your brand]** vs **[competitor]**" query means `alternatives page`.

## 6. Write the brief
- <tool:data_write> — the `proposal`, `serp_shape` and `paa` into every row read at step 5. A proposal is exactly one of three strings, and `existing page pass` always carries the URL to pass.
- <tool:oto_doc> — one doc per run: the table sorted by score within cluster, with the top ten marked among the rows that carry a proposal, the owned queries with their URLs, the competitors not read, any read that stayed truncated, the units spent against the cap, and the date of the readings. **Volumes and positions are third-party readings dated to the run** — the brief never presents them as facts about your market.

## 7. A person picks the ten
Whoever owns search reads the brief and assigns each pick: a `new article` goes to a writer with the query and its People Also Ask questions, an `existing page pass` goes to your page-refresh queue with the URL and the target query, an `alternatives page` goes to whoever owns comparison pages. The run writes `assigned_to` when told. **It never opens the website's CMS, never creates a draft and never changes a page** — deciding what deserves a page and writing it are separate jobs, and keeping them apart is what stops two processes building two pages on one intent.

## Output
The country and competitor set covered, competitors not read and why, reads that stayed truncated, keywords read, gaps found, queries marked owned, clusters ranked, SERPs read, units spent against the cap, and the top ten with their proposed pages.