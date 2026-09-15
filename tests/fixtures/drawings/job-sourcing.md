# Find senior job openings on Google Jobs

**When to use it**: someone is looking for a senior role in a given city or field, and you want live openings found, filtered for real seniority, checked against each employer's recent news, and narrowed to a short list tied to their own background.

```
              Natural language input in Claude
              "Find senior product leadership roles in Berlin and shortlist the best fits."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Search one title per call                  │   serpapi_jobs
│  Run each target title as its own query, and    │   an empty result is never cached
│  widen only the ones that come back empty.      │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Filter for seniority                       ║
║  Drop internships, junior and advisory roles,   ║
║  judged on the posting, not the keyword.        ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ dropped           intern, junior or advisory
                         ▼  a senior role
┌─────────────────────────────────────────────────┐
│  3 · Read the full posting                      │   serpapi_jobs
│  Fetch description and apply options for each   │   details only for roles that passed
│  role that passed, by its job id.               │
└────────────────────────┬────────────────────────┘
                         ▼  one lookup per distinct employer
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  4 · Read the company page       │  │  5 · Check the employer's news   │   serper_search
│  Size, sector and description    │  │  Funding, layoffs or a new leader│
│  from its LinkedIn page.         │  │  since the role was posted.      │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 └───────┬─────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Shortlist the best fits                    │   linkedin_unipile_profile
│  Rank three to five roles, each tied to the     │
│  candidate's own background.                    │
└─────────────────────────────────────────────────┘
```

## 1. Search one title per call
- <tool:serpapi_jobs> op=search with `query` set to one title, `location` set to the city, and `country` and `language` set to the market. **One title per call**: a query that stacks two titles, or a title plus a specialism, comes back empty.
- **Ladder each title from specific to broad**, **[Head of X]**, then **[X Director]**, then **[X]** alone, and step down a rung only when the one above came back empty. A shorter title returns more results.
- **A zero is real.** An empty result is always freshly scraped, never served from cache, so don't retry it with `no_cache`; widen the title instead. A non-empty answer can come from cache: the freshness block in the response gives its age, and `no_cache` is only worth its extra seconds when the list must be current to the hour.
- Put the current year in the query or no year at all, never a past one: a stale year pulls stale postings.
- `max_results` is the cost bound, and pagination is handled for you. Pool every title's results into one list, deduplicated on `job_id` and then on company, normalized title and city, because neighboring rungs of the ladder return the same postings.
- `query` is the parameter to use. The `company` shortcut only searches "company name jobs", which is a different question. If a well-formed query is refused server-side, report it as a bug rather than rewording around it.

## 2. Filter for seniority
- Drop internships, graduate programs, analyst and associate roles, and pure advisory or consulting postings. **Senior-sounding keywords lie**: a large share of results for a deal-making or strategy keyword are internships and analyst roles, so judge the title and the snippet, not the keyword match.
- Keep a role when the posting shows **[your seniority bar]**: a reporting line into the leadership team, ownership of a budget or a team, or experience requirements at your threshold.
- Write down why each role was dropped, so a widened search in step 1 doesn't bring it back.

## 3. Read the full posting
- <tool:serpapi_jobs> op=details with the `job_id` from the search, for the full description and every apply option. Only for roles that passed step 2: a details call on every raw result spends on postings already dropped.
- **Prefer the employer's own careers page among the apply options.** An aggregator repost can outlive the role it advertises.
- Pull the facts the shortlist will cite: scope, reporting line, team size, location and remote policy, and how recently the role was posted.

## 4. Read the company page
Runs at the same time as step 5, **once per distinct employer**, never once per posting: several shortlisted roles often share an employer.
- <tool:linkedin_unipile_profile> op=company with the company name or its LinkedIn slug: headcount, sector, description. Company pages are cached for hours per connected account, but the upstream quota per account is limited, so look up only the employers still in the running.

## 5. Check the employer's recent news
- <tool:serper_search> with kind=news and `tbs` set to the last month (qdr:m), querying the company name: funding, layoffs, a leadership change, an acquisition. **A hiring freeze or a restructuring announced after the posting date is the most useful thing this step can find**, because it means the role may no longer be real.

## 6. Shortlist the best fits
- Read the candidate's background from what they gave you (a CV, a summary), or from their own profile with <tool:linkedin_unipile_profile> op=person.
- Rank three to five roles. Each one gets a fit line tied to the candidate's own background: sector experience, past roles, seniority. **A fit line that would read true for any candidate is not a fit line.**
- Flag anything step 5 found that puts a role in doubt, next to that role, rather than silently dropping it.

## Output
For each shortlisted role:

**Title**, Company (location, how recently posted)
Fit: one or two lines connecting the role to the candidate's own background.
Application link, from the employer's own site where one exists.

Close with a market note: the most senior roles mostly move through headhunters and personal networks, so a job-board search sees only part of the market. Offer to widen the search (other titles on the ladder, nearby cities) or to go deeper on one employer.