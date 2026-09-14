# Find contacts at accounts showing buying signals

**When to use it**: an account left a review, replied to a form or showed some other signal you record, and it now sits in your CRM with nobody linked to write to, so nobody ever follows up. The CRM already knows which accounts raised a hand and a people search knows who works at each one. The gap between them is mechanical (find, dedupe, link, queue), which makes it a job to run on a schedule instead of a line on a to-do list.

```
              Scheduled routine, weekly
              "Find the right people at every account that showed interest but has nobody on file."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · List signal accounts with no contact       │   folk_group, folk_record
│  The signal segment, minus every account that   │   one people search for the whole segment
│  already has someone linked.                    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · Find candidates for each role              │   linkedin_aiark_search
│  Search each account's domain from its record   │   titles filtered client-side, not by the API
│  and match titles on whole words.               │   a page budget per account
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no domain         no website or work email on the record
                         ▼
╔═════════════════════════════════════════════════╗
║  3 · Keep the most senior per role              ║   current employer checked first
║  One current employee per role, on title fit    ║
║  and seniority, never on result rank.           ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ nobody found      no candidate left for any role
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Get a work email                           │   linkedin_aiark_person
│  One export per kept person, and an address off │   a timeout is not a not-found
│  the account's domain is held.                  │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no email          none found, or at another domain
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Spot people already in the CRM             ║   folk_record
║  Every address is matched before anything is    ║   the full address, never the name
║  created, so a match gets updated.              ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Save each contact to the CRM               │   folk_record
│  Create or update, link to the account, and add │   batches of 50, failed items retried alone
│  to the segment's group.                        │   companies read back after every write
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  7 · Check the campaign holds new leads         ║   lemlist_campaign, lemlist_create_lead
║  A probe lead on your own address shows whether ║   reports read before and after the probe
║  an added lead sends unreviewed.                ║   the probe is deleted either way
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ auto-review       campaign sends leads on add
                         ▼  leads wait for review
┌─────────────────────────────────────────────────┐
│  8 · Queue each contact for outreach            │   lemlist_get_leads, lemlist_create_lead
│  Name, email, company and role into your        │   deduplicated on the address
│  campaign, once each, held for review.          │   reports read again after the batch
└─────────────────────────────────────────────────┘

▪ terminal — the row stops there and nothing further is spent on it
```

## 1. List signal accounts with no contact
- <tool:folk_group> — resolve the id of the group that holds your signal segment once, and keep it. Passing it as `group_id` makes the search list that group's companies only; a search with neither a group nor filters fetches every matching page of a large workspace.
- <tool:folk_record> with `entity="company"` and `op="search"` — the segment in one call, not every company filtered afterwards: this list is re-run on a schedule, so keep it cheap. A standard field narrows in `filters`. A group's custom field, such as a signal status or a review date, comes back under `customFieldValues` keyed by group id, so read it from the result rather than guessing a filter key. `count` reports the real total, and a count above the number of results means the list was cut at `max_results`.
- Then **one** person search for the whole segment rather than one lookup per account: `entity="person"` with `filters={"companies": {"in": [<the segment's company ids>]}}`, since relation fields take `in`. Every company that comes back linked to someone leaves the list. What remains is the run.
- **Probe that filter on the first run before trusting it.** The value shape for a relation filter isn't spelled out, and a shape the search misreads can make every account look contactless, or every account look covered. Run it once with a single company id you know has a linked person and confirm `count` is at least 1 and that the people returned belong to that company; only then read a zero on the full list as "nobody linked".
- **Apply the same truncation check to that person search.** It is cut at `max_results` too (100 by default), and a linked person who didn't come back leaves their company in the "no contact" list, so the run goes looking for people the CRM already has; step 5 only catches that on an exact address. Compare `count` with the number of results, and raise `max_results` or split the id list into chunks until the two match.
- Text filters are **prefix matches, not contains**: `{"name": "Blue"}` finds "Blue Harbor", while `{"name": "Harbor"}` finds nothing. Never read a zero count on a name as proof that a record doesn't exist. The step after "doesn't exist" is a create, which is exactly how duplicates are made.

## 2. Find candidates for each role
- **Take each account's domain from its company record**: the website in its `urls` first, the domain of a work address in its `emails` otherwise, normalized (lower-case, no scheme, no `www.`, no path). A company created in the CRM by hand often carries neither, and a webmail domain on a shared inbox is not the company's. An account with no usable domain ends here as **no domain**, named in the report so someone can add its website, rather than being searched by name.
- Define the two or three roles actually worth reaching for your product, commonly the founder or CEO and whoever owns the function your product touches (marketing, operations, procurement). Write each as a keyword list matched on **whole words**, in every language your accounts use, plus exclusion families: assistants, interns, recruiters and **[adjacent functions that share a keyword with your buyer]**. Whole words stop a short keyword from matching inside an unrelated title.
- <tool:linkedin_aiark_search> with `op="people"` and `account={"domain": {"any": {"include": ["<domain>"]}}}`, a plain list rather than the name-matching wrapper. **Filter on `domain`, never `website`**: the tool refuses `website` because the index would accept it, ignore it without a word and return the whole database as if it were your result.
- **`title` and `department` are refused for the same reason**: the index would ignore them and bill you for the company's first page. Match titles client-side against the role lists; `department.departments` is present on every returned record even though it can't be filtered on.
- `contact.seniority` is a level derived from the title, not the title itself, and it drops the right people wherever titles don't follow the hierarchy: a "Managing Director" does not carry `director`. Use it to cut pagination, never to select. For "works in this country", filter on `contact.location`. `account.location` is the headquarters, and a company headquartered elsewhere returns zero, which reads exactly like nobody matching.
- Every returned record is billed, so `size` is the spend bound. Project with `fields` using the real top-level keys (`id`, `profile`, `link`, `location`, `department`, `company`): an unrecognized key is dropped silently, and the records then look empty rather than wrong. A read timeout is retried once and then raised with `retryable: true`, which is a failure and never an empty page.
- **Page to a budget, and say when the budget is what stopped you.** Give each account **[a page budget]** at **[a page size]** and stop there. Results are not ordered by title, so on a large account the right person can sit on a page past the budget. Log a role unmatched after the last page of results as **not found**, and one unmatched when the budget ran out first as **not found within budget**: the first says nobody holds the role, the second only that this run didn't look far enough, and raising the budget for that account is the fix.
- A role with no match doesn't block the roles that did resolve.

## 3. Keep the most senior per role
- **Check the current employer first.** Keep only candidates whose current company on the record is this account's domain: a domain search can surface people whose position there has ended, or a sister company sharing a group domain. A role whose candidates all fail this check is logged as not found, the same as a role the search never matched.
- Where several plausible people fill one role, don't take the first result, because position in the results is not a reliable pick. Judge on title fit, then seniority. A "Head of Marketing" search that returns a marketing coordinator and a CMO keeps the CMO.
- One person per role. That choice is the difference between a contact worth emailing and one who forwards the message up the org chart.
- An account left with no candidate for any role once this check has run ends here as **nobody found**, named in the report with each role's not found or not found within budget, so the second kind can be searched again deeper.

## 4. Get a work email
- <tool:linkedin_aiark_person> with `op="export"` and the `id` from the search: the synchronous email finder, billed per person, so it runs only on the people kept in step 3.
- `{"found": false}` is an absence, so write not found. An error carrying `retryable: true` is a failure: nobody was looked up, so retry later and **never record a not-found from it**. Exports time out in bursts while search stays healthy, and the one automatic retry can bill a second time when only the answer was lost.
- **Gate the address on the account's domain.** An email at a different domain, such as a side project, a previous employer or a personal address, is held back rather than used. A role whose only address fails this gate ends as **no email**, logged as held for a domain mismatch rather than as not found.

## 5. Spot people already in the CRM
- <tool:folk_record> with `entity="person"` and `filters={"emails": "<full address>"}`. Search on the **whole address** and confirm the returned email equals it exactly: the match is a prefix, so one address can match a longer one.
- Never deduplicate on name. A prefix match on a compound name returns zero for a person who exists, and the create that follows is a duplicate.
- A match means the person already exists, so update that record. No match means create a new one.

## 6. Save each contact to the CRM
- <tool:folk_record> with `op="create"` and `items`, up to 50 records per call. Create takes snake_case field names (`first_name`, `job_title`, `company_id`), while update takes camelCase in `fields` (`jobTitle`). An unrecognized create field is refused rather than dropped, so don't mix the two vocabularies. Run the first batch with `dry_run=true` to see what would be written.
- A bulk call returns a receipt with per-item failures. Retry only the failed items, never the whole batch, or every success is created twice.
- Existing people: `op="update"` with `items`, one `{"id", "fields"}` per record. Only the fields you send change, but a list field you send is replaced whole. To link the account, read the person's current `companies` and send the full list back in camelCase, `{"companies": [{"id": "<a company already linked>"}, {"id": "<the account id>"}]}`: sending the account alone unlinks every other company the person had.
- **Read back each written person's `companies`**, created and updated alike. Folk links a company matched on the email's domain on its own, even when `company_id` is passed, and may create one for it: a duplicate of the account you meant. When the read-back shows a company you didn't send, send `companies` again without it and name both companies in the report, so a person can merge or delete the duplicate. This process never deletes a company itself.
- `op="add_to_group"` with `ids` (up to 50) and the segment's group, so every contact carries the segment its account came from and the roster stays traceable to why it exists. Existing group memberships are preserved.

## 7. Check the campaign holds new leads
- <tool:lemlist_campaign> with `op="reports"` and `campaign_ids=[<your outreach campaign id>]` — read `totalCount`, `reviewedCount` and `inSequenceLeadCount` before adding anyone. Together they show the review lock without sending anything: a lead held for review raises only `totalCount`, while a lead the campaign launches as it is added raises the other two as well. Neither the campaign record nor a lead's `isPaused` tells the two apart.
- **Whether an added lead waits is the campaign's setting, not this process's.** A lead is held only while the campaign reviews leads before sending. With auto-review on, every lead added sends on creation, and this process adds leads to a campaign you supply, which may already have it on.
- <tool:lemlist_create_lead> — **one probe lead first, on [an internal address you own]**, never on a prospect, with `deduplicate=false` so an address already used elsewhere isn't skipped (delete any probe an earlier run left behind first). Then read `op="reports"` again. Trust the reading only once `totalCount` has risen by one; until it has, read again rather than take unchanged counts as a pass.
- If `reviewedCount` or `inSequenceLeadCount` rose with it, **the campaign auto-reviews**: add nobody, and put it first in the report with the campaign named. Your own address is what makes the probe safe: the one message that may leave lands in your inbox, not a prospect's.
- <tool:lemlist_lead> with `op="delete"` on the probe, whatever the answer, so it never stays in the campaign as a lead.
- Probe on every run, not once: a campaign's settings can change between runs without notice. A campaign that auto-reviews stays that way until its owner changes it in Lemlist; this process never switches `lemlist_campaign_auto_review`, in either direction.

## 8. Queue each contact for outreach
- <tool:lemlist_get_leads> — read the leads of **[your outreach campaign]** once at the start of the step and build the set of addresses already in it, so the check happens before adding, not after. This read includes leads in every state; a listing left on the default state filter comes back empty and reads as "no leads".
- <tool:lemlist_create_lead> — every resolved contact not already in the set, new or updated, with at least `email`, `first_name`, `last_name`, `company_name` and `job_title`, the role and the signal in `custom_variables`, and `deduplicate=true` so an address already live in another campaign is skipped as well.
- **The lead lands held for review, because step 7 showed on this run that the campaign holds new leads.** Creating it sends nothing: a person reads the queued leads in Lemlist and releases them. Only a process that explicitly owns sending calls <tool:lemlist_launch_lead>, once per lead id, which is the actual send and is hidden by default; this one hands that decision to a person.
- <tool:lemlist_campaign> with `op="reports"` after every **[chunk of leads]** and once the batch is in — `totalCount` should rise by the number of leads added and the other two not at all. A rise in either of the last two means the campaign changed during the run: stop adding, and put it first in the report with the leads added since the last clean reading, since those may have sent.
- Never turn on `lemlist_campaign_auto_review`. With it, every lead this step adds sends on creation, and a contact found this morning gets an email nobody read.

## Output
Report, with anything about the campaign first: whether the probe showed the campaign holding new leads for review, or auto-reviewing so that nobody was added; then accounts in the segment and how many had nobody linked, which accounts had no usable domain, candidates found per role, which accounts came up empty for which role, split into not found and not found within budget, emails found, not found and held for a domain mismatch, contacts created versus updated in the CRM, any duplicate company Folk linked and was unlinked from, leads added and held for review versus launched, and leads skipped as already in a campaign.