# Qualify accounts by their number of locations

**When to use it**: one of your qualification criteria is how many locations an account runs (stores, restaurants, clinics, branches), and today a rep either trusts the first number a search returns or guesses. This looks for a published figure first and checks it for scope and freshness, counts the locations on the map over an area a person names only when no figure holds up, cross-checks that count against open map data, and returns a verdict with its source attached.

```
              Natural language input in Claude
              "How many locations does this fitness chain run nationwide, and does it clear our threshold?"
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Look for a published figure                │   serper_search
│  Press, investor and about pages first, and     │   serper_scrape
│  the page read for its scope and date.          │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  2 · Check scope, date and the line             ║   a passing figure goes straight to step 6
║  A dated figure for the right market passes;    ║   group totals and old announcements fail
║  one near the threshold needs a second source.  ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · If nothing passed, fix the area            │   human: the person running it picks the area
│  Only then does a census run, over a country,   │
│  region or city a person names.                 │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Count the locations on the map             │   serper_maps_census
│  Tile the confirmed area, then keep only the    │
│  listings that really are this brand.           │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · Cross-check with OpenStreetMap             │   osm_pois
│  Count the same brand over the same area from   │
│  open map data, and compare the two.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  6 · Return the count and the verdict           │   counts that straddle the line go to a person
│  The number, its source, date and scope, then   │
│  qualified or not against your threshold.       │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ disqualified      below your threshold
                         ▼  at or above your threshold
┌─────────────────────────────────────────────────┐
│  7 · Hand the account on                        │
│  Only a qualified account moves on, so no       │
│  research time goes to one below the bar.       │
└─────────────────────────────────────────────────┘

▪ terminal — the account stops there on this criterion
```

## Before the first account
Write two things down once, because every verdict below is measured against them:
- **[threshold]**: the location count an account needs to qualify, and the market it is counted in (one country, a set of regions).
- **[scope rule]**: what counts as a location for you. Company-owned sites only, or franchised ones too; points of sale only, or offices, warehouses and pickup points as well. A figure or a count built on a different rule is a different number, even when it looks like an answer.

## 1. Look for a published figure
Someone has often published the number already, and a published figure costs a search, where a census costs a grid of map queries.
- <tool:serper_search> with `kind="news"` on "[brand] number of locations" and the same phrase in the market's language ("[brand] nombre de points de vente" for a French chain), with `tbs="qdr:y"` to keep to the last year. Openings, closures, acquisitions and results announcements are where chains state their count.
- <tool:serper_search> with `kind="web"` and `site_filter` set to the company's own domain for its about and investor pages, then once on `wikipedia.org`. Set `country` and `language` to the account's market on every call: both default to one market, and a query run in the wrong one returns the brand's figure for the wrong country without saying so.
- <tool:serper_scrape> the page a candidate figure came from, using the URL exactly as the search returned it, to read the sentence around the number: "in France", "worldwide", "including franchisees", "at the end of [year]". A snippet drops exactly those qualifiers. A URL built by hand from a company name costs a full timeout, and a timeout is not retried; a 200 with an almost empty body means the page renders in the browser, not that the figure is absent.
- Keep, per candidate: the number, the exact phrase around it, the URL, the publication date and the date the figure refers to. The two dates are often a year apart.

## 2. Check scope, date and the line
A figure passes only when all three hold, and a figure that passes goes straight to step 6: no census runs.
- **Scope.** The figure must cover the market your **[threshold]** is counted in, under your **[scope rule]**. A group or worldwide total, or a count that folds in franchisees you exclude, is a different number. It looks like a real answer and qualifies the wrong thing.
- **Date.** Use the date the figure refers to, not the article's publication date. A figure older than **[freshness window]** does not pass on its own: chains open and close sites all year.
- **The line.** A figure within **[margin]** of your threshold needs a second, independent source before it decides anything. Two articles repeating the same press release are one source, not two: check that the second one does not quote the first.

When nothing passes (no figure, the wrong scope, too old, or a near-threshold number nobody corroborates), the account goes to the census.

## 3. If nothing passed, fix the area
- A person names the area to count: a country, a region, a city. Ask before running anything. Never infer it from where the account's head office sits: a census over an area nobody chose returns a count of nothing in particular.
- The area also sets the cost. <tool:serper_maps_census> spends roughly grid² × `max_pages` map queries per call, and each map page is billed at a higher rate than a web search. A city fits in one call with `center` and `radius_km`. A country does not fit in one square: pass explicit `ll_anchors` over the regions where the brand operates, or run one census per region and merge the results. Start with a modest grid, and tighten it only where a region looks under-covered.

## 4. Count the locations on the map
- <tool:serper_maps_census> with `query` set to the brand name as it appears on its listings, `country` and `language` set to the market, over the area from step 3. It tiles the area into anchors, pages each one and dedupes by place id on the server, so a location seen from two anchors counts once.
- Never use `serper_maps_sample` for this. It caps at about twenty results, leans toward its anchor point and **undercounts silently**: twenty found where sixty exist, with no error and no total.
- **Filter `places[]`; don't report `count`.** `count` is every listing the query matched, not every location of the brand. Keep a listing only when its name is the brand's (normalized for case, accents and a trailing city name), and drop other brands that share a word, retailers that merely stock the brand, listings marked permanently closed, and anything your **[scope rule]** excludes (a head office, a warehouse). Count the filtered list.
- Keep `credits_used` from the response with the result: it is what that census actually cost, and the next account's grid can be sized from it.
- When the area spans several calls, merge on the place id before counting, never on name plus address: two anchors spell the same address two ways.

## 5. Cross-check with OpenStreetMap
- <tool:osm_pois> over the same area with `selector="brand=[brand name]"`, falling back to `name=[brand name]` when the brand tag returns little. Give exactly one area: `commune` (a five-digit INSEE code) or `departement` for a French area, `bbox` anywhere else. Set `limit` above the count you expect; the default is a few hundred, so a national chain would come back cut short.
- Read the comparison, don't average it. Open map data is strong on infrastructure and uneven on consumer retail, so a lower count there is expected and proves nothing. A **higher** count there than on the map means the census missed ground (a grid too coarse, a region never anchored) or the step 4 filter was too strict. Look at the difference before trusting either number.
- Two independent counts that agree within **[margin]** are the confidence signal worth having before a qualification decision.

## 6. Return the count and the verdict
- One result per account: the count, the method (published figure, or census plus cross-check), the source URL or the census query and area, the date the figure refers to, the scope it covers, and the verdict against your **[threshold]**: qualified or disqualified, with the reason in one line.
- Two counts on opposite sides of the threshold, or a gap from step 5 nobody could explain, come back as **borderline** for a person to decide. The run never picks the more convenient number.
- **Exit ▪ disqualified**: below your threshold. The account stops there on this criterion, and nothing further is spent researching it.

## 7. Hand the account on
Only a qualified account moves on to the next stage of your qualification, so no research time is spent on an account below the bar. The count goes with it, with its source and scope, so whoever picks it up can defend the call on a borderline account without redoing the search.

## Output
Per account: the count, how it was obtained, the source and its date, the scope, and qualified, disqualified or borderline, plus what each census cost in credits and any account where the two counts disagreed.