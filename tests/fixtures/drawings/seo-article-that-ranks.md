# Write an SEO article that can rank on Google

**When to use it**: you have one query you want a page for. Before a word is written, this checks that people actually search for it, that none of your own pages already answer it, and what the pages that rank look like. Then it writes the article to beat those pages and leaves it as a draft in your CMS for a person to publish. On results pages that open with an AI Overview, the page that wins is the most complete answer that is easiest to quote, not the most optimized one, and every step below follows from that.

```
              Natural language input in Claude
              "Write the article for 'how to calculate customer lifetime value'."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Measure the query before writing           ║   ahrefs_keywords_explorer
║  Volume, difficulty and intent per phrasing,    ║   ahrefs_serp_overview
║  then what already occupies the results page.   ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no demand         no volume behind any phrasing
                         ▼  a real query
╔═════════════════════════════════════════════════╗
║  2 · Check nothing of yours owns it             ║   webflow_cms
║  Your articles, a site search and impression    ║   serper_search
║  data name any page that already ranks.         ║   ahrefs_gsc
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ already yours     a live page owns the intent
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Read the live SERP and the top three       │   serper_search
│  The format that ranks, its headings, and the   │   serper_scrape
│  People Also Ask questions word for word.       │
└────────────────────────┬────────────────────────┘
                         ▼  the shape that ranks is on record
┌─────────────────────────────────────────────────┐
│  4 · Write the answer-first draft               │
│  The answer in the first sixty words, and H2s   │
│  worded as the questions people ask.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Add the blocks that lift                   │
│  Real tables, ordered lists only for sequences, │
│  source and internal links in the prose.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Check every figure and claim               │   data_rows
│  A number with no source is cut, not published  │
│  with a hedge.                                  │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Stage it as a CMS draft                    │   webflow_cms
│  One draft item in the blog collection, and a   │   data_write
│  row for the query; never a publish call.       │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  8 · Review and publish                         ║   the human step: the run ends at the draft
║  A person reads the draft, adds the links from  ║
║  existing pages, and publishes.                 ║
╚════════════════════════╤════════════════════════╝
                         ▼  on a later run, once the page is live
┌─────────────────────────────────────────────────┐
│  9 · Verify the served page, then track it      │   serper_scrape
│  Assert on the HTML a crawler receives, and     │   ahrefs_project_keywords
│  review the position after thirty days.         │   ahrefs_rank_tracker
└─────────────────────────────────────────────────┘

▪ terminal — no article is drafted for that query
```

## 1. Measure the query before writing
- Decide **[country]** and **[language]** first, from the query or from the person asking, never by guessing. Every call below takes them.
- <tool:ahrefs_keywords_explorer> with `report="overview"`, `country="[country]"` and `keywords="[query],[variant],[variant]"`. Put every phrasing in that one comma-separated call. Units are metered, and exploring related ideas is a keyword-research job, not part of this one. Record volume, difficulty, clicks and SERP features for each phrasing. A query with volume but few clicks gets answered on the results page itself, and that changes what the page is for.
- <tool:ahrefs_serp_overview> with `keyword`, `country` and `top_positions=10` shows who ranks and what kind of result each one is. Judge how strong each ranking page is, not its domain. A strong domain that ranks with a weak page means the position is won on content and can be taken. Read the column names from the tool schema before you add page-level columns to `select`.
- **Set the success metric now.** If an AI Overview or a featured snippet holds position one, count citations, branded search and assisted conversions instead of sessions. Write that on the row so nobody reads flat traffic as failure later.
- **Exit, no demand:** there is no volume behind any phrasing, or everyone searching it is looking for a company with that name. Write the row with `status="no demand"` and the phrasings tried, so the query doesn't get proposed again.
- **No Ahrefs key yet:** <tool:serper_search> with `kind="autocomplete"` shows which phrasings people complete. That proves a phrasing exists, not how much demand it has. Accept a query on it only as weaker evidence, and record which path the run took so the thirty-day review knows.

## 2. Check nothing of yours owns it
- <tool:data_rows> reads **[your articles table]** filtered on the query and its variants. If a row exists, pick up from its status instead of starting over. A row marked `no demand` or `already yours` stops the run.
- <tool:webflow_cms> with `op="collections"` finds the blog collection id (once). Then run `op="items"` on that collection, advancing `offset` until every item is read, and go through names, slugs and summaries. Two articles can have different titles and answer the same question, so read the summary, not just the title. Keep the slugs for step 7.
- <tool:serper_search> with `site_filter="[your domain]"` and the query, then the plain query in the target market. This shows whether any of your URLs are already on the page.
- <tool:ahrefs_gsc> with `report="keywords"`, `date_from` set **[three months]** back, and the `project_id` of the Ahrefs project that has Search Console connected (<tool:ahrefs_project> with `op="list"` returns the ids; keep it on the row for step 9). Filter with `where` on the query and its variants. Search Console names the URL that already collects impressions, which a site search can't: the site search only shows what ranks today. If the keyword rows carry no page URL, run `report="pages"` over the same period, pick the pages whose slug or title matches the query, and confirm each one with `report="keywords"` and a `where` on its `url`: the query has to appear in that page's own keywords. If Search Console isn't connected, rely on the site search, say in the brief that impression data was missing, and never assume what it would have said.
- **If one of your pages already owns the intent, improve that page instead.** Set `status="already yours"`, put the URL in `owner_url` and hand it to a person. A second page aimed at the same intent splits every signal, and even the stronger one ranks worse than a single combined page would. You can spot this when two of your URLs swap positions week to week, or when both show high impressions with a flat, low click-through rate.
- **Watch the broad parent page.** If a hub page already answers the query in full, it will quietly take the click the new article needs. The fix is to cut its answer to one sentence that links to the new article. That is an edit for a person to make, not this run.
- Write the primary query and the two or three variants the page will also own into `queries_owned`. If you can't name a page's query, it doesn't have one.

## 3. Read the live SERP and the top three
- <tool:serper_search> with `kind="web"`, the query, `country`, `language` and **`full=true`**. The default response only keeps title, link and snippet, and drops People Also Ask and related searches, which are exactly what this step needs.
- Record the intent and the format that ranks: a guide, a listicle, a comparison table or a calculator page. Match that format. An essay won't displace a page of listicles, and no amount of prose will displace a comparison table. Also record what else is on the page: an AI Overview, a snippet, People Also Ask, videos.
- **Copy the People Also Ask questions word for word.** They give you an H2 outline, phrased the way searchers phrase the query.
- <tool:serper_scrape> fetches the top three organic URLs exactly as the search returned them. Read their heading structure, their depth, their tables, and what they leave unanswered. If a page returns 200 with an almost empty body, it renders in the browser. Don't judge its shape from that.
- <tool:serper_scrape> one of your own live articles with `format="html"` to read the `<title>` your site actually serves. That tells you the suffix your template adds, and step 4's title budget has to include it.
- Write `shape` on the row: the format, the heading outline, the People Also Ask questions and the gaps.

## 4. Write the answer-first draft
- **The title tag and the H1 do different jobs, so they get separate fields.** The title tag has to win the click in 60 characters as served, suffix included, and starts with the query. The H1 orients a reader who has already arrived. If one string tries to do both, one job gets lost.
- **Answer the title in the first 40 to 60 words.** Write it as a standalone definition with no pronouns, one that doesn't lean on the heading above it: in an AI Overview, that paragraph is all the reader sees. Then delete the draft's first two paragraphs and check whether anything was lost. Usually nothing was.
- **Word each H2 the way the People Also Ask box words the question, and answer it in the first sentence underneath.** Turning a heading into a question and then not answering it makes the page worse. Use one H1 and never skip a heading level.
- Every H2 and its first paragraph must make sense on their own. Name the thing instead of writing "this approach", because a passage quoted elsewhere loses whatever came before it.
- Write the meta description by hand, 140 to 160 characters. Pick one category from your blog's own list.
- Take voice, terms and positioning from **[your tone and positioning notes]**. Don't shape the page around FAQ or HowTo markup: plain semantic HTML is what gets quoted.

## 5. Add the blocks that lift
- Use a real table for anything with two dimensions. Tables and short ordered lists are the easiest blocks for search engines and AI answers to lift. Number a list only when it is a real sequence, because numbering unordered items tells the reader something false.
- One idea per paragraph, with the claim first.
- Put templates and checklists inline, ready to copy. Never put them behind a download or an email form.
- Add a figure only where a claim has a shape to draw: a mechanism, a sequence, or a contrast over time. About three is the limit, never one per H2. A comparison needs a table, not a figure. The caption states the point, and the alt text carries the content, since alt text and caption are all a crawler reads of an image.
- Add two to five links to primary sources, but not in the sentence you want quoted. Put the link in the next sentence.
- Add three to eight internal links in the body text. Anchor text describes the destination in the reader's words, and one link goes up to the parent page, high on the page. Never use a bare URL or the same exact-match phrase every time. List the existing articles that should link to the new one in `links_in`. Adding those links means editing published pages, so that is the reviewer's job.

## 6. Check every figure and claim
These checks run in order, on the draft once step 5 has added its figures and source links:
- <tool:data_rows> reads **[your approved figures table]**. Every number about your own company must be in it, with its source. A number that isn't there doesn't get written. A number shown inside a product screenshot was never measured and doesn't count.
- A statistic from someone else links to its source.
- Leave out numbers that go stale, like rates, prices and yearly thresholds. Describe how the thing works and link to the source that has the current figure.
- Name a customer only if **[your customer references]** marks them as citable. A customer you only show as a logo is never named in the text.
- On legal, tax or health topics, rely on the primary text rather than on references a reader can't check in full. Where two readings exist, say so.
- A claim that fails gets cut here or sourced here. It is never published with a hedge. Declining to give a number is a valid outcome, and it reads better than an estimate.

## 7. Stage it as a CMS draft
- <tool:webflow_cms> with `op="collection"` reads the collection's `fields[]` first. `fieldData` keys are field **slugs**, not display names, and a slug guessed from a display name gets refused. Required fields must all be present.
- Check how your rich-text body field stores a `<table>` before you rely on one. Many CMS rich-text fields don't keep it as a real table, and step 9 checks for one on the served page.
- <tool:webflow_cms> with `op="create"`, `dry_run=true` checks `fieldData` against the schema without writing anything. Once that passes, run it again without `dry_run`, with `item={"fieldData": {...}, "isDraft": true}`. Set the slug explicitly and check it against the slugs from step 2 so it doesn't collide with an existing item. The item is staged and nothing shows on the live site until someone publishes. This process never calls a Webflow publish tool; a person publishes from Webflow.
- <tool:data_write> upserts one row keyed on the query with: `queries_owned`, country, language, volume, difficulty, intent, SERP features, success metric, `shape`, title tag, H1, description, `links_out`, `links_in`, the Ahrefs `project_id`, the CMS item id and `status="draft"`. Step 2 of the next run reads this row. An article missing from the table doesn't exist for that check, so the next run could write a second page for the same query.

## 8. Review and publish
- A person reads the draft in Webflow, fixes what reads wrong, adds the links listed in `links_in` to the existing articles, and publishes. The run ends before this, and `status="draft"` is how it normally ends.

## 9. Verify the served page, then track it
A later run picks up the same row once the reviewer says the page is live.
- <tool:serper_scrape> fetches the live URL with `format="html"`. That returns the raw HTML from a plain request instead of a rendered view, which is what a crawler receives. Content inside a collapsed element that only loads on click isn't in it.
- The HTML is capped. Read `html_tronque` before asserting anything: when it is true, the end of a long article is missing, so don't fail the checks that read the tail of the page (tables, internal links) on that HTML. Read the body again in the default markdown format for those two, and keep the HTML for the head checks.

| Assertion | Pass when |
| --- | --- |
| Fetch | Succeeds, and `final_url` is the published URL, not a redirect |
| Served `<title>` | Equals the title tag plus the suffix, 60 characters or fewer |
| Meta description | Equals the drafted one, 140 to 160 characters |
| The opening answer | Present in the body HTML, not inside a script or JSON-LD |
| Headings | One H1, the H2s in draft order, no skipped level |
| Tables | Real `<table>` elements |
| Internal links | Every one resolves |

- Measure lengths on decoded text. Served HTML uses entities for apostrophes and accented characters, so counting raw HTML makes a title that passes look too long.
- A failed check goes to the reviewer as an edit to make. The run never patches the live item. Write `url`, `published_at`, `verified` and any failed checks on the row with <tool:data_write>.
- <tool:ahrefs_project_keywords> with `op="add"`, the `project_id` kept from step 2, `keywords=[{"keyword": "[query]"}]` and `locations=[{"country": "[country]"}]`. The two lists are matched by position. Tracking starts on the day the page ships, so the thirty-day review reads a series of positions instead of one noisy check.
- Thirty days later, run <tool:ahrefs_rank_tracker> with `report="overview"`, the same `project_id`, `date` set to that day and `device` set to where most of your search traffic comes from, for the position. Then <tool:ahrefs_gsc> with `report="keywords"`, `date_from` set to the publish date and a `where` on the page's `url`, for impressions and click-through rate. A page at positions eight to fifteen with real impressions needs an edit, not a new article. A click-through rate well below what that position usually gets means the title and description need rewriting, before anything else is touched.

## Output
The query's measurements and what was decided: no demand, already covered (with the page that owns it), or drafted. Which data the run had: Ahrefs volumes or only autocomplete, and Search Console or not. The CMS draft's location, title tag, H1 and description. The links proposed in and out. On the verification run: the result of each check, and the keyword now being tracked.