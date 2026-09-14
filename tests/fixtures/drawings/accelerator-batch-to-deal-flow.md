# Add every new accelerator startup to your Attio deal flow

**When to use it**: your team reviews every company in **[the accelerator directory you track]**'s current batch (the Y Combinator directory is the obvious public example), and copying them into the CRM by hand happens late and misses some. This lists the live batches each day, stages every company, and files each one in your Attio deal flow list, deduplicated on its domain and waiting for a person to triage it.

```
              Scheduled routine, daily before the team starts
              "Sync the accelerator's current batch into our deal flow list."
                         │
                         ▼
╔═════════════════════════════════════════════════╗
║  1 · Decide which batches to list               ║   data_rows
║  The newest batch in state and its calendar     ║   no directory page is scraped
║  successor, never below your floor batch.       ║
╚════════════════════════╤════════════════════════╝
                         ▼
┌─────────────────────────────────────────────────┐
│  2 · List each batch by name                    │   apify_run_sync
│  One bounded call per batch, then four gates    │   cost ceiling on every call
│  before the result is trusted at all.           │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ gate failed       whole directory or wrong batch
                         ├───────────────▶  ▪ name refused      flagged, never read as not open
                         ▼  all four gates passed
┌─────────────────────────────────────────────────┐
│  3 · Diff against the staged rows               │   data_rows
│  New, changed and gone companies, compared      │
│  with staging only; the CRM is not read here.   │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Stage, never write the CRM                 │──▶  staging table  one row per company, keyed on slug
│  Upsert by slug; an empty sync timestamp        │──▶  batch state  newest batch, counts, last run note
│  marks what is still owed to the CRM.           │
└────────────────────────┬────────────────────────┘
                         ▼
╔═════════════════════════════════════════════════╗
║  5 · Check each company's select values         ║   attio_attribute
║  A missing batch option blocks that company;    ║   live options, per company
║  a missing industry takes its mapped option.    ║
╚════════════════════════╤════════════════════════╝
                         ├───────────────▶  ▪ blocked           batch option not live yet, retried
                         ├───────────────▶  ▪ no domain         skipped, never fabricated
                         ▼  values confirmed or mapped
┌─────────────────────────────────────────────────┐
│  6 · Write each company into Attio              │   attio_record
│  New: match on domain and file the entry;       │   attio_entry
│  already written: update both by stored id.     │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ domain conflict   new domain on another record
                         ▼
┌─────────────────────────────────────────────────┐
│  7 · Mark the debt paid                         │   data_write
│  Stamp only the rows actually written, with     │
│  their record and entry ids, in one batch.      │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  8 · A human triages the new entries            │   the one human step
│  The team reads each company and sets the       │
│  triage field; the run never writes it.         │
└─────────────────────────────────────────────────┘

▪ terminal — that batch or company goes no further this run; a row still owed is retried on the next
```

Steps 1 to 4 are phase one, steps 5 to 7 phase two. Phase one never touches the CRM and phase two never calls the actor, and that separation is what makes any failed run safe to rerun: the empty sync timestamp is the single record of what the CRM is still owed, so nothing is ever half-written. Phase two runs every day even when phase one found nothing new or a batch failed its gates, because it pays whatever is still owed from earlier runs.

## Before the first run
- **Two tables.** A staging table keyed on the directory's company `slug`, with `batch`, `name`, `website`, `one_liner`, `industry`, `directory_page`, `status` (listed or gone), `first_seen`, `last_seen`, a `synced_at` column that is **empty while the company is owed to the CRM**, a `crm_note`, and four columns step 7 fills: `crm_record_id`, `crm_entry_id`, and `crm_name` and `crm_domain`, the name and domain last sent to the CRM. A batch state table keyed on the full batch name, with the company count, `last_seen`, `refused_since` and `last_run_note`.
- **[Your floor batch]**: the earliest batch that belongs in the CRM. Nothing older is listed, staged or written; older batches stay wherever they were tracked before.
- **The Attio list** **[your deal flow list]** on `companies`, with entry attributes a batch select, a directory page text, an industry multi-select, a one-liner text, and **[your triage field]**, a select owned by people only.
- **The actor.** Pick **[a directory-listing actor for your accelerator]** in the Apify Store and read its input on the Store page once: the exact name and shape of its batch filter, and the field names it returns. A filter passed under the wrong name, a singular where the actor expects an array of full batch names, is typically ignored without an error and returns the entire directory.

## 1. Decide which batches to list
- <tool:data_rows> on the batch state table. Take the **newest batch it holds and its calendar successor** in the accelerator's own cycle (for the YC directory, Winter → Spring → Summer → Fall, rolling the year at Fall → Winter), and drop anything below the floor. Usually two names, sometimes one.
- **"Newest" means highest on the calendar, never the most recently created or updated row.** Sorting on `_updated_at` picks whichever batch was refreshed last, and the run keeps listing an old batch while a new one fills up.
- **No directory page is fetched to find the batches.** Scraping the directory is slow enough to exceed a short tool-call timeout, and the state table plus the calendar already say which names to ask for.

## 2. List each batch by name
- <tool:apify_run_sync> — one call per batch, with the batch filter as an array holding one full name, `max_total_charge_usd` as a hard cost ceiling, and both `max_items` and `limit` set just above **[the largest batch the accelerator has run]**. `max_items` caps billed items only on pay-per-result actors; `limit` caps the list that comes back whatever the actor's pricing, so an unscoped listing never lands thousands of items in the response. `fields` limits the returned columns to the ones you stage; a long description field is heavy and nothing reads it.
- The sync call waits up to 300 s; beyond that Apify answers 408. Then start the same input with <tool:apify_run>, poll <tool:apify_run_status>, and read <tool:apify_dataset_items> on `defaultDatasetId` **only once the status is `SUCCEEDED`**. A dataset read earlier returns a partial batch that passes for a whole one.
- **Four gates before the result is used at all**, size first. Any failure aborts that batch: nothing is diffed or staged, and the reason goes on its state row and in the output.
  1. **Size**: a result that reaches `limit` is the whole directory, capped. A real batch never fills the ceiling.
  2. **Batch**: every returned company's batch equals the one requested.
  3. **Slugs**: present on every company and unique.
  4. **Markers**: none of **[two or three famous alumni from old batches]** appears. They are in the full directory and never in a new batch, which catches an unscoped listing by name even when it came back small.
- If the successor returns at least **[10]** companies, it has become current: upsert its state row. Below that it is still a placeholder.
- **A refused batch name is not "not open yet".** An `invalid-input` error means only that the actor would not accept the name, and there are two causes the tools cannot tell apart: the batch does not exist yet, or the actor lags the accelerator. <tool:apify_actor> returns the actor's card with the date its latest build finished, but not its input fields. So set `refused_since` on the current batch's state row the first time it happens, list the refusal in every run's output until a person has checked the accepted batch names on the actor's Store page, and escalate it as a likely stale actor once it outlasts **[the gap between a batch being announced and its start]** or the latest build predates the announcement. Do not invent the batch, do not switch actors mid-run, and do not create a state row for it.
- **Why this is not a quiet skip**: with no directory scrape, the actor's accepted names are the only signal that a new cohort exists. If refusals were read as "not open yet", every run would exit cleanly, the current batch would keep refreshing `last_seen`, and an entire batch would fill up unseen.

## 3. Diff against the staged rows
- <tool:data_rows> on the staging table, `filter` on the batch, `fields` limited to slug, name, website, one-liner, industry and status. Compare with staging only; **the CRM is not read in phase one.**
- **New**: a slug not staged. **Changed**: a staged slug whose name, one-liner, industry or website differs. **Gone**: a staged `listed` slug absent from today's listing, marked `gone` and never deleted anywhere.
- **A new slug whose website matches a gone slug is a rename**, not one company leaving and another arriving. Step 4 moves the existing row onto the new slug, so it keeps its `first_seen` and its CRM ids.
- Nothing new, changed or gone: refresh `last_seen` and go to phase two.

## 4. Stage, never write the CRM
- <tool:data_write> — one batch with `key="slug"`. `first_seen` is sent only for new slugs, so an upsert never resets it; `last_seen` on every row. A new or materially changed company gets `synced_at` written as `@empty`: an empty string does not overwrite a value already there, and a changed company that keeps its old timestamp is never re-sent.
- A rename is a separate write by the existing row's `id`, because the key cannot match a slug that changed: the new slug and name, `synced_at` as `@empty`, and `first_seen` and the CRM ids left untouched.
- Upsert the batch state rows: count, `last_seen`, and the run note.

## 5. Check each company's select values
- <tool:data_rows> with `filter` `{"synced_at": {"empty": true}, "status": "listed"}` reads the debt. None: stop, the run is done. A row below the floor should never be here; if one is, never write it and never delete it: stamp `synced_at` with `crm_note` = "OUT OF SCOPE: predates the floor batch".
- **Per company, per attribute.** Work out the batch value and the industry array that this company needs: the directory's industry and its sub-industry as two separate values, never one joined string. Diff each against <tool:attio_attribute> `op="options"` (`target="lists"`, your list, the attribute) **and** against the values entries already written use. Treat a schema read as provisional: option lists have been seen to come back truncated with nothing saying so.
- **The batch has no live option**: skip this company, leave `synced_at` empty and set `crm_note` = "BLOCKED: batch '<value>' is not an option yet". <tool:attio_attribute> is read-only and select options are added by a person in Attio; the row retries by itself on the next run once the option exists. **A batch is never approximated**, because a cohort has no nearest neighbor.
- **An industry value has no live option**: look it up in **[your industry mapping table]** (directory value → your closest live option) and substitute it in the array. If it is not there, choose the closest live option, <tool:data_write> one row into the mapping table (directory value → chosen option), and use it: the table exists so the same value resolves identically on every run, and the directory's taxonomy is always larger than the options past batches created. This is a normal write, with no note.
- One company's missing option never holds back another. Never write a value that is neither a live option nor a mapped one, and never drop a value from the array to force a write through.
- **The domain**: take the website, strip the scheme, a leading `www.` and any path. No usable website: skip the company with a note, and never guess a domain.

## 6. Write each company into Attio
Which path a company takes depends only on whether its staged row already holds `crm_entry_id`. A changed company re-sent through the create path would file a second entry, and one whose website changed would match no record and create a second company beside the old one.
- **No entry id yet: create.** <tool:attio_record> `op="search"` on `companies` with `filter` `{"domains": "<domain>"}`. Found: reuse that record, so a company already in the CRM from another source links to it instead of being duplicated. Not found: `op="create"` with `name` and `domains`. `domains` is unique, so a create that fails on a uniqueness conflict means the record appeared in between: search again and reuse it.
- <tool:attio_entry> — `op="create"` on your list with `parent_object="companies"`, the `parent_record_id`, and `entry_values` for batch, directory page, one-liner and **industry as an explicit array**. Keep the record id and the returned entry id for step 7. A response saying the record is already an entry means it was filed some other way and these values did not land: find that entry with `op="query"` filtered on the parent record, keep its id, and send the values through the update path below.
- **Entry id on record: update, never create again.** <tool:attio_entry> `op="update"` with that `entry_id`, `overwrite_multiselect=true`, and only the automation-owned attributes: batch, directory page, one-liner and industry. The default PATCH appends, which leaves the stale industry beside the new one; attributes left out of the payload are untouched.
- On the same path, compare with what the run last sent, never with the CRM, where a person may have edited the record. The staged name differs from `crm_name`: <tool:attio_record> `op="update"` on `crm_record_id` with the new name. The domain differs from `crm_domain`: `op="get"` on that record, swap the old domain for the new one in its domains, and `op="update"` with `overwrite_multiselect=true`, so domains another source added stay and only the stale one goes.
- **A domain update refused as a uniqueness conflict** means another record already holds the new domain. Leave `synced_at` empty with `crm_note` = "CONFLICT: <domain> is on another record" for a person to resolve, and never merge records from the run: a merge cannot be undone.
- **[Your triage field] is never written by the run, not even empty.** It belongs to the people who read the list.
- **Never load the list through a CSV import.** The importer splits multi-select cells on commas, and some directory categories contain commas in their own names. The API takes an array and never splits.

## 7. Mark the debt paid
- <tool:data_write> — one batch with `key="slug"` setting `synced_at`, `crm_record_id`, `crm_entry_id`, `crm_name` and `crm_domain` on **the rows actually written, and only those**. Blocked, skipped and conflicting rows keep `synced_at` empty; rows with a mapped industry are marked normally. A write that failed stays owed and is retried next run: the empty column makes both phases idempotent, and the stored entry id sends the retry down the update path.
- Then check the outcome, not the exit status. <tool:data_rows> with `count_only=true` on owed rows should leave only blocked, skipped and conflicting companies, and the newest `last_seen` on the batch state table should be today. A run can finish without an error and still have listed nothing, so either check failing is reported as a fault.

## 8. A human triages the new entries
The team opens the list, reads each new company and sets the triage field. Views cannot be created through the API, so set one up once in Attio: the list grouped by batch, filtered to the current batch with the triage field empty. That view is the untriaged queue.

**Optional extension**: founder enrichment is left out. Add it only once person records in your CRM have a unique key, such as an email address, and someone reads them, and then only for companies a person has promoted.

## Output
Batches listed and their company counts; any batch that failed a gate, with the gate; any refused successor name and since when; companies new, changed, renamed and gone; companies written to Attio, reusing an existing record, creating one, or updated in place; industries mapped to a closest option; companies blocked on a missing batch option, skipped for no domain, or held on a domain conflict; and the owed count after the run.