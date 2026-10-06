## usage — site, parcel & real estate

everything that characterizes a physical **site** in france: geocoding, cadastre, buildings, risks, solar, property prices — open data, no key.
- `foncier_geocode(adresse)` then `foncier_site(op="parcelle")(lat, lon)` / `foncier_site(op="bati")(lat, lon)` — coordinates, cadastral parcel, building footprint and actual CES (site coverage ratio)
- `foncier_icpe(siret=… | code_insee=…)` — classified installations (regime, seveso, ied, dreal inspections)
- `foncier_dvf(op="prix_m2")(code_commune)` / `foncier_dvf(op="comparables_adresse")(adresse)` — €/m² stats and comparable dvf sales
- `foncier_site(op="solaire")(lat, lon, kwc)` — photovoltaic yield

## usage — electricity consumption and large consumers

`foncier_conso_elec` serves PV prospecting just as well as commercial targeting: "industrials in a given sector above a given number of MWh", by département, commune or metropolitan area.

**two grid tiers, and they are not interchangeable.**
- `reseau="distribution"` (default) reads enedis: consumption by address, with the naf division.
- `reseau="transport"` reads odré (rte): the sites connected to the transmission grid, **entirely absent from enedis** — and these are the largest consumers in the country. saint-jean-de-maurienne returns zero addresses in naf 24 at enedis and 1,702,616 mwh on the odré side.
- `reseau="les_deux"` for a list that doesn't lie. the transmission vintage lags by a year: `avertissement_millesime` says so rather than letting two years be summed.

**two grains, and one of them makes sites disappear.** enedis publishes **one row per address AND per naf division**.
- `maille="ligne"` (default, historical behavior) returns the rows as published. thresholding row by row **misses sites** whose divisions are each under the bar but whose total exceeds it.
- `maille="site"` sums the divisions of an address and applies `min_mwh` **after** the sum. this is almost always what you want. each site then carries `naf2_principal`, `naf2_detail` and `multi_naf2`.

**target a sector: `naf2`, not `secteur`.** `secteur` only knows industry / tertiary / agriculture — a hospital and an office tower are the same thing there. `naf2` takes two-digit divisions (`["24","23","86"]`), the grain at which enedis publishes. the transmission tier, for its part, carries no naf code: the sector only arrives after resolution to a siren.

**mwh per year, never gw.** no french open dataset publishes subscribed power: a "2 gw" threshold is not measurable, a "2 gwh/year" threshold is.

**what can't be located is counted, not hidden.** enedis publishes rows without an address — real consumption that can't be attached to any site. they never come out as sites, and surface in `lignes_ignorees` / `mwh_ignores`.

a perimeter (`dept`, `code_commune` or `code_epci`) is mandatory on distribution: with `maille="site"` the threshold can't be pushed to the server. the transmission tier is exempt, it fits in ~1,600 national rows.

## usage — what the meter doesn't tell you

consumption describes a site without qualifying it, and doesn't locate everything. two sources attack the problem from the other end.

- `foncier_dpe(op="tertiaire")(code_commune= | departement=)` — the **non-residential** stock: hospitals, education, offices, retail, catering. enedis tells you HOW MUCH a site consumes, this dataset tells you WHAT the building is — erp sector, shon floor area, energy labels. its coordinates come out **already in lambert 93**, so a row can be matched to an establishment without intermediate geocoding. `sans_position` counts the diagnostics that aren't geocoded: they are never placed at the center of their commune.
- `foncier_beges(siren= | naf= | annee= | obligee=)` — the **declared ghg assessments** (~11,800, of which ~7,000 mandatory). here the key is the **siren**, not the address: the assessment joins directly to the organization, including for sites that no grid locates. ⚠️ the reporting year is not the publication year — an assessment published in 2026 can cover 2015. ⚠️ a missing emission item is not a zero: totals only sum what was declared, and `postes_declares`/`postes_absents` say what they cover. each assessment also carries three things that are not emissions: `contact` — the declared **person responsible for monitoring**, with their title, phone and email, published by ademe (absent = masked at the source by the declarant, not unfindable); `entites_consolidees` — the sirens of the consolidated perimeter, i.e. a **declared** subsidiary→group-head table (neither shareholding nor corporate office); and `electricite` — a consumption in mwh **inferred** from item 2.1 using the average french factor, marked `certitude: infere` and returned with its factor. it is the only public route to a consumption attached to a **named** legal entity: enedis (address) and rte (iris) are both anonymous.

## usage — which SITE of a large account matters

`foncier_icpe(op="emissions", departement= | code_insee= | siret= | annee=)` — the irep register: emissions declared **per establishment**, with siret and coordinates. it complements `foncier_beges`, which covers the whole organization and never says where: here the site is named, hence the address. measured on département 59 — arcelormittal france at 5.995 mt of fossil co2.

⚠️ **89% of the register's quantities read "< threshold"** (56,848 rows out of 64,045 in 2024): the operator declares BELOW the reporting threshold. they come out as `quantite: null` + `sous_seuil: true`, never as zero, and are ranked AFTER the known quantities — they inform, they don't rank.

⚠️ co2 exists under three labels: fossil (the default), biomass, and the total that sums both. reading the total as fossil inflates a site that burns wood.

## usage — the owner of a building

`foncier_proprietaire(code_commune= | siren= | emprise_min= | batiment_groupe_id=)` — the bdnb (cstb) links a building group to the **siren of its owner**, when the owner is a legal entity. it is the only public source that goes from a place to a company **without address matching**: the relation comes from the land registry, it is exact. it works in both directions — `code_commune` + `emprise_min=1000` lists a commune's large buildings with their owner, `siren=` lists everything a company owns. each row also carries the ground footprint, the use, the construction year, the dpe class and the building's **professional electricity and gas consumption** (kwh/year, 2020 vintage).

⚠️ **a missing building is not a building without an owner.** only the legal entities distributed in majic appear there: natural persons — sci in their own name, farmers, craftsmen — are anonymized at the source by the dgfip. a commune sweep therefore never returns all the buildings, and `couverture_partielle` says so on every response.

⚠️ the upstream api serves **10 rows per call**: a `limit` above 10 costs one round trip per slice, counted in `requetes`. `total` is what was RETURNED, never what exists — the source publishes no count.

## usage — going back up from a site to the company

the two tiers return addresses or iris, never a siret. resolution is done with `fr_stock_search` (connector `sirene`) on the insee commune and the **5-character naf subclasses** — sirene can't read a 2-digit division.

⚠️ matching is done on the address of the **establishment**, never the head office: a factory and its head office are commonly hundreds of kilometers apart.

⚠️ no prefilter on the headcount band: it is unfilled for a majority share of the industrial register, including real multi-site targets.

when consumption is masked or the site can't be found, `foncier_icpe(code_insee=…)` gives the commune's classified installations **with their siret** — another route to heavy industrial sites. its `rubriques` say what the site really does, and `rubriques_energie` isolates those whose activity IS a consumption: 2910/3110 combustion, 2920/2921 cooling and refrigeration, 4735/1185 industrial refrigeration. ⚠️ an authorized quantity is in m³ or installed mw, **never in kwh**: it's a magnitude, not a meter.
