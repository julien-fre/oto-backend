## prerequisite — an Ubersuggest account

oto works with **your own** Ubersuggest account: you sign in with Ubersuggest and authorize oto.

- what comes back, and how much, depends on **your plan**: a free account gets shortened lists (the answer says what is `hidden_by_plan`) and only its three newest keyword lists
- site audits and Content Studio articles need a **paid plan**; an article spends **100 monthly credits** — the agent asks you before writing one
- the connection lasts until you remove it or Ubersuggest revokes it; if it lapses, the card says « reconnect »

## setup — nothing to configure

no application to declare: the first connection on an instance registers oto with Ubersuggest by itself, on this callback URL:

{{callback:/api/ubersuggest/oauth/callback}}

the resulting client id is kept at platform scope (`connector="ubersuggest"`, `key="client_id"`). To force a new registration (after the instance changes address), clear it: `oto_admin_connector_setting(op="clear", connector="ubersuggest", key="client_id")`.

## usage — keywords, domains, backlinks, audits

- `ubersuggest_account(op="status")` first: who is signed in, on which plan. `op="locations"` finds the `loc_id` of a country or city (2840 US, 2250 France)
- `ubersuggest_keywords` — volume, CPC, difficulty, intent (`overview`), ideas (`match`, `suggestions`, `google`), the SERP (`serp`)
- `ubersuggest_domain` — a site's traffic and ranking keywords (`overview`, `keywords`, `top_pages`), its competitors, one page's keywords
- `ubersuggest_backlinks` — backlinks, referring domains, anchors, and `opportunity`: domains linking to competitors but not to you
- `ubersuggest_site_audit` — `start`, then `status` until done, then `results` per issue; `pagespeed` for Core Web Vitals
- `ubersuggest_keyword_lists` and `ubersuggest_projects` — your saved lists, rank-tracking projects, and Content Studio articles

arguments the tools do not type go in `params`, under Ubersuggest's own names; an argument a call does not take is refused, never dropped.

## note — what is not exposed

deleting a keyword list, the guided project onboarding and the AI-visibility setup stay in Ubersuggest's app.
