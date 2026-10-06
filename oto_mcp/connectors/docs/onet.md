## prerequisite — o*net web services api key

create a free developer account on [services.onetcenter.org](https://services.onetcenter.org) (Sign up), generate a key in My Account, then paste it into oto.
- byo-only: no shared oto key — the key is personal, its terms of use are accepted by its holder
- no hard cap announced, but a 429 when the service is saturated: retry after a short delay

## usage — find a us occupation and read its record

the Department of Labor's occupation reference, in one tool:
- "what is the code for the data engineer occupation?" → `onet_occupation(op="search", keyword="data engineer")` — closest first; a code, even partial, can also be searched (`keyword="15-12"`)
- "what does a civil engineer do?" → `onet_occupation(op="get", code="17-2051.00")` — title, description, job titles actually encountered (`sample_of_reported_titles`), tasks (`tasks`, `limit` widens their number), related detailed occupations (`also_see`)

## note — ⚠️ codes and limits

- an O\*NET-SOC code has 8 digits (`15-1299.08`); its first 6 (`15-1299`) are the SOC code under which US wage statistics are published — several O\*NET occupations therefore share the same wage figures
- not every occupation carries every field: an occupation without tasks returns `tasks: []`, it is not an error
- `full=True` additionally returns the API's navigation links (useless to an agent, removed by default)
- **written from the v2.0 reference manual, not yet exercised live** (no key available at the time of writing): on first real use, report any shape discrepancy
