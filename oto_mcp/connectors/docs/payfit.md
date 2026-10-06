## prerequisite — your payfit api key

log in to PayFit **as a company admin**, then **Integrations → API** ([app.payfit.com/integrations/hub/api](https://app.payfit.com/integrations/hub/api)) → "Create a key": give it an explicit label and tick **read scopes only**, then copy it — it is not shown again afterwards. paste it into your oto connector keys under `payfit`.
- **the connector only reads**: no write is wired to PayFit.
- **the key decides what you will see**: oto serves everything the API exposes for reading, but a field that a missing scope does not return does not exist for anyone. for full HR and finance steering: `collaborators:read`, `collaborators:management:read`, `collaborators:contracts:read`, `collaborators:personal:read`, `collaborators:legal-identity:read`, `contracts:read`, `contracts:payslips:read`, `time:read`, `accounting:read`, `health-insurance:read`, `collaborators:meal-vouchers:read`
- **sensitive** scopes, to be ticked only if you need them: `collaborators:social-security:read` (NIR), `collaborators:bank-info:read` (IBAN), `payment-files:read` (payment file)
- **do not tick any write scope** (`collaborators:write`, `collaborators:contracts:write`, `time:write`, `health-insurance:write`): the connector does not use them. on an existing key that carries some, they are useless and can be removed on the PayFit side
- the key opens **only your company**; oto finds its identifier on its own (by introspection), you have no identifier to enter
- whether API access is included or paid depending on the PayFit plan is not publicly documented; partner access (OAuth) is another route, by application
- BYO only: no shared oto key

## setup — a group of companies, one key per company

a PayFit key opens **only one company**, and the API has no group view: two companies = two independent keys. create a key in each PayFit company, then set each one as a company of the connector (section "multiple companies").
- ⚠️ **consolidation is done on your side, not at PayFit**: a total returned without looping over each `_account` would be the figure of a single company presented as the group's

## usage — from the directory to financial steering

start with `payfit_company()`: its `country` says whether the French variants apply (FR contracts, meal vouchers, worked time, health insurance).
- "who works here, who is their manager?" → `payfit_collaborator()` (next page: `cursor=<next_cursor>`)
- "what position, what contract type, what collective agreement, days-based package?" → `payfit_contract(fr=True)` (`natureContratDsn`: 01 CDI, 02 CDD…; `workingTimeModality`: `forfait_jours`…)
- "who is absent next week?" → `payfit_absence(begin_date="YYYY-MM-DD", end_date="YYYY-MM-DD")`, then link `contractId` to a collaborator's `contracts`
- "how much did January's payroll cost us, and on what?" → `payfit_payroll(op="accounting", date="202601")`: one row per entry, with account, label, debit, credit, employee and analytic codes. **this is the only structured numeric data in the API** — payroll cost, charges and benefits in kind are read there by account number (641x, 645x, 6417x)
- "is the month closed?" → `payfit_payroll(date="202601")` (op `status` by default) **before** using any figures
- "the journal for my accounting firm" → `payfit_payroll(op="accounting_export", date="202601")`; "the payment file" → `op="payment_file"` — **documents locked by default**, see the note on personal data
- "someone's payslips" → `payfit_payslip(collaborator_id=…)` for the list, then `op="download"` with the three identifiers of the row: the PDF text comes back readable, the original PDF as `raw_url` (**locked by default**, like any document)
- "how many overtime hours, and how many euros?" → `payfit_payslip(op="overtime", collaborator_id=…, date="202601")`: only the overtime / complementary / increased-rate hours lines of the payslip, read server side (the payslip does not go out). the numbers are returned in the line's order, with no role assigned: check the reading against a payslip before totalling, and never add an `allegement` line (contribution reduction) to the paid amount. for a company, loop over `payfit_collaborator()`
- "how many hours worked?" → `payfit_worked_time(date="202601")` · "meal vouchers" → `payfit_meal_voucher(date="202601")`
- "health insurance and provident cover" → `payfit_insurance()` for the company's contracts, `kind="provident"` for provident cover
- **the month is written `YYYYMM`** (`202601`), never `2026-01`: it is the only form PayFit accepts

## note — no writes in payfit

the connector **never writes** to PayFit, whatever the argument. the write ops still exist in their tools, but each returns the named refusal `payfit_write_not_wired`, which says what the call would have done — nothing is sent:
- `payfit_collaborator(op="create")`, `payfit_contract(op="create")`
- `payfit_absence(op="create")` and `op="cancel"`
- `payfit_insurance(op="affiliate")` and `op="regularize"`

there is neither an org switch nor an activation by an administrator: the capability does not exist in the connector. a hire, a contract, an absence or an affiliation is done in PayFit itself.

## note — what the payfit api does not have

these questions come up often and have **no endpoint** — oto will not fabricate them, and an invented answer would be wrong:
- **a payslip's lines** (gross, net, contribution by contribution): only the PDF and its metadata exist. the only amounts a program can read are the accounting entries — except overtime, which `op="overtime"` READS from the PDF. to get them as accounting data, isolate them on a dedicated sub-account in PayFit's accounting settings (the API cannot do it): the new account will appear as is in `op="accounting"`
- **year-to-date totals**, an aggregated "employer cost", charges as a resource: to be rebuilt from the entries, month by month
- **the DSN**: FR contracts carry fields *coded according to* the DSN standard (nature, status, IDCC, termination reason), but there is no DSN filing or retrieval
- **schedules and clock-ins**: only a monthly aggregate per contract exists (`payfit_worked_time`)
- **leave balances and counters** (paid leave accrued/taken, RTT remaining): nothing. do not deduce them from a list of absences
- **the history of amendments** and any change to an existing contract: a contract is read as it is today
- **expense reports**, benefits in kind as an object
- **tax documents**: those that exist (`payfit_document`) are **British**; on a French company the list is empty, and that is the right answer

## note — personal data: what is masked, and how to lift it

the connector no longer removes anything in a hard-coded way: it serves what the key allows, and protection is an **org policy**, editable.
- **masked by default** (server default): NIR (and NTT), IBAN/BIC, and the reason for an absence (`absence_type`) — sickness, work accident, maternity are health data. a `••••` is a masked value, not missing data
- `absence_category` always stays readable: `ordinary_leave` (paid leave, RTT, rest, unpaid, remote work, school) or `restricted` for everything else — enough to plan a workload without reading a reason
- **served unmasked**: pay in accounting entries, payslips, employer cost, charges, full contracts (termination, trial period, collective agreement, status), worked time, health insurance and provident cover, meal vouchers, e-mail, phone, address, date of birth, gender, nationality, seniority, manager
- **lifting a mask**: an org administrator does so field by field, by **naming** it: `oto_org_settings domain=field_filters op=set service=payfit rules=[] unmask=["iban"]` (accepted names: those of the server default, under their output name). the org policy is **added to** the default: without `unmask`, nothing comes out in clear, and `rules: []` alone is refused (`floor_lift_must_be_explicit`). the response lists the fields now in clear under `unmasked`. ⚠️ *clearing* the policy (`rules: null`) restores the server default as is
- **the `redaction` notice states the EFFECTIVE policy**: every response that may carry a sensitive field names those that the org's policy (or the server default) masks, and those it leaves IN CLEAR. an org that has lifted a mask receives the data in clear, and the notice says so — it never announces a mask that does not apply
- **documents are locked**: a field filter cannot see inside a file, yet the PDF payslip carries the NIR, the payment file each employee's IBAN, the accounting export names and amounts per person (and the British tax documents the insurance number). these four documents are served **only if the org has opened them**, by a separate consent: `oto_org_settings domain=field_filters op=set service=payfit rules=[] documents=true`. this consent **lifts no mask** from the JSON responses. otherwise the call is refused, saying so, and the refusal is not a bug. if the policy cannot be read, the document is refused too. `payfit_payslip(op="overtime")` is not locked: it only returns the overtime lines, never the payslip. their numbers (`numbers`, `rates`) are filtered by the policy like any data; the raw line (`line`), a verbatim excerpt of the payslip, follows the documents lock (`line_withheld` says why otherwise)
- **data** (accounting entries as JSON, payroll status, payslip list) is never locked: the policy filters it field by field
- derived from the public documentation and OpenAPI spec (read on 17/09/2026), never exercised with a real key
