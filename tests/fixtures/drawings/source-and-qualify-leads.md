# Find, qualify and add contacts to Salesforce

**When to use it**: a rep names one target account and wants the right people at it, checked against your ideal customer profile and landed in Salesforce as Contacts with email and mobile filled in. It has to be safe to run again next week on the same account without leaving a single duplicate behind.

```
              Natural language input in Claude
              "Source the ten best contacts at this target account and push them to Salesforce."
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│  1 · Resolve the company to one entity          │   linkedin_aiark_search op=companies
│  Strip any country qualifier, filter on domain, │   never filter on website
│  and keep the head-office entity with staff.    │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no entity         showcase page, no indexed staff
                         ▼  one entity with real staff
┌─────────────────────────────────────────────────┐
│  2 · Page through the seniority pools           │   linkedin_aiark_search op=people
│  Senior, then managers, then an execution pool  │   title filter refused: sort client-side
│  only on a small company or a thin yield.       │
└────────────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────┐
│  3 · Exclude first, then rank                   │   data_write, data_rows
│  Exclusions run before the keyword match, then  │   keyed on company and profile URL
│  each person gets a tier and a one-line reason. │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ out of profile    excluded role, or off head office
                         ▼
┌─────────────────────────────────────────────────┐
│  4 · Human validation of the shortlist          │   a human signs off
│  A person keeps or drops each row; unattended   │
│  runs auto-validate, more conservatively.       │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ rejected          dropped before the push
                         ▼  validated rows only
┌─────────────────────────────────────────────────┐
│  5 · Dedup against Salesforce, then create      │   salesforce_query, salesforce_record
│  Two batched queries, a normalized key cascade, │   never creates an Account
│  then one bulk create of the new contacts.      │
└────────────────────────┬────────────────────────┘
                         ├───────────────▶  ▪ no profile URL    never pushed, only reported
                         ├───────────────▶  ▪ likely duplicate  flagged, never created or merged
                         ▼  in-scope fields the CRM does not already hold
                 ┌───────┴─────────────────────────────┐
                 ▼                                     ▼
┌──────────────────────────────────┐  ┌──────────────────────────────────┐
│  6 · Find the missing emails     │  │  7 · Find the missing mobiles    │   lusha_search_and_enrich, kaspr_enrich_linkedin
│  The whole cohort in one         │  │  Batches of 100 keyed on profile │
│  Dropcontact batch, then poll.   │  │  URL and name, then a backup.    │
└────────────────┬─────────────────┘  └────────────────┬─────────────────┘
                 │                                     │
                 └───────┬─────────────────────────────┘
                         ▼  both batches back
┌─────────────────────────────────────────────────┐
│  8 · Write back once and stamp the Account      │──▶  Salesforce  empty Email and MobilePhone only
│  Residual emails go to the backups first, then  │──▶  Account  a short recap, overwritten each run
│  one bulk update that fills empty fields, one   │   salesforce_record op=bulk_update
│  batched table write, one plain recap line.     │   lusha_search_and_enrich, kaspr_enrich_linkedin  residual emails
└─────────────────────────────────────────────────┘

▪ terminal — that row stops there; a run that validates nobody still gets its reason stamped on the Account
```

**One shared table, one status per row.** Every company's candidates live in one table, **[your prospecting table]**, keyed on `<company slug>::<profile URL>`: the company name lowercased, accents stripped, anything non-alphanumeric collapsed to an underscore. The same person sourced for two companies never collides, and a re-run on the same company updates rows instead of adding them. Every row carries its company, and every read and write filters on it. Check that the table exists with <tool:data_get_schema>, not by listing every table in the workspace, which returns the whole catalogue to answer a yes-or-no question. Only when it does not exist, create it with <tool:data_create_datastore> and declare its schema **once** with <tool:data_set_schema>: the row key as the business key, and a lifecycle on the status column. `sourced` goes to `validated` or `rejected`; `validated` to `pushed`, `duplicate_suspected` or `rejected`; `pushed` to `enriched` or `enrich_failed`; and `enrich_failed` back to `enriched`, so the retry sweep in step 7 can heal a row. `rejected`, `duplicate_suspected`, `enriched` and `enrich_failed` are terminal. Any later change goes through <tool:data_patch_schema>, which merges by key: <tool:data_set_schema> replaces the whole schema, and a second call on a table in use drops whatever it does not restate, the business key and its unique index included. The lifecycle refuses any transition it does not declare, so when step 3 writes a page on a re-run, only keys the table does not hold yet get a status; an existing key is written without one, and the upsert refreshes its profile fields while its status stays where the last run left it. Rows already sourced today for the same company are reused, so an interrupted run resumes instead of paying twice.

The run can start from a conversation or from a button on the CRM record. When the trigger passes the Account Id and the company's profile URL, both are used as given: the entity is resolved from the URL, and the Account lookup in step 5 is skipped.

## 1. Resolve the company to one entity
- **Read the input before searching.** A trailing two-letter country code or country name separated by a space ("Acme UK") is a country qualifier, not part of the name: strip it and carry the country into step 2. When the tail is ambiguous, or the run is unattended and there is no one to ask, keep the whole string as the name rather than guess a split.
- <tool:linkedin_aiark_search> — op=companies with `account` holding the name in the `SMART` matcher, or, when you know the website, `domain` as a plain list (the `SMART` wrapper is for names only). Never filter on `website`: the index accepts that key and ignores it, returning its whole company database as if it were your filtered result, which is why the tool now refuses it outright.
- **Vet before spending on people.** Discard any candidate whose `staff.total` is zero, or that is clearly a showcase page rather than the operating company: it returns no indexed people and the round-trip is wasted. Prefer the head-office entity with substantial staff. If that entity then returns nobody in step 2, retry its `sub_organizations` before giving up. Keep the entity id, its staff total (step 2 and step 3 gate on size) and its head-office city (step 3 judges attachment against it).
- No usable entity ends the run here, with the reason stamped on the Account as in step 8.

## 2. Page through the seniority pools
- <tool:linkedin_aiark_search> — op=people with `account` filtered on the entity **id**, and `contact` on `seniority` and `location`. **Never filter people on the domain** when the company runs branches, stores or franchises: every one of them shares the parent's domain and the results flood with local staff.
- `title` and `department` are refused, because the index accepted them and silently ignored them, billing you for the company's first page. `seniority` is normalized from the title itself, so use it to shorten pagination, never as the selection. The title filter is the client-side sort in step 3.
- `size` 100 and **every page** until `totalPages`, since the right people are often past the first screen. Project with `fields` on real top-level keys only (`id`, `profile`, `link`, `location`, `department`) and leave out `company`, the block repeated identically on every person. A key the index does not recognize is dropped silently, and records come back looking empty rather than wrong.
- Three pools, in order. **Senior** (c-suite, VP, director, head) for economic buyers and decision-makers. **Managers and seniors** for champions: usually much larger, so page it fully, and above **[about 3,000]** people record any sampling as a partial sample. **Execution**, the remaining levels, only when the company is small (**[a few hundred staff]**) or the first two pools produced fewer qualified people than you asked for. On a large company that pool runs to tens of thousands of profiles, mostly outside your buying center, so skip it and record "execution pool skipped (large company)". Read the seniority values actually present on a sample of this company's records before passing any: a made-up label is accepted and ignored.
- `contact.location` means "works in this country". `account.location` means the headquarters, and a company headquartered abroad returns zero with it, which looks exactly like "nobody matches".
- **A missing obvious buyer is a mechanical failure, not a scoring one.** When the economic-buyer tier comes back empty at a company that visibly has that function, rerun the senior pool without `location` before concluding anything (a group-level head is often attached to another country), then check for broken pagination, then for the wrong entity in step 1. Record the outcome as the pool's completeness.
- A read timeout is retried once and then raised as retryable; that retry can bill a second time.

## 3. Exclude first, then rank
- <tool:data_write> — sort each page as below the moment it is parsed, then write it immediately as one batch, `key` set to the row key: new candidates as `sourced`, new excluded titles as `rejected`, and a key the table already holds with no `status` at all, each row carrying the company, the sourcing date and the profile URL. People matching no keyword at all are not written. A 100-person page is far too large to carry between steps in context, and a local file does not survive a reset.
- **Exclusions run before inclusion keywords, never after.** The keyword list is inclusive and permissive, so a keyword present never qualifies a title on its own: "operations" matches the head of operations you want and the IT operations engineer you don't. The order is: (1) the functions you exclude, **[the roles that use or report on your category but never buy it]**, rejected at any level; (2) if you sell to head office, anyone below it, **[regional, area, store or franchise titles]**, with one exception: a multi-country zone role (EMEA, LATAM) is a head office of its own and stays; (3) **[contract types with no buying power, such as interns and apprentices]**; (4) only then the inclusion keywords, in every language the company hires in.
- **Match exclusion patterns on whole words**, case- and accent-insensitive, with feminine and plural forms. As a raw substring, a short pattern like "IT" rejects every digital, security and community role in the pool, silently. A qualifier beats a function word ("internal" outweighs whatever it is attached to), and a mixed title stays in when its wider scope is in your profile.
- **When you sell to head office, attachment counts as much as function.** Judge it on the profile's city against the head-office city from step 1, a territory word in the title (regional, area, district, a city name) and the headline. A current employer in the headline that is not the target means a leaver or a freelancer: drop them.
- Rank the rest as economic buyer, decision-maker or champion, aiming at a realistic blend (**[1-2 / 3-4 / 4-5]**) rather than the largest headcount, with a one-line reason each. **Never pad the shortlist with an excluded profile to reach the number you asked for.** A shorter list is the expected result; padding with head-office execution profiles when the upper tiers are thin is fine.
- Rejected rows keep `rejected` and a reason naming the exclusion that caught them, so the call stays readable in the table. Read everything back with <tool:data_rows> filtered on the company, never from a file.

## 4. Human validation of the shortlist
- **With a person in the conversation**: present the ranked list (name, title, reason, profile URL, no blank URLs), or an inline view with <tool:data_app> filtered on the company, with a completeness line per tier: fully paginated, partial sample, execution pool run or skipped. Wait for the sign-off, then write `validated` or `rejected` on each row in one batch.
- **Unattended** (a CRM button or a webhook): there is nobody to wait for, so apply the ranking directly and be more conservative than a person would. Only rows clearly in profile, attached to head office and tenure-checked are validated; anything borderline **on function or on attachment** is rejected. That caution does not extend to seniority: a head-office execution profile validates normally. Append "auto-validated, no human review" to each validated row's reason.
- **Nothing validated**: skip steps 5 to 7 and go straight to the Account stamp in step 8 with the reason.

## 5. Dedup against Salesforce, then create
- One <tool:data_rows> read of this company's validated rows. **No profile URL, no push**: those rows are listed by name in the report and never reach Salesforce. Then collapse the batch against itself on the hard keys below, because a bulk create compares its records only with what is already in Salesforce, never with each other, so two identical rows in one call are both created and both report success.
- **The Account**: the Id the trigger passed, or one <tool:salesforce_query> per company group (`SELECT Id, Name, BillingCountry FROM Account WHERE Name LIKE ...`). The run never creates an Account. With a person present it asks: create it, point to another, or skip the group. Unattended, it skips the group and reports it. Keep `BillingCountry`, which the mobile normalization needs.
- **Two batched queries, then match in memory**, because normalization cannot happen in SOQL. First, every Contact on the target Accounts (Id, names, title, Email, MobilePhone, Phone, **[your LinkedIn URL field]**, AccountId, **[your source flag field]**): that is what catches an existing record whose profile URL is empty or malformed. Second, globally, Contacts whose Email, MobilePhone or profile URL field is `IN` the batch's own values, with every cheap raw variant of each URL (with and without `www.`, with and without the trailing slash). That query is a candidate net, not the decision. Chunk above about 200 values per `IN` list. Email and MobilePhone are selected on purpose: they are the pipeline's only look at what the CRM already holds.
- **Normalize both sides.** Profile URL: lowercase, drop the scheme, `www.`, any locale subdomain, trailing slash and query string, percent-decode, keep what follows `/in/`. A stored value of the internal member-id form (a long opaque string starting `ACw`) lives in its own key space: compare it only to other ids, and treat "no slug match" on it as unknown. Email: lowercase and trimmed, with generic mailboxes (**[contact, info, hello, sales, admin, noreply…]**) excluded from matching, because several people share them. Mobile: punctuation stripped, then E.164 using the Account's billing country. Name: lowercase, accents stripped, apostrophes, hyphens and spaces collapsed, honorifics removed.
- **Level A, automatic reuse, global scope**: profile slug, internal member id, nominative email, or an E.164 mobile present on **exactly one** existing Contact (a number shared by several records is a switchboard or a bad import). **Level B, reuse and name it in the report**: first name plus last name within the same Account. **Level C, never create and never merge, flag for a person**: one last name is a prefix or an initial of the other with the same first name; the email's local part spells the incoming name on a different domain; or the same name sits on a different Account. When two different existing Contacts match on two different keys, that is a duplicate already in the CRM: flag both Ids and create nothing. **A false match is worse than a duplicate**: a duplicate is visible and mergeable, a wrong match quietly writes one person's phone onto another person's record.
- <tool:salesforce_record> — op=bulk_create on Contact for what is left, at most 200 per call, with **only** FirstName, LastName, Title, AccountId, Email, MobilePhone, the profile URL in canonical form (www.linkedin.com/in/ plus the slug) and **[your source flag field]** set to **[a newly-found value]** on creates only. **MobilePhone, never Phone**: enriched numbers are personal mobiles and Phone is the switchboard. Never write the table's tier or status to any Salesforce field.
- **Read `results` on every bulk response**: a record can fail while the call succeeds. Whether the org's own duplicate rule fires at all is its Setup, not something this run controls. A record that comes back `DUPLICATES_DETECTED` is looked up by name and Account and moved to the reuse list, without the newly-found flag.
- <tool:salesforce_record> — op=bulk_update on matched Contacts, writing only the profile URL field, and only where it is empty or not canonical. That is what stops the same near-miss from recurring on the next run.
- <tool:data_write> — one batch back to the table: the Salesforce Id, `pushed` or `duplicate_suspected`, the key that matched, any candidate Id, and whether the Contact already had an email and a mobile. A batched write deduplicates on the table's declared key, not on the row id, so pass `key` and the key column on every row.
- **Settle the enrichment scope once**, before either lookup: email, mobile, or both. With a person present, ask, even when the request already mentioned enrichment. Unattended, take the scope the trigger states, or default to both, since an unattended run exists to fill contact details. A cohort out of scope is skipped entirely, and the run records how the scope was set: asked, given by the trigger, or defaulted.

## 6. Find the missing emails
- **Build the cohort from Salesforce's state, not from the row.** A freshly sourced row is empty by construction, so it proves nothing. Only Contacts with no Email on file enter this cohort; judging need from the empty row is how verified CRM data gets overwritten with a guess.
- <tool:dropcontact_enrich> — the whole cohort in one batch (up to 250): first name, last name, company, `linkedin`, and the row key inside `custom_fields`, which comes back unchanged, since result order is not guaranteed. Pass `language` "en" unless your contacts are French, which is the default.
- <tool:dropcontact_result> — first poll around 30 seconds after submitting, then every 20 to 30 seconds until `done`. A nominative email on the company's own domain beats a generic mailbox; anything marked invalid counts as no email.

## 7. Find the missing mobiles
- Only Contacts with no MobilePhone on file enter this cohort.
- <tool:lusha_search_and_enrich> — batches of up to 100 with `reveal` set to phones, each contact identified by `linkedinUrl`, `firstName`, `lastName` and `companyName`, with `clientReferenceId` set to the row key. **Never by email alone**: a bare-email lookup has returned a completely unrelated person at another company, and billed for it. A different current company in the result is not a wrong match on its own, because provider data can be fresher than a stale CRM record; a different number against a source you trust is. Read the per-result `error` (`NOT_FOUND`, `COMPLIANCE_RESTRICTED`, `ENRICH_FAILED`) inside a successful response, and read `billing.creditsCharged` before repeating a large reveal. Take the mobile with the highest confidence.
- <tool:kaspr_enrich_linkedin> — only for contacts the batch did not resolve, one call each: `linkedin_id` set to the profile URL, `with_phone` true, `data_to_get` set to `phone`. **Field names come from the provider's own enum**, and an unknown one answers 500, which looks exactly like an outage. A real server error gets one retry in the same session, then the row records "backup provider unavailable, retry needed". A normal answer with nothing on file is recorded as a confirmed absence and never re-queried.
- Before the backup runs, sweep this company's earlier rows still flagged "retry needed", on a mobile or on an email, and give each one fresh attempt with the matching backup call. A server error from the backup provider is usually transient, and because every later run on the company retries the flag, an outage heals itself instead of leaving the row stale.

## 8. Write back once and stamp the Account
- **Close the email gaps once both batches are back**, which is what lets steps 6 and 7 run without waiting on each other. Contacts Dropcontact left without a usable email go to <tool:lusha_search_and_enrich> in one residual batch, identified exactly as in step 7, with `reveal` set to emails. What is still missing then gets the per-contact <tool:kaspr_enrich_linkedin> backup with `data_to_get` set to `workEmail` and `directEmail`, under step 7's retry rule. The email's source is recorded as `dropcontact`, `lusha` or `kaspr`.
- <tool:salesforce_record> — one op=bulk_update on Contact, Email only where the Contact had none and MobilePhone only where it had none. **This call fills empty fields and never replaces populated ones**: the cohort rule already kept those contacts out, and this second gate stays because this is the call that destroys data. Never write a blank, never Phone, never the newly-found flag.
- <tool:data_write> — one batch: email and its source, mobile and its source (`salesforce` for a value that was already on file and never looked up), and `enriched`, or `enrich_failed` where nothing was found.
- <tool:salesforce_record> — op=update on the Account's **[sourcing status text field]**, with a plain-English recap for the reps who open the record. **Overwrite, never append**, and no company name or date in it: the record already names the company, and a last-sourced date field, if you keep one, holds the time. If the field is a short text area (255 characters is common), Salesforce rejects an over-long value with `STRING_TOO_LONG` rather than cutting it, so truncate client-side a few characters under the limit and add an ellipsis (250 for a 255-character field); on a failure line, cut the reason and keep the prefix.
- Line 1 leads with Contacts actually **created**, then matches, never summed and never with pipeline words like "reused": `<C> contacts added · <M> already in Salesforce`. Line 2, only when enrichment ran: `Email + phone: <n> · Email only: <n> · No contact details: <n>`. **Never print a zero segment.** Nothing created but matches found: `0 new contacts — <M> profiles found, all already in Salesforce`.
- **Zero added always says why**, picked from what happened: no head-office profile matching your profile; profiles found but none attached to head office; profiles found but all out of profile; entity not resolved (showcase page, no indexed staff); no profile carried a profile URL; Salesforce push failed with a short reason; or, when genuinely unknown, "cause unknown, see the run". A rep reading the record has to be able to tell whether pressing the button again is worth it.
- Only with an Account Id the run actually holds, never a name-matched guess. A failed stamp is reported and does not fail the run.

## Output
Report: people found per pool and whether each was fully paginated, validated and rejected with the reasons, Contacts created and matched (and on which key), suspected duplicates with both Ids, rows skipped for no profile URL, emails and mobiles found per provider, rows still waiting on a retry, and the recap stamped on the Account.