# Update a Webflow page for the keywords buyers search

**When to use it**: your pages aren't thin, they're mistargeted. The title tags read like internal feature names or slogans nobody types into a search box, so a page with the right content ranks for nothing. Three fields fix that without touching the copy: a title tag that leads with the real query, a meta description that says what the reader gets, and one question-shaped heading. This runs nightly over a register of pages you care about, a few at a time, with a person approving every change before it reaches the site.

```
              Scheduled routine, nightly in batches of five
              "Refresh the title and meta description of our pricing page."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Take the pages due                         │   data_rows
│  Approved proposals first, then up to five due  │   the state picks, the pass date orders
│  pages, never passed first, or the one named.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Read the SERP, then the page as served     │   serper_search
│  What ranks decides the wording; the HTML a     │   serper_scrape, webflow_pages
│  crawler receives shows what is there today.    │   ahrefs_site_explorer
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ owned elsewhere   another of your URLs ranks for it
                         ├───────────────▶  ▪ not verifiable    the served HTML carries no body
                         ▼  the phrasing is observed, not guessed
┌─────────────────────────────────────────────────┐
│  3 · Write the three fields                     │   never body copy, never a new number
│  A title that leads with the query, a payoff    │
│  description and one question-shaped H2.        │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  4 · A person approves the diff                 ║   webflow_pages, webflow_cms
║  Before and after for every field, approved     ║   dry_run returns the real diff
║  before any write: a page goes live on write.   ║   slack_post_message
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ awaiting approval  picked up once a person approves
                         ▼  approved
┌─────────────────────────────────────────────────┐
│  5 · Write the fields to Webflow                │   webflow_pages
│  Page settings go live at once; a CMS item is   │   webflow_cms
│  staged, then only that item is published.      │   webflow_publish, one call per collection
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ held for a person  the item carries someone else's staged edit
                         ▼
╔═════════════════════════════════════════════════╗
║  6 · Verify live, keep or revert                ║   serper_scrape
║  Re-read the live page and assert every field;  ║   a revert is the process working
║  one failed check restores the previous values. ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ reverted          a live check failed; retried once, then a person
                         ▼  every field verified live
┌─────────────────────────────────────────────────┐
│  7 · Record and report                          │──▶  page register  last pass date, before and after, defects
│  The register row with the date and the defects │──▶  Slack channel  one line per page, a warning when needed
│  seen, and one line per page to the team.       │
└─────────────────────────────────────────────────┘

▪ terminal — the page stops there; its register row says whether it returns to the queue or waits for a person, and its line in the channel says why
```

## Before the first run
- **A page register**, one row per page you want passed: `url`, `surface` (`page` for a static page, `cms` for a collection item), `page_ref` (the page id, or the collection id and item id), `target_query`, `country`, the three fields `_before` and `_after`, `title_suffix`, `h2_placement`, `state` (`due`, `proposed`, `approved`, `live`, `owned_elsewhere`, `not_verifiable`, `held`, `reverted`, `retired`), `revert_count`, `approved_by`, `published_at`, `verified`, `last_pass_at`, `owner_url`, `defects`, `note`. Seed it with the pages that matter (home, product and solution pages, pricing, integrations), each with the query its owner wants it to answer. A keyword gap brief is a good source for both the pages and the queries.
- **A Webflow token** with page and CMS scopes on the site, and **the Slack channel** your search owner reads.
- Your own positioning doc, published figures and the list of customers you're allowed to name. Every claim written in step 3 has to stand on one of those.

## 1. Take the pages due
- <tool:data_rows> — three reads, in this order:
  1. `filter={"state": "approved"}`: proposals from an earlier night that a person has since approved. They go straight to step 5.
  2. `filter={"state": "due", "last_pass_at": {"empty": true}}`, `limit=5`: pages never passed. They get a read of their own because `data_rows` sorts empty cells last in both directions, so a single query ordered by `last_pass_at` would put them at the back of the queue, not the front.
  3. Only when read 2 left slots free: `filter={"state": {"in": ["due", "live"]}, "last_pass_at": {"not_empty": true}}` plus `filters=[{"field": "last_pass_at", "op": "lt", "value": <today minus [your re-pass interval]>}]`, `order_by="last_pass_at"`, `order_dir="asc"`, `limit` set to the free slots: the pages whose last pass is oldest.
- On demand, the one URL named instead; a URL not yet in the register is added first at `state="due"`, with its target query asked of the operator.
- **`state` decides what is taken; `last_pass_at` only orders it.** `proposed` waits for a person. `owned_elsewhere`, `not_verifiable`, `held` and `reverted` wait for a person to fix the cause and set the row back to `due`. `retired` is never taken. Select on the date alone and every unapproved proposal is re-read, re-written and re-posted every night, until the five slots are permanently full of the same pages and new ones are never reached.
- **Each state is written the moment it changes**, so an interrupted run resumes where it stopped. Don't use a claim-and-lease queue for this: a lease expires while a proposal waits for a person, and the page gets proposed twice.

## 2. Read the SERP, then the page as served
- <tool:serper_search> — `query=<target_query>` with the row's `country` and `language`, and `full=true` so the People Also Ask box comes back. Read who ranks, in what format, the questions verbatim and the exact phrasing of the top titles. **The SERP decides the wording**, not the page's current H1: if what ranks says "**[category]** software" and the page's title is a product slogan, the SERP wins.
- **Owned elsewhere is an exit.** If another URL on your own domain appears in the results for the target query, write it into `owner_url` and set `state="owned_elsewhere"`: the report tells the owner to change the target query or pass the other page instead. Two of your pages aimed at one query split the signal and both rank worse.
- <tool:ahrefs_site_explorer> — `report="organic-keywords"` with `target=<page url>`, `mode="exact"`, the row's `country`, `date=<the run date, YYYY-MM-DD>` (the report refuses a call without it), `select="keyword,best_position,volume"` and a small explicit `limit`: the queries this exact URL already ranks for. It records the target query's position before the pass (the after is read on a later night, since positions move over weeks, not hours), and it surfaces a closely related query the page already ranks for with more volume — proposed to the owner in step 4, never swapped in silently.
- <tool:serper_scrape> — `format="html"`: the raw HTML from a plain request, no scraper credit spent. Record `title_before`, `description_before`, the H1 and the H2 nearest the top **from the served HTML, never from the Webflow Designer or the CMS draft**: the problem lives in what a crawler receives. A page that serves a title and no body (content rendered client-side) exits at `state="not_verifiable"` — step 6 could never prove the change landed — and is reported as a defect.
- <tool:webflow_pages> — `op="get"` with the row's `page_id` (a CMS item: `webflow_cms` `op="item"`) reads the SEO title as it is stored in the field. **The served title minus that field is `title_suffix`**: a brand suffix the template appends, or nothing. It is recorded on the row, because step 3 counts it against the 60 characters and step 6 expects it on the live page.

## 3. Write the three fields
- **The title tag** leads with the query the SERP showed, **60 characters or fewer as served**: the field plus `title_suffix`. Templates often add a brand suffix to some pages and not others, which is why the suffix is measured per page at step 2 rather than assumed. The H1 is never touched.
- **The meta description** is 140 to 160 characters, carries the query's terms, and says what the reader gets from the page — it describes the page, not the vendor.
- **One question-shaped H2**, worded the way the People Also Ask box words it, and only where the paragraph that already follows would answer it in its first sentence. If no existing paragraph answers it, don't write the heading: a question the prose walks past makes the page worse.
- **Hard limits.** Only what the product does as your own docs record it, never a capability generalized from a sibling page. No number that isn't in your published figures. No customer who isn't on your citable list. And a field that reads identically to another page's `_after` value in the register fails this step for both pages.

## 4. A person approves the diff
- <tool:webflow_pages> — `op="update"` with `dry_run=true` fetches the page and returns the real diff (`changes: {field: {from, to}}`) without writing anything.
- <tool:webflow_cms> — `op="update"` with `dry_run=true` does the same for a CMS item; the field slugs come from `op="collection"`.
- Store the diff's `from` values in the register as the revert. **Take them from Webflow, not from the served page**: a served title that carries a template suffix, written back into the field, would come out with the suffix doubled.
- <tool:slack_post_message> — one line per page with before and after for each field and the SERP phrasing it came from, and the row moves to `state="proposed"`. The run stops there for that page. A person approves by setting `state="approved"` or by telling the agent, and step 1 picks it up.
- **Why the gate sits before the write, not after it:** `webflow_pages op="update"` has no draft. A title or SEO change on a static page is visible to visitors and search engines the moment it is written, so there is no "stage it and let a person publish" for page settings. The review has to happen first.

## 5. Write the fields to Webflow
- **Re-run the dry-run before writing an approved row**, and compare it with what was approved. Any `from` value that no longer matches means someone edited the page since: the row goes back to `proposed` with the fresh diff instead of overwriting their change. An empty diff means the write already landed on a night that was interrupted: a CMS item goes straight to its publish, a static page to step 6.
- **A CMS item must carry nobody else's staged edit.** `webflow_publish` publishes the item's whole staged state, not only the fields this run wrote. So before writing an item, read it with `webflow_cms` `op="item"`: a `lastUpdated` later than its `lastPublished` means a change someone staged is not live yet. The item is not written, the row goes to `state="held"` with the reason in `note`, and a person decides.
- <tool:webflow_pages> — a static page: `op="update"`, `page_id`, `seo={"title": …, "description": …}`. Only the fields given change, and the change is live at once. The API cannot write a static page's body on the site's primary locale at all, so **a static page's H2 goes into the report for a person to place in the Designer**, while the other two fields still ship.
- <tool:webflow_cms> — a CMS item: `op="update"` with `collection_id`, `id` and `item.fieldData` holding only the SEO title, the description and, where the collection has a dedicated field for it, the H2. Never rewrite a rich-text body to insert a heading. The update is staged.
- <tool:webflow_publish> — **one call per collection**, with that `collection_id` and the `ids` of that collection's items in this batch, never any other item. **Never `webflow_site_publish` by default**: it publishes the whole site, including every change anyone else has staged since the last publish, so it runs only on the operator's explicit word.
- <tool:data_write> — `published_at`, and `h2_placement` set to `written` or `for a person`.

## 6. Verify live, keep or revert
- <tool:serper_scrape> — `format="html"` on the live URL, once the publish has had time to propagate. Assert in code, per page:

| Assertion | Threshold |
| --- | --- |
| The page loads, with its body | not an error page or an empty shell |
| Served `<title>` equals `title_after` + `title_suffix` | the served string is 60 characters or fewer |
| Served meta description equals `description_after` | 140 to 160 characters |
| The H2 is in the served HTML, outside any script or JSON-LD | when `h2_placement="written"` and the HTML is complete |
| The H1 | identical to what step 2 read |

- **The served HTML is capped.** When `html_caracteres` reports more than the HTML returned, the page was cut off, and an H2 missing from a cut-off page proves nothing: the H2 check is recorded as not verifiable in `note`, and the title, description and H1 alone decide.
- **One failure reverts that page**: write the stored `from` values back with the same step 5 call, publish by the same path, re-scrape and confirm the old values are live, then set `verified=false`, add one to `revert_count`, and set `note` to the assertion that failed. `last_pass_at` does not move. **A first revert puts the row back at `state="due"`**, so a later night re-reads the SERP and proposes again; a second leaves it at `state="reverted"`, which step 1 never takes, until a person has looked. A revert is the process working, not failing.

## 7. Record and report
- <tool:data_write> — for every page: `verified`, `state` and `defects`, with `last_pass_at` set to today and `state="live"` **only for pages that verified live**. A defect is anything wrong the run saw and **did not fix**: a typo in an H1, placeholder text, a broken link, a figure that contradicts another page. Each is recorded once, on the page where it was seen. The run never edits body copy to fix one.
- <tool:slack_post_message> — a header line and one line per page:

```text
Search pass · <date> · <n> proposed, <n> live, <n> reverted, <n> owned elsewhere
<url> · "<title_after>" · <proposed | live | reverted: <assertion> | owned by <owner_url> | held: staged edit>
Needs a person: <only when one is: an approval waiting, a revert, a held item, an H2 to place by hand, a defect>
```

If the post fails on `not_in_channel`, finish the run anyway: the register rows are written, and the failure goes into `note`.

## Output
Pages taken, proposed and waiting for approval, owned elsewhere, not verifiable, held for someone else's staged edit, written, verified live, reverted with the failed assertion (and whether it goes back to the queue or to a person), H2s left for a person to place, and defects recorded.