# Map every doctor and clinic in a city

**When to use it**: you need every doctor or health facility of one kind in a French city — to size a territory, plan field visits, or find where coverage is thin — and one map search with a scroll through the first page is not a count. This runs a tiled map census, then checks it against the two public registries that between them hold every practice and every facility, so what the map missed is named instead of invisible.

```
              Natural language input in Claude
              "List every general practitioner and every health center in Bordeaux."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Pin the specialty and the city             ║   foncier_geocode
║  The activity code, exact facility labels,      ║
║  commune codes and a center point.              ║
╚════════════════════════╤════════════════════════╝
                         ▼  codes confirmed
┌─────────────────────────────────────────────────┐
│  2 · Census the map listings                    │   serper_maps_census
│  Tile the city into a grid, page every anchor,  │
│  keep only listings inside the city.            │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Count each location once                   │
│  Collapse listings on address and phone, never  │
│  on the practitioner's name.                    │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · List every registered practice             │   fr_stock_search
│  Every active establishment under the code, per │
│  commune code, paged to the end.                │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  5 · List every registered facility             │   sante_finess_search
│  Every health center or clinic in the category, │
│  filtered to the city's codes.                  │
└────────────────────────┬────────────────────────┘
                         ▼  registries complete
┌─────────────────────────────────────────────────┐
│  6 · Match the registries to the map            │   foncier_geocode
│  Registries deduped on SIRET, then matched by   │
│  phone, address, then geocoded address.         │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  7 · Flag what the map missed                   ║   checked by a person, not discarded
║  Registered but never found: a gap list for a   ║
║  person to check, never dropped.                ║
╚═════════════════════════════════════════════════╝
```

## 1. Pin the specialty and the city
A solo or group practice and a health center live in two different registries, each with its own vocabulary. Resolve both before anything is searched — getting either one wrong silently changes what steps 4 and 5 call complete.
- **Practice side: one activity code.** The establishment's declared NAF code — `86.21Z` for general medicine, for example. A neighboring code (`86.22C`, other specialists) is a different census, not a looser version of the same one. The code is what the practice declared, not a diploma, so a few practitioners will sit under a code you didn't expect; that is a limit of the benchmark, stated in the report.
- **Facility side: the exact category labels.** <tool:sante_finess_search> filters `categorie` on a **substring** of the label, and a substring can land on a neighboring category — "Maison de Santé" also matches a psychiatric category whose label starts the same way. Run each candidate label once with a small `limit`, read the `categorie` values that come back, and keep the exact labels (or their `categorie_code`) for step 5.
- <tool:foncier_geocode> with the city name. Take the candidate whose `type` is `municipality` and check its department (a bare name also returns homonymous localities elsewhere). Keep `lat`/`lon` as the census center and `citycode` as the commune code.
- **Paris, Lyon and Marseille are the trap.** The geocoder returns the city-level code, but both registries index those cities per arrondissement (`75101`–`75120`, `69381`–`69389`, `13201`–`13216`). A registry query on the city-level code returns zero rows and no error. Enumerate the arrondissement codes instead.

## 2. Census the map listings
- <tool:serper_maps_census> with `query="[specialty in the local language, e.g. médecin généraliste]"`, `center` from step 1, and `radius_km` large enough that the square covers the city limits. The tool tiles the square into `grid`×`grid` anchors, pages each one up to `max_pages` and dedupes on place id server-side. A single maps query caps out around twenty results and leans toward its anchor point, undercounting without saying so — that is why it isn't used here.
- Run a second phrasing (the specialty term and the way practices label themselves, e.g. "[cabinet médical]") and merge on place id: map categories are self-declared.
- **Check the grid once per city.** Re-run the densest part (the center, a smaller `radius_km`) with a finer `grid`. If it returns place ids the first pass missed, the grid was too coarse — raise it for the whole city. Cost grows with `grid`² × `max_pages`, so start at the default and densify only on evidence; report `credits_used` from the response.
- Keep only listings whose address carries one of **[the city's postcodes]**. The census square spills into neighboring communes, and the registries in steps 4 and 5 are scoped by commune code — a listing outside the city will never find its match and will inflate the map-only count.
- Drop listings whose category is clearly another trade (pharmacies, laboratories, a different specialty). Keep medical-center listings: a practice often appears only under its building's name.
- <tool:data_write> — one row per listing: place id, name, address, phone, website, latitude, longitude, category.

## 3. Count each location once
- Normalize first: phone to national format with spaces and country prefix stripped; address to number + street type spelled out + street name + postcode, uppercase, accents removed.
- **Same normalized address and same phone (or one side has no phone): one location.** A cabinet routinely lists several practitioners under one pin — doctors sharing a secretariat, an associate the listing hasn't caught up with — and deduping on the practitioner's name turns that one location into three or four rows.
- Same address, different phones: keep separate locations and tag them `shared_building`. A medical building houses practices that have nothing to do with each other, and merging on address alone undercounts.
- Keep every practitioner name seen at a location on its row. The registries often withhold names, so the names are for the person who checks the gap list in step 7, not a matching key.

## 4. List every registered practice
- <tool:fr_stock_search> with `naf="8621Z"` (the activity code from step 1), `code_commune` set to each commune or arrondissement code, `active_only=true`, `limit=1000`. The response's `count` is the size of **that page**, not a total: advance `offset` until a page comes back shorter than `limit`.
- Why the SIRENE stock and not the indexed `fr_search`: that one truncates enumeration silently past its cap and is built to qualify companies, not to list every establishment in a commune. The stock has no cap and filters on the establishment's own commune code.
- Rows whose address fields read `[ND]` belong to practitioners who opted out of public diffusion. They count toward the registry total and are marked `address_suppressed`; they can never be matched by address, which is why the report counts them separately.
- Assemble the address from `numero_voie`, `type_voie`, `libelle_voie` and `code_postal`, and key each row on `siret`.

## 5. List every registered facility
- <tool:sante_finess_search> with `q="[department code]"`, `departement="[department code]"` and `categorie` from step 1. A FINESS number starts with its department code, so the department code as `q` enumerates the department rather than searching a name.
- Set `limit` well above the expected count. `count` is what came back, not a total: if it equals `limit`, the list was cut — raise `limit` and run again.
- Keep rows whose exact `categorie` is one of the labels from step 1, and whose commune is in the city: the full INSEE code is `departement_code` followed by the three-digit `commune_code` (for the three cities above, that gives the arrondissement code).
- One run per category label. Each row carries `tel` and `siret`, and both matter in step 6.
- Steps 4 and 5 read independent sources and can run in either order; step 6 needs both finished.

## 6. Match the registries to the map
Work down a fallback chain and record which rung matched each entry:
1. **Registries against each other, on `siret`.** A health center can also be registered under the practice code in SIRENE; without this pass it is counted once per registry.
2. **Phone.** FINESS rows carry a phone; compare it, normalized, with the locations from step 3. SIRENE rows have none, so they go straight to the next rung.
3. **Normalized address**, built the same way as in step 3. Registry addresses abbreviate street types (`R`, `AV`, `BD`) where map listings spell them out, which is why step 3's normalization expands them.
4. **Geocoded address**, for what is still unmatched on either side. <tool:foncier_geocode> with the address and `code_commune` returns a canonical address label, and two spellings of one address converge on the same label. Only a `housenumber` match counts; a `street` or `locality` result is approximate and does not match anything. Geocoding only the residue keeps the call count to the entries that need it.

## 7. Flag what the map missed
- Every addressable registry entry with no match goes on the **gap list**: registered address, SIRET or FINESS number, source registry, and the reason (`no map listing`, `approximate address only`). Nothing is discarded. Some gaps are real practices with no online presence; some are stale registrations for a practice that has since closed. The list is what lets a person tell those apart.
- Map locations with no registry match go on a separate **map-only list**. They are usually a practice registered under another code or at another address, not a census error.
- A person checks the gap list before the census is called complete.
- <tool:data_write> — the final table: one row per location or registry entry, keyed on place id, SIRET or FINESS number, with its source, match rung and gap reason.

## Output
Distinct map locations found; active entries per registry, with the number of suppressed addresses; matches by rung (SIRET, phone, address, geocoded); the gap list with registered addresses; the map-only list; and the census cost from `credits_used`. Coverage is never reported as complete: suppressed addresses cannot be matched, and the report says how many there were.