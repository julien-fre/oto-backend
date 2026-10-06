## prerequisite — posthog api key

create a **personal key** in PostHog (Settings → Personal API keys — see the [API docs](https://posthog.com/docs/api)), then paste it into oto.

- ⚠️ **it is NOT the JS snippet key.** PostHog puts forward the **project** key `phc_…` (the installation and ingestion one): the read API refuses it, with a `401` impossible to tell apart from a dead key. you need the **personal** key, which starts with `phx_`. oto refuses a `phc_` on entry rather than leaving you to hunt for the cause
- **scopes**: the key carries the permissions chosen at its creation. the useful minimum here is `query:read` + `project:read`; add `insight:read`, `person:read`, `event_definition:read`, `property_definition:read`, `cohort:read`, `feature_flag:read`, `session_recording:read`, `annotation:write` depending on what you want to do. a key missing a scope authenticates just fine and fails **on the first real call** — so the "test the connection" button exercises a real query, not just the identity
- **region**: `https://us.posthog.com` and `https://eu.posthog.com` are two **distinct** deployments. a key from one is unknown to the other, and here again the symptom is a `401`. pick your project's (or your self-hosted instance's URL)
- **project**: optional. left empty, oto discovers it from the key. fill it in to pin the key to a specific project when it sees several
- byo-only: no shared oto key. this is your product data

## usage — queries, persons, accounts, insights, flags, recordings

- "how many signups last week?" → `posthog_query(hogql="SELECT count() FROM events WHERE event = 'signup' AND timestamp > now() - INTERVAL 7 DAY")`
- "which events exist on our side?" → `posthog_schema(op="events")` — do this **before** writing a query; `op="tables"` then `op="columns", table="events"` for the schema
- "our activation funnel, but over last week" → `posthog_insight(op="list")` to find it, then `posthog_insight(op="run", insight_id=…, date_from="-7d")` — the figure returned is **the dashboard's**, computed by PostHog
- "a funnel we haven't built yet" → `posthog_query(query={"kind": "FunnelsQuery", …})` — above all **not** hand-written HogQL for a funnel (see the note below)
- "who is this user?" → `posthog_person(op="list", search="alice@acme.com")` then `op="get"`
- "which customers are dropping off?" → `posthog_group(op="types")` then `op="list"` — **account**-level questions cannot be answered with persons
- "which feature flags are active?" → `posthog_flag(op="list")`
- "show me sessions where people get stuck" → `posthog_recording(op="list", date_from="-7d")`
- "note that v2.3 shipped today" → `posthog_project(op="annotate", content="v2.3 in production")`
- "this number surprises me" → `posthog_project(op="current")`: which project, which account, which region answered — it is the most frequent explanation

## note — funnels and retention: do not rewrite them in SQL

PostHog's funnel semantics (ordered or unordered steps, conversion window, exclusion steps, attribution) cannot be faithfully rebuilt in HogQL. a hand-written query will return a **plausible** number, and it will disagree with the one your team reads in PostHog — the worst outcome, because nothing signals the error.

two correct routes, in this order:
1. the insight already exists → `posthog_insight(op="run", insight_id=…)`, optionally with `date_from`/`date_to` to change the window. the definition comes from your team, the computation from PostHog
2. otherwise → `posthog_query(query={"kind": "FunnelsQuery" | "RetentionQuery" | "TrendsQuery", …})`, which makes PostHog compute

free HogQL remains the right route for everything else: counts, breakdowns, joins, ad hoc questions.

## note — hogql dialect

it is ClickHouse SQL with PostHog accessors:
- properties: `properties.$browser`, `person.properties.email` — no `JSONExtract`. values are **strings**: comparing a number requires `toFloat(properties.amount) > 10`
- the event-name column is `event` (not `event_name`); time is `timestamp`, filtered by `timestamp >= now() - INTERVAL 7 DAY`
- unique users = `uniq(person_id)` — **never** `count(distinct distinct_id)`, which counts devices
- usual joined tables: `events`, `persons`, `sessions`, `groups`. `posthog_schema` lists them all

a query without `LIMIT` is bounded to 101 rows by PostHog, with `hasMore` true: aggregate in the query rather than paginating.

## note — what this connector will never do

create, edit or **toggle** a feature flag, write an insight or a cohort, delete a person or a recording, send events: none of these operations exists in the underlying library. toggling a flag changes the product for real users, and deleting a person is irreversible and regulated — it is not an assistant's decision. do them in PostHog.

the **only** write is the annotation: a dated marker placed on your graphs, purely additive, which modifies no measurement.

## note — verified live on 2026-08-22

tested against a real PostHog Cloud US project: identity, project discovery, HogQL, typed queries, re-running a saved insight, schema (156 tables, `events` at 52 columns), the 14 resource families and annotation writing respond as coded. three shapes that cannot be deduced from the docs and that are handled here: `groups_types` returns a **bare** list (no `results` envelope), `/events/` and `/persons/` carry **no** `count` (never announce a total from a page — go through a query), and the raw `/query/` response is **93% internal diagnostics** (generated SQL, modifiers, cache keys), reduced here to the columns, types, results and the query actually executed.
