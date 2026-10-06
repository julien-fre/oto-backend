## usage — wages by occupation in the united states (bls oews open data)

public source of the Bureau of Labor Statistics, no key. a single tool:
- "how much does a data scientist earn in the US, and in Chicago?" → `bls_oews_wages(soc="15-2051", areas=["US", "IL", "16980"])` — per area: P10, P25, median (`p50`), P75, P90 and mean in **annual** dollars, plus employment
- accepted areas: `"US"`, a State (name or postal abbreviation: `"Illinois"`, `"IL"`), or the **5-digit CBSA code** of a metropolitan area (`"16980"` = Chicago-Naperville-Elgin, `"35620"` = New York-Newark-Jersey City) — ⚠️ a metropolitan area NAME is not resolved
- the occupation is passed by its **6-digit SOC code** (`"15-1299"` or `"151299"`)

## note — ⚠️ what the figure covers, and what it does not say

- **latest published year only**: the API serves no OEWS history; the year is in the result (`year`), to be cited with the figure
- **SOC granularity, not O\*NET**: an 8-digit O\*NET-SOC code (`"15-1299.08"`) is accepted, but its suffix is STRIPPED — the returned wages then cover the whole `15-1299` SOC (an "all other" category that is sometimes very broad), and the result says so in `note`. to be repeated to the user rather than presenting the figure as that of the detailed occupation
- **a missing value is never guessed**: a wage capped or unpublished by the BLS comes back `null`, with its raw form in `raw` (`"-"`) and the reason in `footnotes`; `missing` lists the measures without a series for this occupation × this area (small areas, rare occupations)
- **SHARED daily quota**: without a registration key, the BLS serves 25 requests per day to the whole platform; one request covers 3 areas → group the areas in ONE call (12 at most), never one call per area. exhausted quota = explicit refusal, come back the next day
