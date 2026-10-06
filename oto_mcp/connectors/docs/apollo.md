## prerequisite — get an apollo key

create an api key in the developer/api settings of your [apollo](https://app.apollo.io) account.
- paste it into your oto connectors on `/account`
- the key inherits the credits of your apollo plan
- **only company and people search/enrichment** (below) allows
  a free-tier platform key (daily quota) if you do not set your own — it
  queries Apollo's SHARED database, the same for everyone.
- quota exhausted: `apollo_match_person` refuses by NAMING the counter (`used/limit`) and
  says it resets at midnight — the response of a successful call also carries `platform_quota`
  (`used`/`limit`/`remaining`) so you can stop before the refusal in the middle of a batch. Set your
  own key to lift the limit right away, or for THIS lead: `hunter_email_finder`
  (email) and `kaspr_enrich_linkedin` / `fullenrich_enrich_linkedin` (phone,
  LinkedIn history) — different source, no credit burned on a call that
  would fail anyway.
- **the REVEALS are BYO-only, and for a different reason than the rest: COST.**
  Apollo bills a reveal ON TOP of the match, whereas our platform counter can only
  debit a bare match. This is true of the phone (~9 credits where a bare match costs
  1) as well as personal emails (separate pot, plan-dependent scale — gap not measured,
  so not debitable either). `apollo_match_person` keeps working without your key, it
  simply returns no mobile, no direct dial, and no personal email.
- **contacts, sequences, emails and conversations are BYO-only** — no platform fallback
  on these tools, you need your own key. Not only to write: even listing or
  reading them returns YOUR data (your contact book, your connected mailboxes, the content
  of your sent emails, your call transcripts) — a pooled platform key
  would expose that to any other oto user.
- **the contact tools additionally require a "Master" key** (Apollo → Settings →
  Integrations → API): a standard key authenticates but returns 403 on these three.

## usage — b2b prospecting (companies + contacts)

search and enrich companies and people, and spot hiring signals.
- `apollo_search_organizations` — companies by name, domain, country
- `apollo_search_people` — people by domains, departments, titles, seniorities
- `apollo_match_person` — enriches a person (linkedin url or email = best identifiers)
- `apollo_bulk_match` — **up to 10 people in ONE call**, the form that list
  building uses: a search returns hundreds of obfuscated names, and this is how
  they are revealed (300 people = 30 calls, not 300). ⚠️ **the batch saves
  no credits** — apollo bills per PERSON, exactly like 10 single calls;
  what it saves are calls and the rate limit. reveals (personal
  emails, phones) there require your own key, as in single mode.
- `apollo_job_postings` — active job postings of a company (hiring signal)

⚠️ **the nested company record is lightened by default** on `apollo_match_person` and
`apollo_bulk_match`: tech stack, funding rounds, subsidiaries and keywords weighed
91% of the payload — a single match came out at 60,000 characters and exceeded the output
limit of MCP clients. the name, domain, phone, headcount and industry
stay; `full=True` returns the raw, at the same price. **in a batch**, each record also loses
`employment_history` and `account` (the company record of your apollo CRM): without that, a
batch of 10 came out at ~86,000 characters.

## usage — the reveals (direct phone, personal emails)

⚠️ **the only apollo action that does not return its result.** apollo never returns a
mobile in the response: it verifies it on its side and POSTs it to a url, a few
minutes later. the immediate response only carries a `request_id`.

- `apollo_reveal_phone(webhook_url=…, person_id=…)` — orders the reveal. your own apollo
  key, ~9 credits. `webhook_url` is **mandatory on apollo's side**: it is an
  HTTPS url that YOU control (an n8n or make endpoint, your service) — **oto is not a
  webhook receiver**, and does not see what lands there.
- `apollo_reveal_phone_result(request_id)` — reads back the SAME content, **without a webhook, 0
  credit, for 30 days**. this is how the number comes back to the agent: you do not have
  to read yourself what apollo posted. each record of `people[]` comes out lightened like the
  reveal (employer tech stack, employment history, apollo CRM company
  record removed — the numbers stay); `full=True` returns the whole envelope.
- ⚠️ **polling requires the `webhook_result` permission on your key** (or a
  "Master" key), according to the apollo doc — same family of prerequisites as the contact
  tools. to be verified on a real key: if it does not have it, the reveal still goes out
  and the numbers arrive on your webhook, but `apollo_reveal_phone_result` will refuse.
- ⚠️ **keep the `request_id`** (a table row, the run journal): once lost, the
  credits are spent and there is nothing left to collect. after 30 days, the result
  disappears for good.
- apollo does not sign its callbacks and may replay a send: your endpoint is to be
  treated as unauthenticated, and made idempotent.
- `apollo_match_person(reveal_personal_emails=True)` — the PERSONAL emails, for their part,
  do come back in the response (synchronous), **on your own apollo key** (same cost
  rule as the phone). apollo withholds them for people in GDPR regions: an empty
  result is an answer, not an outage.

## usage — contacts (the people IN your workspace)

- `apollo_contact` (`op=fields|search|get|update`) — read and edit a contact
  SAVED on your side: title, email, phones, stage, lists, custom fields
- `op=search` finds a contact and its `contact_id` (your contacts, not the shared
  database). ⚠️ The other source, often already paid for: `apollo_match_person` carries the
  contact id NESTED at `person.contact.id`, as soon as the person is a contact on your
  side. An id from `apollo_search_people` is a PERSON id and will be refused here.
  The same `contact_id` is then used for `apollo_sequence_contacts(op=add)`
- ⚠️ a **contact ≠ a person**: `apollo_search_people` queries the shared
  Apollo database, `apollo_contact` only sees what your team has already
  saved. A person found but never saved has no contact id
- `op=get` **costs no credit** — it is the way to re-read a contact;
  `apollo_match_person` costs one and returns the shared record, not your values
- `op=fields` first for a custom field write: the payload
  is keyed by field **id**, never by name. For a picklist, the
  value to write is the option `id`, not its label
- `op=create_field` declares a custom field without going through the Apollo
  interface (useful when the account belongs to the client). ⚠️ For a long text —
  a hook, a paragraph — use `field_type="textarea"`: `string` is
  capped at 120 characters and Apollo truncates without saying anything. A field already
  carrying this name makes the creation be REFUSED rather than creating a namesake
- ⚠️ these three calls require an Apollo **Master** key (Settings →
  Integrations → API); a standard key authenticates but returns 403
- ⚠️ `label_names` REPLACES list membership instead of adding to it
- `dry_run` available on `op=update`

## usage — sequences (automated email campaigns)

- `apollo_email_accounts` / `apollo_email_schedules` — read prerequisites (YOUR connected
  mailboxes / send schedules), to call before creating a sequence or enrolling
  contacts in it
- `apollo_sequence` (`op=search|create|update|activate|deactivate|archive`) — manage a
  sequence
- `apollo_sequence_contacts` (`op=add|update_status|activity`) — enroll/remove
  contacts, consult their activity. `add` starts an automated campaign to real
  people — `dry_run` available

## usage — one-off emails (outside a sequence)

- `apollo_email` (`op=draft|send|status|search|content|stats`) — `draft` prepares,
  `send` sends (always two distinct calls). `search`/`content` return YOUR sent
  emails (body included), not a shared database

## usage — conversations (recorded calls/video meetings)

- `apollo_conversation` (`op=search|get|export|export_status`) — YOUR transcripts and
  recordings. The credit cost of a `get` depends on the presence of AI insights,
  unpredictable before the call
