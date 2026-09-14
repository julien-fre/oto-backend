# A product release updates the Notion docs that became stale

**When to use it**: a release changes how something behaves, and every help article and doc page describing the old behavior is now quietly wrong until a customer finds the stale sentence. This quotes each sentence a release made false and writes its replacement in the page's own voice, staged as a help-center draft or a Notion fix for a person to accept or paste. The same message carries the documentation gaps your customers already hit, which no release would ever surface.

```
              Scheduled routine, weekly, or right after a release
              "The release went out this morning. Which help articles are now wrong?"
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Find the releases to sweep                 │   productlane_changelogs
│  The release you named, or every unswept        │   data_rows
│  published entry, oldest first.                 │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ nothing new       every release already swept
                         ▼  a release not yet swept
┌─────────────────────────────────────────────────┐
│  2 · Reduce it to behavior statements           │   linear_issue
│  What a user can now do, what now works         │
│  differently, and what is gone.                 │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ internal only     no customer-visible change
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Find every page that describes it          ║   productlane_docs
║  Search the help center and Notion widely, then ║   notion_search
║  read each candidate in full.                   ║   notion_get_blocks
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ no page           a gap to report, not a fix
                         ▼  a page carries the behavior
╔═════════════════════════════════════════════════╗
║  4 · Decide what is now false                   ║   no quote, no claim
║  Quote the exact sentence the release broke,    ║
║  or record the page as still accurate.          ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ still accurate    recorded as checked
                         ▼  the page is now false or incomplete
┌─────────────────────────────────────────────────┐
│  5 · Write only the lines that moved            │   oto_doc
│  Replace the broken sentences in the page's     │
│  own voice and leave the rest untouched.        │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Stage it, never publish it                 │   productlane_docs
│  A Productlane draft, a Notion fix to paste,    │   data_write
│  then the release marked swept.                 │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Add the gaps people already hit            │   data_rows
│  Open documentation gaps, most-asked first,     │
│  reported even when nothing was wrong.          │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · Post one message                           │   slack_post_message
│  What is wrong, what is missing, and what       │
│  waits in Productlane for an accept.            │
└─────────────────────────────────────────────────┘

▪ terminal — nothing more is drafted for that release or page; the message still counts it
```

The steps follow one release and one page through. Step 1 can return several releases, and each is swept in turn, oldest first: steps 2 to 6 run once per release, and inside a release steps 3 to 5 run once per page it touches, so a release that changed one behavior described in four places produces four drafts and four page rows. Steps 7 and 8 run once per run, independent of the releases, and the gaps are reported even when nothing was invalidated: a run that says "nothing to correct" while the same unanswered question keeps coming in is telling half the truth.

## Before the first run
- **A changelog with published entries** in Productlane, each naming the tracker issues it shipped. If your changelog lives in a table another process writes, read that table with <tool:data_rows> at step 1 instead; nothing else changes.
- **The release to start from** (**[the publish date of the first release to sweep]**). Entries published before it are never swept, so the first run doesn't reopen your whole changelog history.
- **A doc updates table** with one row per page per release, plus one release row per release swept: `release`, `surface` (`productlane`, `notion`, or `release` on the release row), `article_id`, `article_title`, `visibility`, `verdict` (`false`, `incomplete` or `accurate` on a page row; `swept` or `internal_only` on the release row), `quote`, `replacement`, `heading`, `draft_id`, `undocumented` (on the release row, the behavior statements no page describes), `status` (`awaiting_accept`, `awaiting_paste`, `done`, `declined`), `run_id`. Give it a `row_key` column declared as the business key, `<release>:<surface>:<article_id>` on a page row and `<release>:release` on the release row, so a replay merges instead of duplicating.
- **A documentation gaps table** that your call or support feedback process writes to: `question`, `believed_answer`, `accounts` (how many accounts asked, declared as a number so it sorts numerically), `surface`, `status`.
- **The Notion root page** your docs live under, shared with the Notion integration.
- **Your own reference pages** in your knowledge base: what the product does, what each plan carries, the integrations list, and your writing rules (**[your voice rules, e.g. how you address the reader, words you never use]**).
- **Your docs channel** in Slack, with the app a member of it.

## 1. Find the releases to sweep
- When the operator named a release, take it. Otherwise <tool:productlane_changelogs> `op="search"`, `published=true`, `limit=50`, paging with the returned `cursor` until it runs out, and keep the entries published on or after your start date. The search has no sort option, so order the candidates yourself by the published date each result carries, oldest first, never by page order.
- <tool:data_rows> on the doc updates table, `filter={"row_key": {"in": ["<release>:release", ...]}}`, one key per candidate. **Only the release row means swept.** Any other row is not a guard: a release whose behaviors no page describes writes no page row at all, and a guard on "any row exists" would pick it again and post the same message every run. Every candidate without a release row is swept, oldest first, so a week with several releases leaves none behind.
- When every candidate already has its release row, the run leaves at `nothing new`: nothing is swept, and steps 7 and 8 still run, headed `No new release to sweep`.
- <tool:productlane_changelogs> `op="get"` with `changelog_id` for the full body of each release to sweep.
- **An unpublished entry is not a release.** Filter on `published=true`: an entry still in draft describes something customers can't see yet, and "correcting" the docs to match it makes them wrong in the other direction.
- **Never call `op="broadcast"`.** It emails subscribers and posts to the configured Slack channels with no undo; it is dry-run by default, which makes a single `dry_run=false` the whole failure.

## 2. Reduce it to behavior statements
- **The changelog entry is the input, not the diff.** It is already written in the words a customer reads, which is the register the help articles are in, and that shared register is what makes the matching in step 3 work at all.
- <tool:linear_issue> `op="get"` on each issue key the entry references, for the description of what the change was meant to do. Where the entry names no keys, `op="search"` with the feature's name.
- Reduce the release to **three or four behavior statements**: what a user can now do that they could not, what now happens differently, and what is gone. Each one names the module the way your product reference page names it, not the way the pull request title does. Everything downstream matches against these statements, never against engineering wording.
- **A release with no customer-visible change stops here**, at `internal only`. Its release row is written straight away with <tool:data_write>: `row_key="<release>:release"`, `surface="release"`, `verdict="internal_only"`, so the next run doesn't pick it up again.

## 3. Find every page that describes it
- <tool:productlane_docs> `op="articles"`, `kind="doc"`, `visibility="all"`, `limit=100`, paging with `cursor` until it runs out. The default page is 50 rows, and one page is not the help center. Match on the behavior statements, the module name and the terms your product uses for the feature, then `op="article"` with `article_id` to read each candidate in full.
- <tool:notion_search> with `filter_type="page"`, run once per module name and once per behavior term, keeping only results under your docs root. <tool:notion_get_blocks> with `recursive=true` reads the page text; `notion_get_page` returns properties only, and a page judged from its properties has not been read.
- **Search widely, confirm narrowly.** A title match is a candidate, never a finding: a page only counts once its text has been read and actually carries the behavior. Keep every page read, including the ones dismissed, because "these pages were checked and are fine" is half the value of the run.
- **Notion search is not a listing.** It returns only what the integration can see, so a page under a parent that was never shared with it looks exactly like a page that doesn't exist. Confirm the docs root answers before believing an empty result.
- **Productlane visibility has four values**: `public`, `agent`, `internal`, `unlisted` (`all` exists only as a list filter). Flag every `agent` article whatever else happens to it: that is what the help-center assistant answers customers from, so a wrong sentence there is said aloud, not merely read. A fix to an `internal` article is reported as internal, not as a customer-facing correction.
- A behavior no page describes leaves at `no page`. It is kept for the release row's `undocumented` field and reported in the message, never drafted into a new article nobody asked for.

## 4. Decide what is now false
One verdict per page, and nothing in between:

| Verdict | What it means | What it needs |
| --- | --- | --- |
| false | A sentence contradicts what now ships | The sentence quoted exactly, with its heading |
| incomplete | Nothing is wrong, but the page omits what the release added | The heading where it belongs, and why |
| accurate | The release didn't change what this page says | Nothing; its row is written in step 6 |

**No quote, no claim.** A page is reported as wrong only when the run can quote, verbatim, the sentence that is now false. A page that "feels stale" is not a finding, and a summary of a page is not evidence against it: a verdict without a quotable sentence is `accurate`.

## 5. Write only the lines that moved
- <tool:oto_doc> `op="search"`, then `op="get"`, on your writing rules and your product reference pages, read before a word is written.
- For a `false` verdict, write the replacement for **that sentence or that paragraph**, in the page's own voice and structure. For `incomplete`, write the missing paragraph and name the heading it goes under.
- **Never rewrite a whole page.** It destroys the work of whoever wrote it and buries the actual change in a diff nobody can review.
- The register is the site's, not the engineering team's: no internal code names, no branch names, no issue keys. A number appears only as it stands in your reference pages, and a capability only if your product pages already carry it: an article is a promise, and a release note is not a license to invent one.

## 6. Stage it, never publish it
- <tool:productlane_docs> `op="drafts"` twice, once with `status="draft"` and once with `status="open"`, matched on `article_id`. A draft that was just created can sit in either state, and checking only one opens a second draft on the same article. **An article that already has a pending draft gets no second one**; it is named in the message instead, so the person reviewing sees one proposal per article.
- <tool:productlane_docs> `op="create_draft"`, `kind="edit"`, `article_id`, `fields={"content": "<the full article markdown with only the affected lines changed>"}`. Start from the content `op="article"` returned and keep every image reference verbatim: the draft carries the whole article, and a partial copy is how content goes missing.
- **The only Productlane write is `create_draft`.** Never `update` (it applies immediately, and with `allow_image_removal` it deletes every image the new content omits), and never `create`, `delete`, `move` or `accept`. A person accepts, in Productlane, having read it. **Whoever accepts must read the returned status**: `accept` can answer `superseded` instead of `accepted`, an HTTP success that applied nothing because the article changed under the draft, and that correction then has to be rebuilt against the article as it now stands.
- **Nothing is written to Notion.** It has no draft state, and any write is live the moment it lands; the replacement goes in the table with `status="awaiting_paste"`.
- <tool:data_write> with `rows=[...]` and `key="row_key"` on the doc updates table: one row per staged page on both surfaces, carrying the quote, the replacement, the heading and the `draft_id` Productlane returned (`status="awaiting_accept"`), and in the same batch one `verdict="accurate"` row for every page read and found accurate, so "checked and fine" is on record too. **The row outlives the draft**: if the draft is declined, the next release can still tell "nobody has fixed this yet" from "this was never raised".
- **Then mark the release swept**, with a second <tool:data_write>: `row_key="<release>:release"`, `surface="release"`, `verdict="swept"`, and `undocumented` holding every behavior step 3 found no page for. It is written whatever the outcome, and it goes last on purpose: a run that dies midway leaves no release row, so the next run sweeps that release again, and the pending-draft check and the `row_key` merge keep the replay from opening a second draft or a second row.

## 7. Add the gaps people already hit
- <tool:data_rows> on the documentation gaps table, `filter={"status": {"ne": "answered"}}`, `order_by="accounts"`. Report the top **[five]**, oldest first within a tie, each with its question, the believed answer if one was recorded, and where it was asked.
- **A gap is not a correction and not an engineering ticket.** It is a missing article someone needs to write. No draft is created for it here, and it never becomes a tracker issue.
- This is the half a release can never surface: a release tells you what changed, and only the people getting stuck tell you what was never written down. Both land on the same person, so both go in the same message.

## 8. Post one message
- <tool:slack_post_message> once per run, to your docs channel, covering every release swept:

```text
Docs after <release>[, <release>] · <n> pages checked

Now wrong (<n>), drafts waiting in Productlane
• <Article> (<visibility>): "<the quoted sentence>"

Incomplete (<n>)
• <Article>: the release added <what>, nothing covers it

Notion, waiting to be pasted (<n>)
• <Page>: the replacement is in the table

Not documented anywhere (<n>)
• <release>: <behavior statement>

Asked and unanswered (<n>), no article exists
• "<question>" (<n> accounts)

Checked and accurate: <n>
```

- The quotes and replacements go in the thread, with `thread_ts` set to the returned `ts`, so the channel message stays readable.
- **A run that found nothing still posts** the checked count and the gaps: a silent run and a broken run must not look alike.
- The app must be a member of the channel or the post fails with `not_in_channel`. A message above about 4,000 characters is split into threaded parts and the response says so in `split_into`: read it before concluding anything was lost, because a message can be deleted but not edited, and a duplicate is worse than a long thread.

## What this never does
- Never publishes, updates, accepts or deletes a help article, and never writes to Notion.
- Never rewrites a page beyond the lines the release invalidated.
- Never turns a documentation gap into an engineering ticket or a new article.

## Output
One Productlane draft per article a release made wrong or incomplete; in the doc updates table, one row per page checked, carrying the verdict, the quote, the replacement and the draft id, plus one release row per release swept; and one Slack message naming the releases swept, what is now wrong, what is incomplete, what waits to be pasted into Notion, the behaviors no page describes, the top unanswered documentation gaps, and how many pages were checked and found accurate. Every `agent`-visibility article touched is flagged by name.