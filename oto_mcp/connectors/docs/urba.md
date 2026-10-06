## usage — urban planning & territory

the regulatory and territorial envelope of a point or a commune — open data, no key. geocode the address first (`foncier_geocode`).
- `urba_zonage(lat, lon)` — enforceable plu/plui zoning (géoportail de l'urbanisme), with the pdf regulation if available
- `urba_risques(code_insee)` / `urba_argiles(lat, lon)` — natural/technological risks and clay shrink-swell hazard
- `urba_qpv(code_insee)` / `urba_qpv_proximite(lat, lon)` — priority neighbourhoods (quartiers prioritaires de la ville)
- `urba_epfif(code_insee)` / `urba_socio(code_insee)` — epfif sectors (île-de-france) and insee socio-demographic profile

## usage — who decides, who answers, on a public target

at a commune or a public body, the decision-maker is an **elected official** and the contact a **department**: neither is a sirene executive, and paid enrichment does not find them.

- `urba_annuaire(op="maires", code_commune= | departement=)` — the mayor, with their start-of-term date (useful to know whether the contact has changed since the last campaign). `op="presidents_epci"` + `siren=` (the epci's) for an intercommunality — only the president is returned, not the thousands of community councillors. the date of birth and sex, present in the source file, are **not** returned. match on the insee code, never on the name: "sainte-marie" exists dozens of times.
- `urba_annuaire(op="services", code_commune= | siren= | type_service=)` — public services (~36,000 town halls, prefectures, ddfip…) with switchboard, email and `responsables`: the **named head**, their title, often their direct email. a field the source serialized badly comes out in `champs_illisibles`, not as a silent blank. ⚠️ served by opendatasoft: egress from the prod box was verified on 11/09/2026.
